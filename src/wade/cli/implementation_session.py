"""Implementation session subcommands — check, sync, done."""

from __future__ import annotations

from pathlib import Path

import typer

from wade.models.session import SyncEventType

implementation_session_app = typer.Typer(
    help="Implementation session commands (check, sync, done).",
)


@implementation_session_app.command()
def check() -> None:
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


@implementation_session_app.command()
def sync(
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON events."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without merging."),
    main_branch: str | None = typer.Option(
        None, "--main-branch", help="Override main branch name."
    ),
) -> None:
    """Sync current branch with main."""
    from wade.services.implementation_service import sync as do_sync

    result = do_sync(
        dry_run=dry_run,
        main_branch=main_branch,
        json_output=json_output,
        session_type="implementation",
    )
    # Exit codes: 0=success, 2=conflict, 4=preflight failure, 1=other error
    if result.success:
        if not json_output:
            from wade.ui.console import console

            console.info("Sync complete — proceed to wade implementation-session done.")
        raise typer.Exit(0)
    elif result.conflicts:
        if not json_output:
            from wade.ui.console import console

            console.info(
                "ACTION REQUIRED — resolve the conflicts listed above, "
                "then re-run wade implementation-session sync."
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


@implementation_session_app.command()
def done(
    target: str | None = typer.Argument(None, help="Issue number, worktree name, or plan file."),
    plan: str | None = typer.Option(None, "--plan", help="Plan file to resolve worktree from."),
    no_close: bool = typer.Option(False, "--no-close", help="Don't close the issue on merge."),
    draft: bool = typer.Option(False, "--draft", help="Create PR as draft."),
    no_cleanup: bool = typer.Option(False, "--no-cleanup", help="Don't remove worktree."),
) -> None:
    """Finalize implementation — push branch and create PR (or direct merge)."""
    from wade.services.implementation_service import done as do_done

    success = do_done(
        target=target,
        plan_file=Path(plan) if plan else None,
        no_close=no_close,
        draft=draft,
        no_cleanup=no_cleanup,
    )
    if success:
        from wade.ui.console import console

        console.info(
            "SESSION COMPLETE — do not make further changes. "
            "Present the PR link to the user and suggest they exit the session."
        )
    raise typer.Exit(0 if success else 1)
