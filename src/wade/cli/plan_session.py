"""Plan session subcommands — done."""

from __future__ import annotations

from pathlib import Path

import typer

plan_session_app = typer.Typer(
    help="Plan session commands (done).",
)


@plan_session_app.command()
def done(
    plan_dir: Path = typer.Argument(..., help="Path to the plan directory containing .md files."),  # noqa: B008
) -> None:
    """Validate plan files — run this before exiting a planning session."""
    from wade.services.plan_service import plan_done as do_plan_done
    from wade.ui.console import console

    result = do_plan_done(plan_dir)

    for diag in result.warnings:
        console.warn(f"{diag.file}: {diag.message}")

    for diag in result.errors:
        console.error(f"{diag.file}: {diag.message}")

    if result.has_errors:
        n = len(result.errors)
        console.error(f"Plan validation failed — {n} error(s) must be fixed before exiting.")
        raise typer.Exit(1)

    console.success(f"Plan validation passed ({len(result.warnings)} warning(s)).")

    # Remind agent to review if reviews are enabled. Advisory only —
    # must never turn a successful validation into a failure.
    try:
        from wade.config.loader import load_config

        config = load_config()
        if config.ai.review_plan.enabled is not False:
            console.hint("P.s.: run `wade review plan <plan_file>` if you haven't already.")
    except Exception:
        pass

    console.info(
        "SESSION COMPLETE — do not implement anything. "
        "Present the workflow recap and what happens next to the user. "
        "Suggest they exit the session now. "
        "wade will read the plan files and create GitHub issues automatically."
    )

    raise typer.Exit(0)
