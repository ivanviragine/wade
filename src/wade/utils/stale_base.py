"""Stale-base marker under a worktree's ``.wade/`` directory (#407).

Records that startup ``catchup`` could **not** advance the branch onto its base, so the
in-session ``SessionStart`` context injection
(:func:`wade.hooks.policies.session_start_context`) can surface a loud,
compaction-surviving "N commits behind" warning the agent actually reads — unlike the
startup ``console.warn``, which never reaches the agent's in-session context.

The marker is a single-line file ``.wade/stale_base`` holding ``"<count> <reason>"`` where
``reason`` is a short token (:data:`REASON_UNTRACKED_CONFLICT` …) naming *why* catchup did
not advance. It is written by the post-catchup check in ``implementation_service.core`` and
cleared once a later ``sync``/``catchup`` reaches "up to date" (``sync._merge_base``).

Pure-stdlib leaf (only ``structlog``) so the lean ``wade-hook`` entry point can read it
without the crossby/CLI cold-start cost — and :func:`read_stale_base` touches nothing that
prints to stdout, preserving that entry's decision-JSON contract (the #349 gotcha). It lives
here rather than in the (heavy-``__init__``) implementation-service package precisely so the
read path stays import-light. Sibling of :mod:`wade.utils.markers`; kept separate because
that module's markers are zero-byte presence flags while this one carries a small payload.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import structlog

logger = structlog.get_logger()

__all__ = [
    "REASON_MERGE_CONFLICT",
    "REASON_SKIP_WORKTREE",
    "REASON_UNKNOWN",
    "REASON_UNTRACKED_CONFLICT",
    "StaleBaseMarker",
    "clear_stale_base",
    "read_stale_base",
    "write_stale_base",
]

_MARKER_NAME = "stale_base"

# Reason tokens — why startup catchup did not advance the branch onto its base.
REASON_UNTRACKED_CONFLICT = "untracked_conflict"
REASON_MERGE_CONFLICT = "merge_conflict"
REASON_SKIP_WORKTREE = "skip_worktree"
REASON_UNKNOWN = "unknown"

# Cap the stored reason so a pathological value can never bloat the marker (and, in turn,
# the capped SessionStart context budget).
_MAX_REASON_LEN = 40


class StaleBaseMarker(NamedTuple):
    """Parsed ``.wade/stale_base`` contents: commits-``behind`` count + ``reason`` token.

    The field is ``behind`` (not ``count``) so it does not shadow ``tuple.count``.
    """

    behind: int
    reason: str


def _marker_path(worktree_root: Path) -> Path:
    return worktree_root / ".wade" / _MARKER_NAME


def write_stale_base(worktree_root: Path, count: int, reason: str) -> bool:
    """Write ``"<count> <reason>"`` to ``.wade/stale_base``. Best-effort; returns success.

    Only a positive ``count`` is meaningful (the branch is behind); callers gate on that.
    The reason is reduced to a single, length-capped token so the marker stays one short
    line regardless of what a caller passes.
    """
    token = (reason or REASON_UNKNOWN).split()
    safe_reason = (token[0][:_MAX_REASON_LEN] if token else "") or REASON_UNKNOWN
    wade_dir = worktree_root / ".wade"
    try:
        wade_dir.mkdir(parents=True, exist_ok=True)
        _marker_path(worktree_root).write_text(f"{int(count)} {safe_reason}\n", encoding="utf-8")
        return True
    except OSError:
        logger.debug("stale_base.write_failed", path=str(worktree_root))
        return False


def read_stale_base(worktree_root: Path) -> StaleBaseMarker | None:
    """Return the parsed marker, or ``None`` when absent / empty / malformed / unreadable.

    Import-light and stdout-safe (a plain file read, never through the ``wade.git`` layer)
    so it is safe on the lean ``wade-hook`` ``SessionStart`` entry point (#349).
    """
    try:
        raw = _marker_path(worktree_root).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not raw:
        return None
    parts = raw.split(None, 1)
    try:
        count = int(parts[0])
    except (ValueError, IndexError):
        return None
    reason = parts[1].strip() if len(parts) > 1 and parts[1].strip() else REASON_UNKNOWN
    return StaleBaseMarker(behind=count, reason=reason)


def clear_stale_base(worktree_root: Path) -> None:
    """Remove ``.wade/stale_base`` if present. Best-effort; ignores errors."""
    try:
        _marker_path(worktree_root).unlink(missing_ok=True)
    except OSError:
        logger.debug("stale_base.clear_failed", path=str(worktree_root))
