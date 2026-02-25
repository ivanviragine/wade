"""Task subcommands — plan, create, list, read, update, close, deps."""

from __future__ import annotations

from pathlib import Path

import typer

task_app = typer.Typer(
    help="GitHub Issue CRUD + AI planning.",
    invoke_without_command=True,
)


@task_app.callback()
def task_callback(ctx: typer.Context) -> None:
    """Show interactive menu when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return

    from ghaiw.ui import prompts
    from ghaiw.ui.console import console

    menu_items = [
        "Plan tasks with AI",
        "Create a GitHub Issue",
        "List GitHub Issues",
        "Read a GitHub Issue",
        "Update a GitHub Issue",
        "Close a GitHub Issue",
        "Analyze dependencies",
    ]
    hints = [
        "task plan",
        "task create",
        "task list",
        "task read",
        "task update",
        "task close",
        "task deps",
    ]

    idx = prompts.menu("ghaiwpy task", menu_items, hints=hints)

    subcommands = ["plan", "create", "list", "read", "update", "close", "deps"]
    selected = subcommands[idx]

    if selected == "plan":
        from ghaiw.services.plan_service import plan as do_plan

        success = do_plan()
        raise typer.Exit(0 if success else 1)
    elif selected == "create":
        from ghaiw.services.task_service import create_interactive

        task = create_interactive()
        if task:
            console.empty()
            console.info("When you're ready to start, run:")
            console.detail(f"ghaiwpy work start {task.id}")
        raise typer.Exit(0 if task else 1)
    elif selected == "list":
        from ghaiw.services.task_service import list_tasks as do_list

        do_list()
        raise typer.Exit(0)
    elif selected == "read":
        number = prompts.input_prompt("Issue number")
        if number:
            from ghaiw.services.task_service import read_task

            task = read_task(number)
            raise typer.Exit(0 if task else 1)
    elif selected == "update":
        number = prompts.input_prompt("Issue number")
        if number:
            comment = prompts.input_prompt("Comment (or leave empty)")
            if comment:
                from ghaiw.services.task_service import update_task

                success = update_task(number, comment=comment)
                raise typer.Exit(0 if success else 1)
    elif selected == "close":
        number = prompts.input_prompt("Issue number")
        if number:
            from ghaiw.services.task_service import close_task

            success = close_task(number)
            raise typer.Exit(0 if success else 1)
    elif selected == "deps":
        numbers_str = prompts.input_prompt("Issue numbers (space-separated)")
        if numbers_str:
            from ghaiw.services.deps_service import analyze_deps

            issue_ids = numbers_str.split()
            graph = analyze_deps(issue_numbers=issue_ids)
            raise typer.Exit(0 if graph is not None else 1)

    raise typer.Exit(0)


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
        create_interactive,
    )
    from ghaiw.ui.console import console

    if plan_file:
        task = create_from_plan_file(Path(plan_file).expanduser())
    else:
        # Interactive mode — prompt for title and body
        task = create_interactive()

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
    from ghaiw.ui import prompts
    from ghaiw.ui.console import console

    tasks = do_list(state=state, show_deps=deps, json_mode=json_output)

    # Interactive picker: offer to start work on a task (only for open, non-JSON, TTY)
    if tasks and not json_output and state == "open" and prompts.is_tty():
        console.empty()
        items = [f"#{t.id} — {t.title}" for t in tasks]
        items.append("(none — exit)")
        idx = prompts.select("Start work on an issue?", items)
        if idx < len(tasks):
            from ghaiw.services.work_service import start as do_start

            success = do_start(target=tasks[idx].id)
            raise typer.Exit(0 if success else 1)

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
    check: bool = typer.Option(False, "--check", help="Validate existing dependencies."),
) -> None:
    """Analyze dependencies between issues."""
    from ghaiw.services.deps_service import analyze_deps
    from ghaiw.ui import prompts
    from ghaiw.ui.console import console

    # Interactive issue selection if no numbers provided
    if not numbers and prompts.is_tty():
        from ghaiw.services.task_service import list_tasks as do_list

        console.info("No issue numbers provided. Fetching open issues...")
        tasks = do_list(state="open", json_mode=True)
        if len(tasks) < 2:
            console.error("Need at least 2 open issues for dependency analysis.")
            raise typer.Exit(1)

        items = [f"#{t.id} — {t.title}" for t in tasks]
        console.info("Select issues for dependency analysis (enter numbers separated by spaces):")
        for i, item in enumerate(items):
            console.detail(f"  {i + 1}) {item}")
        selection = prompts.input_prompt("Issue indices (e.g., 1 2 3) or 'all'")
        if selection.strip().lower() == "all":
            numbers = [int(t.id) for t in tasks]
        else:
            try:
                indices = [int(x) - 1 for x in selection.split()]
                numbers = [int(tasks[i].id) for i in indices if 0 <= i < len(tasks)]
            except (ValueError, IndexError):
                console.error("Invalid selection.")
                raise typer.Exit(1) from None

    if not numbers:
        console.error("Provide at least 2 issue numbers.")
        raise typer.Exit(1)

    issue_ids = [str(n) for n in numbers]

    # --check mode: validate existing dependencies without re-running AI
    if check:
        import re

        from ghaiw.config.loader import load_config
        from ghaiw.providers.registry import get_provider

        config = load_config()
        provider = get_provider(config)
        valid_set = set(issue_ids)

        console.header("Dependency validation")
        all_valid = True
        for issue_id in issue_ids:
            try:
                task = provider.read_task(issue_id)
                dep_match = re.search(
                    r"\*\*Depends on:\*\*\s*(.*?)$",
                    task.body,
                    re.MULTILINE,
                )
                if dep_match:
                    dep_refs = re.findall(r"#(\d+)", dep_match.group(1))
                    for ref in dep_refs:
                        if ref not in valid_set:
                            console.warn(f"#{issue_id} depends on #{ref} (not in analyzed set)")
                            all_valid = False
                        else:
                            console.detail(f"#{issue_id} → #{ref} (valid)")
                block_match = re.search(
                    r"\*\*Blocks:\*\*\s*(.*?)$",
                    task.body,
                    re.MULTILINE,
                )
                if block_match:
                    block_refs = re.findall(r"#(\d+)", block_match.group(1))
                    for ref in block_refs:
                        if ref not in valid_set:
                            console.warn(f"#{issue_id} blocks #{ref} (not in analyzed set)")
                            all_valid = False
                        else:
                            console.detail(f"#{issue_id} blocks #{ref} (valid)")
            except Exception as e:
                console.warn(f"Could not read #{issue_id}: {e}")
                all_valid = False

        if all_valid:
            console.success("All dependencies are valid.")
        raise typer.Exit(0 if all_valid else 1)

    graph = analyze_deps(issue_numbers=issue_ids, ai_tool=ai, model=model)
    raise typer.Exit(0 if graph is not None else 1)
