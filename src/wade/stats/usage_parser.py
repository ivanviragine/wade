"""Parse WADE's own token-usage blocks back out of PR bodies (history backfill).

WADE has long written per-session usage tables into PR bodies between
``wade:impl-usage`` / ``wade:review-usage`` markers (see
``services/implementation_service/usage_tracking.py``). Reading them back lets
``wade stats sync`` reconstruct history that predates the local database.

Only WADE's rendered shapes are recognized — single-model tables
(``| Metric | Value |``), multi-model tables (``| Metric | Total | `m1` | … |``)
and the legacy pre-``### Session N`` layout. Anything else yields no sessions.
The marker strings are duplicated here (this layer cannot import services); a
unit test pins them to the service constants.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from wade.models.stats import ModelUsageRow

IMPL_USAGE_MARKERS = ("<!-- wade:impl-usage:start -->", "<!-- wade:impl-usage:end -->")
REVIEW_USAGE_MARKERS = ("<!-- wade:review-usage:start -->", "<!-- wade:review-usage:end -->")
# Planning usage lives in *issue* bodies (``task_service.build_plan_summary_block``).
PLAN_USAGE_MARKERS = ("<!-- wade:plan-summary:start -->", "<!-- wade:plan-summary:end -->")

_SESSION_HEADING = re.compile(r"^###\s+Session\s+(\d+)\s*$", re.MULTILINE)


class ParsedUsageSession(BaseModel):
    """One ``### Session N`` table from a usage block."""

    index: int
    ai_tool: str | None = None
    model: str | None = None
    total_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    premium_requests: int | None = None
    per_issue_estimate: int | None = None  # planning blocks: "This issue (est.)"
    models: list[ModelUsageRow] = Field(default_factory=list)

    @property
    def has_tokens(self) -> bool:
        return any(
            v is not None
            for v in (self.total_tokens, self.input_tokens, self.output_tokens, self.cached_tokens)
        )


def _extract_block(body: str, markers: tuple[str, str]) -> str | None:
    start = body.find(markers[0])
    if start < 0:
        return None
    end = body.find(markers[1], start + len(markers[0]))
    if end < 0:
        return None
    return body[start + len(markers[0]) : end]


def _cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _clean(cell: str) -> str:
    return cell.strip().strip("*").strip("`").strip()


def _int(cell: str) -> int | None:
    digits = re.sub(r"[^\d]", "", _clean(cell))
    return int(digits) if digits else None


# Planning blocks list per-model rows as "| `model` | **1,000** in · **200** out · **0** cached |".
_PLAN_MODEL_ROW = re.compile(
    r"\*\*([\d,]+)\*\*\s*in\s*·\s*\*\*([\d,]+)\*\*\s*out(?:\s*·\s*\*\*([\d,]+)\*\*\s*cached)?"
)

_LABEL_FIELDS = {
    "total tokens": "total_tokens",
    "input tokens": "input_tokens",
    "output tokens": "output_tokens",
    "cached tokens": "cached_tokens",
    "premium requests (est.)": "premium_requests",
}


def _parse_table(index: int, text: str) -> ParsedUsageSession | None:
    rows = [_cells(ln) for ln in text.splitlines() if ln.strip().startswith("|")]
    rows = [r for r in rows if r and not all(set(c) <= {"-", ":", " "} for c in r)]
    if not rows or _clean(rows[0][0]).lower() != "metric":
        return None
    header, body = rows[0], rows[1:]
    multi = len(header) >= 3 and _clean(header[1]).lower() == "total"
    model_names = [_clean(c) for c in header[2:]] if multi else []
    per_model: dict[str, dict[str, int]] = {m: {} for m in model_names}
    session = ParsedUsageSession(index=index)
    for row in body:
        if len(row) < 2:
            continue
        label = _clean(row[0]).lower()
        if label == "tool":
            session.ai_tool = _clean(row[1]) or None
        elif label == "model":
            session.model = _clean(row[1]) or None
        elif label == "this issue (est.)":
            session.per_issue_estimate = _int(row[1])
        elif row[0].strip().startswith("`") and (m := _PLAN_MODEL_ROW.search(row[1])):
            session.models.append(
                ModelUsageRow(
                    model=_clean(row[0]),
                    input_tokens=int(m.group(1).replace(",", "")),
                    output_tokens=int(m.group(2).replace(",", "")),
                    cached_tokens=int((m.group(3) or "0").replace(",", "")),
                )
            )
        elif label in _LABEL_FIELDS:
            field = _LABEL_FIELDS[label]
            setattr(session, field, _int(row[1]))
            for name, cell in zip(model_names, row[2:], strict=False):
                value = _int(cell)
                if value is not None:
                    per_model[name][field] = value
    for name in model_names:
        vals = per_model[name]
        session.models.append(
            ModelUsageRow(
                model=name,
                input_tokens=vals.get("input_tokens", 0),
                output_tokens=vals.get("output_tokens", 0),
                cached_tokens=vals.get("cached_tokens", 0),
                premium_requests=vals.get("premium_requests", 0),
            )
        )
    if session.total_tokens is None and any(
        v is not None for v in (session.input_tokens, session.output_tokens, session.cached_tokens)
    ):
        session.total_tokens = (
            (session.input_tokens or 0)
            + (session.output_tokens or 0)
            + (session.cached_tokens or 0)
        )
    return session


def parse_usage_block(body: str, markers: tuple[str, str]) -> list[ParsedUsageSession]:
    """Return every session table in the marked usage block of *body*."""
    block = _extract_block(body or "", markers)
    if block is None:
        return []
    headings = list(_SESSION_HEADING.finditer(block))
    sessions: list[ParsedUsageSession] = []
    if not headings:  # legacy single-session layout
        parsed = _parse_table(1, block)
        return [parsed] if parsed else []
    for i, heading in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(block)
        parsed = _parse_table(int(heading.group(1)), block[heading.end() : end])
        if parsed is not None:
            sessions.append(parsed)
    return sessions


def usage_fingerprint(
    kind: str,
    ai_tool: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_tokens: int | None,
    total_tokens: int | None,
) -> str:
    """Identity of one session's usage numbers, used to dedupe across sources.

    The same session can surface in a PR body, its issue body and the local
    database; all three render from one ``TokenUsage``, so these numbers agree.
    The model name is excluded (the bodies record the *effective* model, the
    local row the requested one).
    """
    parts = [kind, (ai_tool or "").lower()]
    parts += ["" if v is None else str(v) for v in (input_tokens, output_tokens, cached_tokens)]
    parts.append("" if total_tokens is None else str(total_tokens))
    return "|".join(parts)


def has_wade_markers(body: str) -> bool:
    return "<!-- wade:" in (body or "")
