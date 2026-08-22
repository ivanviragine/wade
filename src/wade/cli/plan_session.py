"""Plan session subcommands — done."""

from __future__ import annotations

from pathlib import Path

import typer

plan_session_app = typer.Typer(
    help="Plan session commands (check, done).",
)


@plan_session_app.command()
def check() -> None:
    """Verify planning-session capabilities before writing plan artefacts.

    Exit codes match the other session checks, with 4/5 for GitHub
    authentication/API failures and 6 when detached knowledge-vote staging is
    not writable.
    """
    from wade.cli.session_shared import run_check

    run_check("plan")


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
        # A diagnostic can embed the plan's own (untrusted) title — render without
        # Rich markup so bracket tokens in it aren't parsed as markup.
        console.error(f"{diag.file}: {diag.message}", markup=False)

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
    except Exception:  # Advisory — must never break a successful validation
        pass

    console.info(
        "SESSION COMPLETE — do not implement anything. "
        "Report by exception: end with the emoji step-status summary (steps: Plan "
        "file(s), Review, Knowledge, Validate) and its handles, then present the exit "
        "decision as a native dialog whose first option is "
        "'Exit now — wade creates the issue(s) & draft PR(s) (recommended)'. "
        "Surface only what needs the user's attention."
    )

    raise typer.Exit(0)
