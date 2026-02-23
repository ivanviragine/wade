"""Task subcommands — plan, create, list, read, update, close, deps."""

from __future__ import annotations

from pathlib import Path

import typer

task_app = typer.Typer(
    help="GitHub Issue CRUD + AI planning.",
    no_args_is_help=True,
)


@task_app.command()
def plan(
    ai: str | None = typer.Option(None, "--ai", help="AI tool to use for planning."),
    model: str | None = typer.Option(None, "--model", help="AI model to use."),
) -> None:
    """Run an AI-assisted planning session."""
    from ghaiw.services.plan_service import plan as do_plan

    success = do_plan(ai_tool=ai, model=model)
    raise typer.Exit(0 if success else 1)


@task_app.command()
def create(
    plan_file: str | None = typer.Option(None, "--plan-file", help="Path to plan markdown file."),
    no_start: bool = typer.Option(False, "--no-start", help="Skip interactive work-start prompt."),
    ai: str | None = typer.Option(None, "--ai", help="AI tool (for labeling)."),
    model: str | None = typer.Option(None, "--model", help="AI model (for labeling)."),
) -> None:
    """Create a GitHub Issue from a plan file or interactively."""
    from ghaiw.services.task_service import (
        add_planned_by_labels,
        create_from_plan_file,
    )
    from ghaiw.ui.console import console

    if not plan_file:
        console.error("--plan-file is required")
        raise typer.Exit(1)

    task = create_from_plan_file(Path(plan_file).expanduser())
    if not task:
        raise typer.Exit(1)

    # Add planned-by labels if AI tool is specified
    if ai and task:
        from ghaiw.config.loader import load_config
        from ghaiw.providers.registry import get_provider

        config = load_config()
        provider = get_provider(config)
        add_planned_by_labels(provider, task.id, ai, model)

    # Show next-step hint
    if not no_start:
        console.empty()
        console.info("When you're ready to start, run:")
        console.detail(f"ghaiwpy work start {task.id}")

    raise typer.Exit(0)


@task_app.command("list")
def list_tasks(
    state: str = typer.Option("open", "--state", "-s", help="Filter by state: open, closed, all."),
    deps: bool = typer.Option(False, "--deps", help="Show dependency refs."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List GitHub Issues."""
    from ghaiw.services.task_service import list_tasks as do_list

    do_list(state=state, show_deps=deps, json_mode=json_output)
    raise typer.Exit(0)


@task_app.command()
def read(
    number: int = typer.Argument(..., help="Issue number to read."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Read a GitHub Issue."""
    from ghaiw.services.task_service import read_task

    task = read_task(str(number), json_mode=json_output)
    raise typer.Exit(0 if task else 1)


@task_app.command()
def update(
    number: int = typer.Argument(..., help="Issue number to update."),
    plan_file: str | None = typer.Option(
        None, "--plan-file", help="Path to markdown file with new body."
    ),
    comment: str | None = typer.Option(None, "--comment", help="Comment text to add."),
) -> None:
    """Update a GitHub Issue body or add a comment."""
    from ghaiw.services.task_service import update_task

    plan_path = Path(plan_file).expanduser() if plan_file else None
    success = update_task(str(number), body_file=plan_path, comment=comment)
    raise typer.Exit(0 if success else 1)


@task_app.command()
def close(
    number: int = typer.Argument(..., help="Issue number to close."),
    comment: str | None = typer.Option(None, "--comment", help="Final comment before closing."),
) -> None:
    """Close a GitHub Issue."""
    from ghaiw.services.task_service import close_task

    success = close_task(str(number), comment=comment)
    raise typer.Exit(0 if success else 1)


_DEPS_NUMBERS = typer.Argument(None, help="Issue numbers to analyze.")


@task_app.command()
def deps(
    numbers: list[int] = _DEPS_NUMBERS,
    ai: str | None = typer.Option(None, "--ai", help="AI tool for analysis."),
    model: str | None = typer.Option(None, "--model", help="AI model to use."),
) -> None:
    """Analyze dependencies between issues."""
    from ghaiw.services.deps_service import analyze_deps
    from ghaiw.ui.console import console

    if not numbers:
        console.error("Provide at least 2 issue numbers.")
        raise typer.Exit(1)

    issue_ids = [str(n) for n in numbers]
    graph = analyze_deps(issue_numbers=issue_ids, ai_tool=ai, model=model)
    raise typer.Exit(0 if graph is not None else 1)
