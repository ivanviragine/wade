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

    Planning is deliberately offline in the agent runtime: it checks the
    detached checkout plus local vote staging only. Exit 7 means the latter is
    not writable; GitHub finalization happens later in the trusted parent
    ``wade plan`` process.
    """
    from wade.cli.session_shared import run_check

    run_check("plan")


@plan_session_app.command()
def done(
    plan_dir: Path = typer.Argument(..., help="Path to the plan directory containing .md files."),  # noqa: B008
    from_file: Path | None = typer.Option(  # noqa: B008
        None,
        "--from-file",
        help="Import this native Markdown plan or WADE bundle before validation.",
    ),
    from_stdin: bool = typer.Option(
        False, "--from-stdin", help="Read the explicit plan or bundle from stdin before validation."
    ),
) -> None:
    """Validate plan files — run this before exiting a planning session."""
    from wade.services import interactive_plan_service as interactive
    from wade.services.plan_service import plan_done as do_plan_done
    from wade.ui.console import console

    directory = plan_dir.absolute()
    root = directory.parent.parent
    managed = (
        directory.name == "plans" and directory.parent.name == ".wade" and interactive.active(root)
    )
    try:
        if from_file is not None and from_stdin:
            raise ValueError("Choose --from-file or --from-stdin, not both")
        if from_file is not None or from_stdin:
            if not managed:
                raise ValueError(
                    "Plan import requires the active interactive session's .wade/plans"
                )
            if from_file is not None:
                content = interactive.read_artifact(from_file)
            else:
                import sys

                content = sys.stdin.read(2_000_001)
            interactive.import_artifact(root, content)
    except (ValueError, OSError) as exc:
        from wade.services.native_plan_service import failure_message

        console.error(failure_message(exc), markup=False)
        raise typer.Exit(1) from exc

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

    if managed:
        try:
            interactive.complete(root)
        except (ValueError, OSError) as exc:
            from wade.services.native_plan_service import failure_message

            console.error(failure_message(exc), markup=False)
            raise typer.Exit(1) from exc

    # Remind agent to review if reviews are enabled. Advisory only —
    # must never turn a successful validation into a failure.
    try:
        from wade.config.loader import load_config

        config = load_config()
        if not managed and config.ai.review_plan.enabled is not False:
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
