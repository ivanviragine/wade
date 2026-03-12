"""Review subcommands — plan review, implementation review, and PR comment review."""

from __future__ import annotations

import typer

from wade.cli.autocomplete import (
    complete_ai_tools,
    complete_delegation_modes,
    complete_effort_levels,
    complete_models,
)
from wade.models.delegation import DelegationMode

review_app = typer.Typer(
    help="AI-powered review commands.",
    invoke_without_command=True,
)


@review_app.callback()
def review_callback(ctx: typer.Context) -> None:
    """Show help when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


@review_app.command("plan")
def review_plan_cmd(
    plan_file: str = typer.Argument(..., help="Path to the plan file to review."),
    ai: str | None = typer.Option(
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
    mode: DelegationMode | None = typer.Option(  # noqa: B008
        None,
        "--mode",
        help="Delegation mode: prompt, interactive, headless.",
        autocompletion=complete_delegation_modes,
    ),
    effort: str | None = typer.Option(
        None, "--effort", help="Effort level for AI.", autocompletion=complete_effort_levels
    ),
) -> None:
    """Review a plan file."""
    from wade.services.review_delegation_service import review_plan

    result = review_plan(
        plan_file,
        ai_tool=ai,
        model=model,
        mode=mode.value if mode else None,
        effort=effort,
        ai_explicit=ai is not None,
        model_explicit=model is not None,
        effort_explicit=effort is not None,
    )
    if result.success and not result.skipped:
        from wade.ui.console import console

        console.info(
            "REVIEW COMPLETE — address any actionable feedback above, "
            "then proceed to wade plan-session done."
        )
    raise typer.Exit(0 if result.success else 1)


@review_app.command("implementation")
def review_implementation_cmd(
    staged: bool = typer.Option(False, "--staged", help="Review only staged changes."),
    ai: str | None = typer.Option(
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
    mode: DelegationMode | None = typer.Option(  # noqa: B008
        None,
        "--mode",
        help="Delegation mode: prompt, interactive, headless.",
        autocompletion=complete_delegation_modes,
    ),
    effort: str | None = typer.Option(
        None, "--effort", help="Effort level for AI.", autocompletion=complete_effort_levels
    ),
) -> None:
    """Review code changes."""
    from wade.services.review_delegation_service import review_implementation

    result = review_implementation(
        staged=staged,
        ai_tool=ai,
        model=model,
        mode=mode.value if mode else None,
        effort=effort,
        ai_explicit=ai is not None,
        model_explicit=model is not None,
        effort_explicit=effort is not None,
    )
    if result.success and not result.skipped:
        from wade.ui.console import console

        console.info(
            "REVIEW COMPLETE — address any actionable feedback above, "
            "then proceed to wade implementation-session done."
        )
    raise typer.Exit(0 if result.success else 1)


@review_app.command("pr-comments")
def review_pr_comments_cmd(
    target: str = typer.Argument(..., help="Issue number."),
    ai: list[str] | None = typer.Option(  # noqa: B008
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
    detach: bool = typer.Option(False, "--detach", help="Launch AI in a new terminal."),
    yolo: bool = typer.Option(False, "--yolo", help="Skip AI tool permission prompts."),
) -> None:
    """Address PR review comments."""
    from wade.services.review_service import start as do_start
    from wade.ui import prompts

    selected_ai: str | None = None
    if ai and len(ai) > 1:
        idx = prompts.select("Select AI tool", ai)
        selected_ai = ai[idx]
    elif ai and len(ai) == 1:
        selected_ai = ai[0]

    success = do_start(
        target=target,
        ai_tool=selected_ai,
        model=model,
        detach=detach,
        ai_explicit=selected_ai is not None,
        model_explicit=model is not None,
        yolo=yolo or None,
    )
    raise typer.Exit(0 if success else 1)
