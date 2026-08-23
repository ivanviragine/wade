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


def _session_readiness_result(phase: str, cwd: Path | None = None) -> CheckResult:
    """Delegate to the readiness service, naming the directory to inspect.

    Resolution (config load, phase -> ``ai.<command>`` mapping, tool selection)
    lives in ``check_service.resolve_session_readiness``; this stays dispatch.

    ``cwd`` defaults to the runtime actually executing ``wade`` and is
    overridden only by the endpoints that operate on a *resolved* worktree
    rather than the caller's own (``done`` with a worktree target or
    ``--plan``).
    """
    from wade.services.check_service import resolve_session_readiness

    return resolve_session_readiness(phase, cwd)


def require_ready(
    phase: str,
    *,
    exit_code: int | None = None,
    json_output: bool = False,
    cwd: Path | None = None,
) -> None:
    """Stop a mutating session endpoint before its runtime is known-ready.

    ``wade`` is not privileged relative to the AI process that invokes it.
    Re-checking immediately before lifecycle commands prevents an initial
    successful check from masking a later resume, sandbox, PATH, credential, or
    network change.  ``exit_code`` lets existing command-level contracts (for
    example sync's preflight exit 4) remain stable while the output carries the
    specific machine-readable readiness reason.

    ``json_output`` keeps sync's line-delimited JSON contract intact: a caller
    that asked for JSON must never receive the human-readable readiness block
    on stdout, so the same verdict is emitted as one ``error`` event instead.

    ``cwd`` is the directory to check — pass the worktree the command will
    actually act on when that differs from the process cwd.
    """
    result = _session_readiness_result(phase, cwd)
    if result.exit_code == 0:
        return
    if json_output:
        import json

        reason = result.failure.value if result.failure else result.status.value.lower()
        payload: dict[str, str] = {
            "event": "error",
            "reason": reason,
            "status": result.status.value,
        }
        if result.phase is not None:
            payload["phase"] = result.phase.value
        typer.echo(json.dumps(payload))
    else:
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
