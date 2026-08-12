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
    no_stash: bool = typer.Option(
        False,
        "--no-stash",
        help="Disable auto-stash: fail immediately on any uncommitted changes.",
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
        no_stash=no_stash,
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
    skip_review: bool = typer.Option(
        False, "--skip-review", help="Skip the review-ran completion gate."
    ),
) -> None:
    """Finalize review — run the completion gates, push, and update the PR."""
    from wade.services.implementation_service import done as do_done

    success = do_done(
        target=target,
        plan_file=Path(plan) if plan else None,
        no_close=no_close,
        draft=draft,
        session_type="review-pr-comments",
        skip_review=skip_review,
    )
    if success:
        from wade.cli.session_shared import DOC_PASS_ADVISORY
        from wade.models.review import format_review_status_summary
        from wade.services.review_service import get_review_status
        from wade.ui.console import console

        console.warn(DOC_PASS_ADVISORY)

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
                    "Report by exception: give the PR/URL and what's next, and only call out "
                    "anything that needs the developer's attention. Suggest they exit the session."
                )
        else:
            console.warn(
                "SESSION COMPLETE — push succeeded, but review status could not be verified. "
                "Report by exception: give the PR/URL and what's next, and only call out "
                "anything that needs the developer's attention. Suggest they exit the session."
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
