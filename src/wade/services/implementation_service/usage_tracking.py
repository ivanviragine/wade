"""Token usage block management for PR and issue bodies."""

from __future__ import annotations

import re

from crossby.models.ai import TokenUsage

from wade.utils.markdown import (
    append_session_to_body,
    extract_marker_block,
    remove_marker_block,
)
from wade.utils.token_usage_markdown import resolve_token_usage_totals

__all__ = [
    "IMPL_USAGE_MARKER_END",
    "IMPL_USAGE_MARKER_START",
    "REVIEW_USAGE_MARKER_END",
    "REVIEW_USAGE_MARKER_START",
    "_append_usage_entry",
    "_build_session_usage_table",
    "_count_sessions",
    "_enrich_body_with_usage",
    "_resolve_usage_totals",
    "_strip_impl_usage_block",
    "_strip_review_usage_block",
    "_usage_has_token_metrics",
    "append_impl_usage_entry",
    "append_review_usage_entry",
    "build_impl_usage_block",
    "build_review_usage_block",
]

# --- Implementation usage block markers ---
IMPL_USAGE_MARKER_START = "<!-- wade:impl-usage:start -->"
IMPL_USAGE_MARKER_END = "<!-- wade:impl-usage:end -->"

# --- Review usage block markers ---
REVIEW_USAGE_MARKER_START = "<!-- wade:review-usage:start -->"
REVIEW_USAGE_MARKER_END = "<!-- wade:review-usage:end -->"


def _usage_has_token_metrics(usage: TokenUsage | None) -> bool:
    """Return True when usage contains aggregate or per-model token metrics."""
    return bool(
        usage
        and (
            usage.total_tokens is not None
            or usage.input_tokens is not None
            or usage.output_tokens is not None
            or usage.cached_tokens is not None
            or (usage.premium_requests or 0) > 0
            or usage.model_breakdown
        )
    )


def _resolve_usage_totals(
    token_usage: TokenUsage | None,
) -> tuple[int | None, int | None, int | None, int | None]:
    """Resolve aggregate token counts, deriving them from breakdown rows when needed."""
    if token_usage is None:
        return None, None, None, None

    return resolve_token_usage_totals(
        total_tokens=token_usage.total_tokens,
        input_tokens=token_usage.input_tokens,
        output_tokens=token_usage.output_tokens,
        cached_tokens=token_usage.cached_tokens,
        model_breakdown=token_usage.model_breakdown,
    )


def _enrich_body_with_usage(
    body: str,
    ai_tool: str,
    model: str | None,
    usage: TokenUsage | None,
    has_tokens: bool,
    has_session: bool,
) -> str:
    """Append implementation usage and session entries to a markdown body."""
    result = body
    if has_tokens:
        assert usage is not None
        result = append_impl_usage_entry(result, ai_tool=ai_tool, model=model, token_usage=usage)
    if has_session:
        assert usage is not None and usage.session_id is not None
        result = append_session_to_body(
            result,
            phase="Implement",
            ai_tool=ai_tool,
            session_id=usage.session_id,
        )
    return result


def _build_session_usage_table(
    ai_tool: str | None = None,
    model: str | None = None,
    token_usage: TokenUsage | None = None,
) -> str:
    """Build a single-session markdown usage table (no markers or headings).

    Generates the table rows for one session, used by both impl and review
    usage block builders.
    """
    from crossby.ai_tools.transcript import format_count

    breakdown = token_usage.model_breakdown if token_usage else []
    multi = len(breakdown) > 1
    total_tokens, input_tokens, output_tokens, cached_tokens = _resolve_usage_totals(token_usage)
    has_tokens = _usage_has_token_metrics(token_usage)

    lines: list[str] = []

    if multi:
        names = [row.model for row in breakdown]
        n = len(names)
        header = "| Metric | Total | " + " | ".join(f"`{m}`" for m in names) + " |"
        sep = "| " + " | ".join(["---"] * (2 + n)) + " |"
        empty = " |" * n

        lines.extend([header, sep])

        if ai_tool:
            lines.append(f"| Tool | `{ai_tool}` |{empty}")

        if has_tokens:

            def per(attr: str) -> str:
                return " | ".join(f"**{format_count(getattr(r, attr))}**" for r in breakdown)

            per_total = " | ".join(
                f"**{format_count((r.input_tokens or 0) + (r.output_tokens or 0) + (r.cached_tokens or 0))}**"  # noqa: E501
                for r in breakdown
            )
            if total_tokens is not None:
                lines.append(f"| Total tokens | **{format_count(total_tokens)}** | {per_total} |")
            if input_tokens is not None:
                inp_total = format_count(input_tokens)
                lines.append(f"| Input tokens | **{inp_total}** | {per('input_tokens')} |")
            if output_tokens is not None:
                out_total = format_count(output_tokens)
                lines.append(f"| Output tokens | **{out_total}** | {per('output_tokens')} |")
            if cached_tokens is not None:
                cac_total = format_count(cached_tokens)
                lines.append(f"| Cached tokens | **{cac_total}** | {per('cached_tokens')} |")
        else:
            lines.append(f"| Total tokens | *unavailable* |{empty}")

        if token_usage and token_usage.premium_requests and token_usage.premium_requests > 0:
            per_prem = " | ".join(
                f"**{r.premium_requests}**" if r.premium_requests else "" for r in breakdown
            )
            lines.append(
                f"| Premium requests (est.) | **{token_usage.premium_requests}** | {per_prem} |"
            )

    else:
        lines.extend(["| Metric | Value |", "| --- | --- |"])

        if ai_tool:
            lines.append(f"| Tool | `{ai_tool}` |")
        if model:
            lines.append(f"| Model | `{model}` |")

        if has_tokens:
            if total_tokens is not None:
                lines.append(f"| Total tokens | **{format_count(total_tokens)}** |")
            if input_tokens is not None:
                lines.append(f"| Input tokens | **{format_count(input_tokens)}** |")
            if output_tokens is not None:
                lines.append(f"| Output tokens | **{format_count(output_tokens)}** |")
            if cached_tokens is not None:
                lines.append(f"| Cached tokens | **{format_count(cached_tokens)}** |")
        else:
            lines.append("| Total tokens | *unavailable* |")

        if token_usage and token_usage.premium_requests and token_usage.premium_requests > 0:
            lines.append(f"| Premium requests (est.) | **{token_usage.premium_requests}** |")

    return "\n".join(lines)


def _count_sessions(block_content: str) -> int:
    """Count ``### Session N`` occurrences in a marker block's inner content."""
    return len(re.findall(r"^### Session \d+", block_content, re.MULTILINE))


def _append_usage_entry(
    body: str,
    ai_tool: str | None,
    model: str | None,
    token_usage: TokenUsage | None,
    start_marker: str,
    end_marker: str,
    heading: str,
) -> str:
    """Append a new session entry to a usage marker block.

    If the block doesn't exist, creates a fresh block with ``### Session 1``.
    If the block exists with N sessions, appends ``### Session N+1``.
    """
    existing = extract_marker_block(body, start_marker, end_marker)
    table = _build_session_usage_table(ai_tool=ai_tool, model=model, token_usage=token_usage)

    if existing is None:
        # Fresh block
        lines = [
            start_marker,
            "",
            f"## {heading}",
            "",
            "### Session 1",
            "",
            table,
            "",
            end_marker,
        ]
        block = "\n".join(lines)
        stripped = body.rstrip("\n")
        return stripped + "\n\n" + block + "\n" if stripped else block + "\n"

    # Existing block — count sessions and append
    n = _count_sessions(existing)

    if n == 0 and existing.strip():
        # Old format (no ### Session headings) — wrap old content as Session 1
        new_inner = f"### Session 1\n\n{existing.strip()}\n\n### Session 2\n\n{table}"
    else:
        new_session = f"### Session {n + 1}\n\n{table}"
        new_inner = existing.rstrip("\n") + "\n\n" + new_session

    # Rebuild: remove old block, construct new one with appended session
    cleaned = remove_marker_block(body, start_marker, end_marker)
    new_block = f"{start_marker}\n\n{new_inner}\n\n{end_marker}"
    stripped = cleaned.rstrip("\n")
    return stripped + "\n\n" + new_block + "\n" if stripped else new_block + "\n"


def append_impl_usage_entry(
    body: str,
    ai_tool: str | None = None,
    model: str | None = None,
    token_usage: TokenUsage | None = None,
) -> str:
    """Append an implementation usage session entry to the body."""
    return _append_usage_entry(
        body,
        ai_tool=ai_tool,
        model=model,
        token_usage=token_usage,
        start_marker=IMPL_USAGE_MARKER_START,
        end_marker=IMPL_USAGE_MARKER_END,
        heading="Token Usage (Implementation)",
    )


def append_review_usage_entry(
    body: str,
    ai_tool: str | None = None,
    model: str | None = None,
    token_usage: TokenUsage | None = None,
) -> str:
    """Append a review usage session entry to the body."""
    return _append_usage_entry(
        body,
        ai_tool=ai_tool,
        model=model,
        token_usage=token_usage,
        start_marker=REVIEW_USAGE_MARKER_START,
        end_marker=REVIEW_USAGE_MARKER_END,
        heading="Token Usage (Review)",
    )


def build_impl_usage_block(
    ai_tool: str | None = None,
    model: str | None = None,
    token_usage: TokenUsage | None = None,
) -> str:
    """Build the ## Token Usage (Implementation) section for PR body.

    Wraps ``_build_session_usage_table`` with markers and a ``### Session 1``
    header.
    """
    table = _build_session_usage_table(ai_tool=ai_tool, model=model, token_usage=token_usage)
    lines = [
        IMPL_USAGE_MARKER_START,
        "",
        "## Token Usage (Implementation)",
        "",
        "### Session 1",
        "",
        table,
        "",
        IMPL_USAGE_MARKER_END,
    ]
    return "\n".join(lines)


def _strip_impl_usage_block(body: str) -> str:
    """Remove existing implementation usage block from body (idempotent)."""
    return remove_marker_block(body, IMPL_USAGE_MARKER_START, IMPL_USAGE_MARKER_END)


def build_review_usage_block(
    ai_tool: str | None = None,
    model: str | None = None,
    token_usage: TokenUsage | None = None,
) -> str:
    """Build the ## Token Usage (Review) section for PR/issue body.

    Wraps ``_build_session_usage_table`` with review markers and a
    ``### Session 1`` header.
    """
    table = _build_session_usage_table(ai_tool=ai_tool, model=model, token_usage=token_usage)
    lines = [
        REVIEW_USAGE_MARKER_START,
        "",
        "## Token Usage (Review)",
        "",
        "### Session 1",
        "",
        table,
        "",
        REVIEW_USAGE_MARKER_END,
    ]
    return "\n".join(lines)


def _strip_review_usage_block(body: str) -> str:
    """Remove existing review usage block from body (idempotent)."""
    return remove_marker_block(body, REVIEW_USAGE_MARKER_START, REVIEW_USAGE_MARKER_END)
