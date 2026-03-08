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
    console.info(
        "SESSION COMPLETE — do not implement anything. "
        "Suggest the user to exit the session now. "
        "wade will read the plan files and create GitHub issues automatically."
    )
    raise typer.Exit(0)
