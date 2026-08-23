"""Recovery sweep for knowledge votes stranded by a failed session handoff.

Lives beside ``knowledge_service`` rather than inside it so the vote lifecycle
itself stays console-free: this module owns only *when* the sweep runs and how
its outcome is reported. Both throwaway-worktree lifecycles (``wade plan`` and
standalone ``wade task deps``) call :func:`report_retained_vote_recovery` before
creating their next worktree.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from wade.models.config import ProjectConfig
from wade.ui.console import console

logger = structlog.get_logger()

# Printed next to every "worktree preserved for retry" error, because "retry"
# is only actionable if the user knows what performs it.
RETAINED_VOTE_RECOVERY_HINT = (
    "The votes stay in that worktree — the next `wade plan` or `wade task deps` "
    "hands them off automatically once access is restored."
)


def report_retained_vote_recovery(repo_root: Path, config: ProjectConfig) -> None:
    """Flush votes retained by earlier throwaway sessions and report the outcome.

    A no-op when knowledge is disabled or nothing was retained — the common
    case must stay silent. Never raises: recovering an *earlier* session's votes
    is best-effort and must not block the session about to start.
    """
    if not config.knowledge.enabled:
        return
    from wade.services.knowledge_service import flush_retained_staged_ratings

    try:
        outcomes = flush_retained_staged_ratings(repo_root, config.knowledge)
    except Exception as exc:
        logger.warning("knowledge.retained_sweep_failed", error=str(exc))
        return

    for outcome in outcomes:
        if outcome.success:
            console.info(
                f"Recovered {outcome.appended_count} staged knowledge vote(s) from a "
                f"retained session worktree at {outcome.worktree}."
            )
        else:
            # Still retained, still retryable — say so instead of going quiet.
            console.warn(
                f"Staged knowledge votes at {outcome.worktree} could not be handed off: "
                f"{outcome.message or 'unknown error'}"
            )
