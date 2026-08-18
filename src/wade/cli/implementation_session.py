"""Implementation session subcommands — check, sync, done."""

from __future__ import annotations

from pathlib import Path

import typer

implementation_session_app = typer.Typer(
    help="Implementation session commands (check, sync, done).",
)


@implementation_session_app.command()
def check() -> None:
    """Verify worktree safety for AI agents.

    Exit codes:
      0  IN_WORKTREE          — safe to work
      1  NOT_IN_GIT_REPO      — not inside a git repository
      2  IN_MAIN_CHECKOUT     — unsafe for agent work
      3  WORKTREE_GIT_BLOCKED — worktree git metadata is not writable
    """
    from wade.cli.session_shared import run_check

    run_check()


@implementation_session_app.command()
def catchup(
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
    """Sync current branch with base branch (early catchup at session startup)."""
    from wade.cli.session_shared import handle_sync_result
    from wade.models.session import SyncEventType
    from wade.services.implementation_service import catchup as do_catchup

    result = do_catchup(
        dry_run=dry_run,
        main_branch=main_branch,
        json_output=json_output,
        no_stash=no_stash,
    )
    # Catchup has custom success messages for dry-run vs real merge
    if result.success:
        if not json_output:
            from wade.ui.console import console

            if any(e.event == SyncEventType.DRY_RUN for e in result.events):
                console.info("Catchup preview complete.")
            else:
                console.info("Catchup complete — branch is up to date.")
        raise typer.Exit(0)
    # Conflicts get a catchup-specific message
    if result.conflicts:
        if not json_output:
            from wade.ui.console import console

            console.info(
                "ACTION REQUIRED — merge aborted (inspection-only), no conflict markers remain. "
                "Resolve manually via `git merge` or `git rebase`, then re-run "
                "wade implementation-session catchup."
            )
        raise typer.Exit(2)
    # Preflight and other errors use the shared handler
    handle_sync_result(
        result, json_output=json_output, next_step_hint="wade implementation-session catchup"
    )


@implementation_session_app.command()
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
        session_type="implementation",
        no_stash=no_stash,
    )
    handle_sync_result(
        result, json_output=json_output, next_step_hint="wade implementation-session done"
    )


@implementation_session_app.command()
def done(
    target: str | None = typer.Argument(None, help="Issue number, worktree name, or plan file."),
    plan: str | None = typer.Option(None, "--plan", help="Plan file to resolve worktree from."),
    no_close: bool = typer.Option(False, "--no-close", help="Don't close the issue on merge."),
    draft: bool = typer.Option(False, "--draft", help="Create PR as draft."),
    skip_review: bool = typer.Option(
        False, "--skip-review", help="Skip the review-ran completion gate."
    ),
) -> None:
    """Finalize implementation — run the completion gates, push, and update the PR."""
    from wade.services.implementation_service import done as do_done

    success = do_done(
        target=target,
        plan_file=Path(plan) if plan else None,
        no_close=no_close,
        draft=draft,
        session_type="implementation",
        skip_review=skip_review,
    )
    if success:
        from wade.cli.session_shared import DOC_PASS_ADVISORY
        from wade.ui.console import console

        # The review-ran gate now enforces that `wade review implementation` ran
        # for this sha before `done` succeeds, so the old post-done "review not
        # confirmed" advisory is redundant (and would contradict the gate).
        console.warn(DOC_PASS_ADVISORY)

        console.info(
            "SESSION COMPLETE. "
            "Report by exception: end with the emoji step-status summary (Review, Docs, "
            "PR-SUMMARY, Sync, Done) and its handles — PR number/URL, closes #N on merge, "
            "branch — then present the exit decision as a native dialog whose first option "
            "is 'Exit now — wade takes over (recommended)'. Surface only what "
            "needs the developer's attention."
        )
    raise typer.Exit(0 if success else 1)
