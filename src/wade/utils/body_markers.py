"""Marker-bounded PR/issue body updates (issue #357, A4/A5).

wade writes several sections into a PR or issue body (plan, usage tables,
session list, summary, dependencies). Each lives inside an HTML-comment marker
pair so wade can rewrite *only its own* section and leave any concurrent edit
outside the markers untouched.

Two guarantees this module centralizes:

- **A4 — re-read before write.** :func:`update_body_preserving_markers`
  re-reads the current remote body immediately before applying a marker-scoped
  transform and writing it back, so a concurrent edit made between an earlier
  read and this write is not clobbered.
- **A5 — size budget.** GitHub rejects bodies over ``GITHUB_BODY_MAX`` chars.
  :func:`enforce_body_budget` trims the oldest (least valuable) usage sessions
  when a body would exceed the cap, emitting a **user-visible** warning that
  includes what was dropped — never a silent truncation or a silent API 422.

Designed for reuse by #358's ``.ratings.yml`` counter fix — the same
read → marker-scoped transform → budget → write shape applies there.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import structlog

from wade.utils.markdown import remove_marker_block

log = structlog.get_logger(__name__)

# GitHub rejects issue/PR bodies longer than this many characters (HTTP 422).
GITHUB_BODY_MAX = 65_536

# Usage marker blocks whose ``### Session N`` rows may be trimmed to fit budget,
# oldest first. Imported lazily to avoid a circular import with usage_tracking.
_SESSION_HEADING_RE = re.compile(r"(?=^### Session \d+)", re.MULTILINE)


def build_marked_block(start_marker: str, end_marker: str, inner: str) -> str:
    """Wrap *inner* content in a marker pair (empty inner → bare markers)."""
    inner = inner.strip("\n")
    if not inner:
        return f"{start_marker}\n{end_marker}"
    return f"{start_marker}\n\n{inner}\n\n{end_marker}"


def upsert_marked_block(body: str, start_marker: str, end_marker: str, inner: str) -> str:
    """Insert or replace a marker block, preserving all other content verbatim.

    Any existing block for this marker pair is removed and the new block is
    appended at the end. Content outside the markers — including a concurrent
    edit — is left exactly as-is. An empty *inner* removes the block entirely.
    """
    cleaned = remove_marker_block(body, start_marker, end_marker)
    if not inner.strip():
        return cleaned
    block = build_marked_block(start_marker, end_marker, inner)
    stripped = cleaned.rstrip("\n")
    return f"{stripped}\n\n{block}\n" if stripped else f"{block}\n"


def _trim_oldest_session(body: str, start_marker: str, end_marker: str) -> tuple[str, str | None]:
    """Drop the oldest ``### Session N`` entry from one usage block.

    Returns (new_body, dropped_text). ``dropped_text`` is None when the block is
    absent or holds a single session (never emptied — the newest is kept).
    """
    from wade.utils.markdown import extract_marker_block

    inner = extract_marker_block(body, start_marker, end_marker)
    if inner is None:
        return body, None
    parts = _SESSION_HEADING_RE.split(inner)
    # parts[0] is the block heading/preamble before the first session.
    preamble = parts[0]
    sessions = parts[1:]
    if len(sessions) <= 1:
        return body, None  # keep at least the newest session
    dropped = sessions[0].strip()
    kept_inner = (preamble + "".join(sessions[1:])).strip("\n")
    new_body = upsert_marked_block(body, start_marker, end_marker, kept_inner)
    return new_body, dropped


def enforce_body_budget(
    body: str,
    *,
    warn: Callable[[str], None] | None = None,
    label: str = "body",
) -> str:
    """Trim the oldest usage sessions until *body* fits GitHub's size cap.

    Drops sessions oldest-first from the implementation- then review-usage
    blocks. Each drop emits a user-visible warning (via *warn*) that includes
    the dropped session's content, so the data is never lost silently. If the
    body still exceeds the cap after every trimmable session is gone, a final
    warning is emitted and the (still-oversized) body is returned — the caller's
    write may then fail loudly rather than wade truncating arbitrary content.
    """
    from wade.services.implementation_service.usage_tracking import (
        IMPL_USAGE_MARKER_END,
        IMPL_USAGE_MARKER_START,
        REVIEW_USAGE_MARKER_END,
        REVIEW_USAGE_MARKER_START,
    )

    if len(body) <= GITHUB_BODY_MAX:
        return body

    blocks = (
        (IMPL_USAGE_MARKER_START, IMPL_USAGE_MARKER_END),
        (REVIEW_USAGE_MARKER_START, REVIEW_USAGE_MARKER_END),
    )
    for start, end in blocks:
        while len(body) > GITHUB_BODY_MAX:
            trimmed, dropped = _trim_oldest_session(body, start, end)
            if dropped is None:
                break  # nothing left to trim in this block
            body = trimmed
            msg = (
                f"{label} exceeded GitHub's {GITHUB_BODY_MAX}-char limit — dropped the "
                f"oldest usage session to fit. Dropped content:\n{dropped}"
            )
            log.warning("body.budget_trim", label=label, dropped_len=len(dropped))
            if warn is not None:
                warn(msg)
        if len(body) <= GITHUB_BODY_MAX:
            return body

    if len(body) > GITHUB_BODY_MAX:
        over = len(body) - GITHUB_BODY_MAX
        msg = (
            f"{label} is {over} chars over GitHub's {GITHUB_BODY_MAX}-char limit even after "
            "trimming usage history — the update may be rejected. Shorten the body manually."
        )
        log.warning("body.budget_exceeded", label=label, over=over)
        if warn is not None:
            warn(msg)
    return body


def update_body_preserving_markers(
    read_body: Callable[[], str | None],
    write_body: Callable[[str], bool],
    transform: Callable[[str], str],
    *,
    warn: Callable[[str], None] | None = None,
    label: str = "body",
) -> bool:
    """Re-read → marker-scoped transform → budget → write.

    *read_body* is called immediately before writing to minimize the window in
    which a concurrent edit could be clobbered. *transform* must only rewrite
    content inside wade's own markers. Returns ``write_body``'s result, or
    ``False`` when the body could not be read.
    """
    current = read_body()
    if current is None:
        return False
    new_body = transform(current)
    new_body = enforce_body_budget(new_body, warn=warn, label=label)
    return write_body(new_body)
