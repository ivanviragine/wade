"""Address-reviews session subcommands — check, sync, done, fetch, resolve."""

from __future__ import annotations

from pathlib import Path

import typer

from wade.models.work import SyncEventType

reviews_session_app = typer.Typer(
    help="Review session commands (check, sync, done, fetch, resolve).",
)


@reviews_session_app.command()
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


@reviews_session_app.command()
def sync(
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON events."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without merging."),
    main_branch: str | None = typer.Option(
        None, "--main-branch", help="Override main branch name."
    ),
) -> None:
    """Sync current branch with main."""
    from wade.services.work_service import sync as do_sync

    result = do_sync(
        dry_run=dry_run,
        main_branch=main_branch,
        json_output=json_output,
        session_type="address-reviews",
    )
    if result.success:
        raise typer.Exit(0)
    elif result.conflicts:
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


@reviews_session_app.command()
def done(
    target: str | None = typer.Argument(None, help="Issue number, worktree name, or plan file."),
    plan: str | None = typer.Option(None, "--plan", help="Plan file to resolve worktree from."),
    no_close: bool = typer.Option(False, "--no-close", help="Don't close the issue on merge."),
    draft: bool = typer.Option(False, "--draft", help="Create PR as draft."),
    no_cleanup: bool = typer.Option(False, "--no-cleanup", help="Don't remove worktree."),
) -> None:
    """Finalize work — push branch and create PR (or direct merge)."""
    from wade.services.work_service import done as do_done

    success = do_done(
        target=target,
        plan_file=Path(plan) if plan else None,
        no_close=no_close,
        draft=draft,
        no_cleanup=no_cleanup,
    )
    raise typer.Exit(0 if success else 1)


@reviews_session_app.command()
def fetch(
    target: str = typer.Argument(..., help="Issue number."),
) -> None:
    """Fetch unresolved PR review comments and print formatted markdown to stdout."""
    from wade.services.review_service import fetch_reviews

    success = fetch_reviews(target=target)
    raise typer.Exit(0 if success else 1)


@reviews_session_app.command()
def resolve(
    thread_id: str = typer.Argument(..., help="GitHub review thread node ID."),
) -> None:
    """Mark a PR review thread as resolved on GitHub."""
    from wade.services.review_service import resolve_thread

    success = resolve_thread(thread_id=thread_id)
    raise typer.Exit(0 if success else 1)
