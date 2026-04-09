"""Review PR comments session subcommands — check, sync, done, fetch, resolve."""

from __future__ import annotations

from pathlib import Path

import typer

review_pr_comments_session_app = typer.Typer(
    help="Review PR comments session commands (check, sync, done, fetch, resolve).",
)


@review_pr_comments_session_app.command()
def check() -> None:
    """Verify worktree safety for AI agents.

    Exit codes:
      0  IN_WORKTREE       — safe to work
      1  NOT_IN_GIT_REPO   — not inside a git repository
      2  IN_MAIN_CHECKOUT  — unsafe for agent work
    """
    from wade.cli.session_shared import run_check

    run_check()


@review_pr_comments_session_app.command()
def sync(
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON events."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without merging."),
    main_branch: str | None = typer.Option(
        None, "--main-branch", help="Override main branch name."
    ),
) -> None:
    """Sync current branch with main."""
    from wade.cli.session_shared import handle_sync_result
    from wade.services.implementation_service import sync as do_sync

    result = do_sync(
        dry_run=dry_run,
        main_branch=main_branch,
        json_output=json_output,
        session_type="review-pr-comments",
    )
    handle_sync_result(
        result,
        json_output=json_output,
        next_step_hint="wade review-pr-comments-session done",
    )


@review_pr_comments_session_app.command()
def done(
    target: str | None = typer.Argument(None, help="Issue number, worktree name, or plan file."),
    plan: str | None = typer.Option(None, "--plan", help="Plan file to resolve worktree from."),
    no_close: bool = typer.Option(False, "--no-close", help="Don't close the issue on merge."),
    draft: bool = typer.Option(False, "--draft", help="Create PR as draft."),
    no_cleanup: bool = typer.Option(False, "--no-cleanup", help="Don't remove worktree."),
) -> None:
    """Finalize review — push branch and update PR."""
    from wade.services.implementation_service import done as do_done

    success = do_done(
        target=target,
        plan_file=Path(plan) if plan else None,
        no_close=no_close,
        draft=draft,
        no_cleanup=no_cleanup,
    )
    if success:
        from wade.models.review import format_review_status_summary
        from wade.services.review_service import get_review_status
        from wade.ui.console import console

        status = get_review_status()
        if status is not None:
            messages = format_review_status_summary(status)
            for level, message in messages:
                if level == "success":
                    console.success(message)
                elif level == "warn":
                    console.warn(message)
                elif level == "info":
                    console.info(message)
            if not messages:
                console.info(
                    "SESSION COMPLETE — push succeeded. "
                    "Present the workflow recap, current state, and next steps to the user. "
                    "Suggest they exit the session."
                )
        else:
            console.warn(
                "SESSION COMPLETE — push succeeded, but review status could not be verified. "
                "Present the workflow recap, current state, and next steps to the user. "
                "Suggest they exit the session."
            )
    raise typer.Exit(0 if success else 1)


@review_pr_comments_session_app.command()
def fetch(
    target: str = typer.Argument(..., help="Issue number."),
) -> None:
    """Fetch unresolved PR review comments and print formatted markdown to stdout."""
    from wade.services.review_service import fetch_reviews

    success = fetch_reviews(target=target)
    raise typer.Exit(0 if success else 1)


@review_pr_comments_session_app.command()
def resolve(
    thread_id: str = typer.Argument(..., help="GitHub review thread node ID."),
) -> None:
    """Mark a PR review thread as resolved on GitHub."""
    from wade.services.review_service import resolve_thread

    success = resolve_thread(thread_id=thread_id)
    raise typer.Exit(0 if success else 1)
