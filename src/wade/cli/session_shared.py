"""Shared helpers for implementation-session and review-pr-comments-session CLI commands."""

from __future__ import annotations

from pathlib import Path

import typer

from wade.models.session import SyncEventType, SyncResult


def run_check() -> None:
    """Verify worktree safety for AI agents.

    Exit codes:
      0  IN_WORKTREE       — safe to work
      1  NOT_IN_GIT_REPO   — not inside a git repository
      2  IN_MAIN_CHECKOUT  — unsafe for agent work
    """
    from wade.services.check_service import check_worktree

    result = check_worktree(Path.cwd())
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
