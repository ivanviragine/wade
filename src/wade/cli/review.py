"""Review subcommands — plan review and code review via delegation."""

from __future__ import annotations

import typer

from wade.cli.autocomplete import complete_ai_tools, complete_models

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
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="Delegation mode: prompt, interactive, headless.",
    ),
) -> None:
    """Review a plan file."""
    from wade.services.review_delegation_service import review_plan

    result = review_plan(plan_file, ai_tool=ai, model=model, mode=mode)
    raise typer.Exit(0 if result.success else 1)


@review_app.command("code")
def review_code_cmd(
    staged: bool = typer.Option(False, "--staged", help="Review only staged changes."),
    ai: str | None = typer.Option(
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="Delegation mode: prompt, interactive, headless.",
    ),
) -> None:
    """Review code changes."""
    from wade.services.review_delegation_service import review_code

    result = review_code(staged=staged, ai_tool=ai, model=model, mode=mode)
    raise typer.Exit(0 if result.success else 1)
