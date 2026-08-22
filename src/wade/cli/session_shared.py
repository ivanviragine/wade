"""Shared helpers for implementation-session and review-pr-comments-session CLI commands."""

from __future__ import annotations

from pathlib import Path

import typer

from wade.models.session import SyncEventType, SyncResult
from wade.services.check_service import CheckResult

# Printed by both `implementation-session done` and `review-pr-comments-session
# done` after a successful push, mirroring the existing review advisory pattern.
DOC_PASS_ADVISORY = (
    "Documentation pass not confirmed — re-read what this session changed and "
    "update docs (or state why none were needed) if you haven't already."
)


def _session_readiness_result(phase: str) -> CheckResult:
    """Return the phase-aware readiness result for this exact CLI runtime.

    Keeping this in the CLI layer is deliberate: a session command and the
    initial skill check must inspect the environment that is actually executing
    ``wade``.  A worktree launcher may have more authority than the AI child
    that later runs ``sync`` or ``done``.
    """
    from wade.config.loader import load_config
    from wade.models.readiness import ReadinessPhase
    from wade.services.check_service import check_session_readiness

    readiness_phase = ReadinessPhase(phase)
    try:
        config = load_config()
    except Exception:
        # A bare repository can still use the legacy worktree check. Config
        # validation has its own command and should not obscure the exact git
        # context failure this preflight is responsible for.
        config = None
    command = {
        ReadinessPhase.PLAN: "plan",
        ReadinessPhase.IMPLEMENTATION: "implement",
        ReadinessPhase.REVIEW_PR_COMMENTS: "review_pr_comments",
        ReadinessPhase.DEPS: "deps",
    }[readiness_phase]
    tool = config.get_ai_tool(command) if config is not None else None
    return check_session_readiness(readiness_phase, Path.cwd(), config, tool)


def require_ready(phase: str, *, exit_code: int | None = None) -> None:
    """Stop a mutating session endpoint before its runtime is known-ready.

    ``wade`` is not privileged relative to the AI process that invokes it.
    Re-checking immediately before lifecycle commands prevents an initial
    successful check from masking a later resume, sandbox, PATH, credential, or
    network change.  ``exit_code`` lets existing command-level contracts (for
    example sync's preflight exit 4) remain stable while the output carries the
    specific machine-readable readiness reason.
    """
    result = _session_readiness_result(phase)
    if result.exit_code == 0:
        return
    typer.echo(result.format_output())
    raise typer.Exit(result.exit_code if exit_code is None else exit_code)


def run_check(phase: str) -> None:
    """Print the phase-specific capabilities an AI session needs.

    Exit codes:
      0  IN_WORKTREE          — safe to work
      1  NOT_IN_GIT_REPO      — not inside a git repository
      2  IN_MAIN_CHECKOUT     — unsafe for agent work
      3  WORKTREE_GIT_BLOCKED — linked-worktree git metadata is not writable
      4  GITHUB_CLI_BLOCKED — GitHub CLI cannot start in this runtime
      5  GITHUB_AUTH_BLOCKED — GitHub CLI credentials are unavailable
      6  GITHUB_API_BLOCKED — read-only GitHub API probe cannot reach GitHub
      7  KNOWLEDGE_STAGING_BLOCKED — detached vote staging cannot be written
    """
    result = _session_readiness_result(phase)
    typer.echo(result.format_output())
    raise typer.Exit(result.exit_code)


def handle_sync_result(result: SyncResult, *, json_output: bool, next_step_hint: str) -> None:
    """Map a SyncResult to the appropriate exit code and console message.

    Exit codes: 0=success, 2=conflict, 4=preflight failure, 1=other error.
    """
    if result.success:
        if not json_output:
            from wade.ui.console import console

            console.info(f"Sync complete — proceed to {next_step_hint}.")
        raise typer.Exit(0)
    elif result.conflicts:
        if not json_output:
            from wade.ui.console import console

            sync_cmd = next_step_hint.replace(" done", " sync")
            console.info(
                f"ACTION REQUIRED — resolve the conflicts listed above, then re-run {sync_cmd}."
            )
        raise typer.Exit(2)
    elif any(
        e.event == SyncEventType.ERROR
        and e.data.get("reason")
        in (
            "not_git_repo",
            "detached_head",
            "no_main_branch",
            "on_main_branch",
            "dirty_worktree",
        )
        for e in result.events
    ):
        raise typer.Exit(4)
    else:
        raise typer.Exit(1)
