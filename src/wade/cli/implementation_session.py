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
      0  IN_WORKTREE       — safe to work
      1  NOT_IN_GIT_REPO   — not inside a git repository
      2  IN_MAIN_CHECKOUT  — unsafe for agent work
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
) -> None:
    """Sync current branch with base branch (early catchup at session startup)."""
    from wade.cli.session_shared import handle_sync_result
    from wade.models.session import SyncEventType
    from wade.services.implementation_service import catchup as do_catchup

    result = do_catchup(
        dry_run=dry_run,
        main_branch=main_branch,
        json_output=json_output,
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
) -> None:
    """Sync current branch with main."""
    from wade.cli.session_shared import handle_sync_result
    from wade.services.implementation_service import sync as do_sync

    result = do_sync(
        dry_run=dry_run,
        main_branch=main_branch,
        json_output=json_output,
        session_type="implementation",
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

        # Remind agent to review if reviews are enabled. Advisory only —
        # must never turn a successful completion into a failure.
        try:
            from wade.config.loader import load_config

            config = load_config()
            if config.ai.review_implementation.enabled is not False:
                console.warn(
                    "Review not confirmed — run `wade review implementation` now "
                    "if you haven't already, then present results to the user."
                )
        except Exception:  # Advisory — must never break a successful completion
            pass

        console.info(
            "SESSION COMPLETE — do not make further changes. "
            "Present the workflow recap, current state, and next steps to the user. "
            "Suggest they exit the session."
        )
    raise typer.Exit(0 if success else 1)
