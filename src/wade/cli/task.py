"""Task subcommands — create, list, read, update, close, deps."""

from __future__ import annotations

from pathlib import Path

import typer

from wade.cli.autocomplete import (
    complete_ai_tools,
    complete_delegation_modes,
    complete_effort_levels,
    complete_models,
)

task_app = typer.Typer(
    help="GitHub Issue management.",
    invoke_without_command=True,
)


@task_app.callback()
def task_callback(ctx: typer.Context) -> None:
    """Show interactive menu when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return

    from wade.ui import prompts
    from wade.ui.console import console

    menu_items = [
        "Create a new GitHub Issue",
        "List GitHub Issues",
        "Read a GitHub Issue",
        "Update a GitHub Issue",
        "Close a GitHub Issue",
        "Analyze dependencies",
    ]
    hints = [
        "task create",
        "task list",
        "task read",
        "task update",
        "task close",
        "task deps",
    ]

    console.hint("Use `wade plan` to plan tasks with AI.")

    idx = prompts.menu("wade task", menu_items, hints=hints)

    subcommands = ["create", "list", "read", "update", "close", "deps"]
    selected = subcommands[idx]

    if selected == "create":
        from wade.services.task_service import create_interactive

        task = create_interactive()
        raise typer.Exit(0 if task else 1)
    elif selected == "list":
        from wade.services.task_service import list_tasks as do_list

        do_list()
        raise typer.Exit(0)
    elif selected == "read":
        from wade.services.task_service import prompt_task_selection

        number = prompt_task_selection("Issue number")
        if number:
            from wade.services.task_service import read_task

            task = read_task(number)
            raise typer.Exit(0 if task else 1)
    elif selected == "update":
        from wade.services.task_service import prompt_task_selection

        number = prompt_task_selection("Issue number")
        if number:
            comment = prompts.input_prompt("Comment (or leave empty)")
            if comment:
                from wade.services.task_service import update_task

                success = update_task(number, comment=comment)
                raise typer.Exit(0 if success else 1)
    elif selected == "close":
        from wade.services.task_service import prompt_task_selection

        number = prompt_task_selection("Issue number")
        if number:
            from wade.services.task_service import close_task

            success = close_task(number)
            raise typer.Exit(0 if success else 1)
    elif selected == "deps":
        from wade.services.task_service import prompt_multi_task_selection

        issue_ids = prompt_multi_task_selection("Select issues for analysis")
        if issue_ids:
            from wade.services.deps_service import analyze_deps

            graph = analyze_deps(issue_numbers=issue_ids)
            raise typer.Exit(0 if graph is not None else 1)

    raise typer.Exit(0)


@task_app.command()
def create(
    title: str | None = typer.Option(None, "--title", "-t", help="Issue title (non-interactive)."),
    body: str | None = typer.Option(None, "--body", "-b", help="Issue body text."),
    body_file: str | None = typer.Option(
        None, "--body-file", help="Path to a file whose contents become the issue body."
    ),
    label: list[str] | None = typer.Option(  # noqa: B008
        None, "--label", "-l", help="Extra label(s) to apply (can repeat)."
    ),
) -> None:
    """Create a new GitHub Issue (interactive by default, non-interactive with --title)."""
    from wade.ui.console import console

    if title is not None:
        from pathlib import Path

        from wade.services.task_service import create_task

        # Resolve body: --body-file takes precedence over --body
        resolved_body = ""
        if body_file:
            bp = Path(body_file).expanduser()
            if not bp.is_file():
                console.error(f"File not found: {body_file}")
                raise typer.Exit(1)
            try:
                resolved_body = bp.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                console.error(f"Could not read {body_file}: {e}")
                raise typer.Exit(1) from None
        elif body:
            resolved_body = body

        task = create_task(title=title, body=resolved_body, extra_labels=list(label or []))
    else:
        from wade.services.task_service import create_interactive

        task = create_interactive()

    if task:
        console.empty()
        console.info("When you're ready to implement, run:")
        console.detail(f"wade implement {task.id}")
    raise typer.Exit(0 if task else 1)


@task_app.command("list")
def list_tasks(
    state: str = typer.Option("open", "--state", "-s", help="Filter by state: open, closed, all."),
    deps: bool = typer.Option(False, "--deps", help="Show dependency refs."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List GitHub Issues."""
    from wade.services.task_service import list_tasks as do_list
    from wade.ui import prompts
    from wade.ui.console import console

    tasks = do_list(state=state, show_deps=deps, json_mode=json_output)

    # Interactive picker: offer to start work on a task (only for open, non-JSON, TTY)
    if tasks and not json_output and state == "open" and prompts.is_tty():
        console.empty()
        items = [f"#{t.id} — {t.title}" for t in tasks]
        items.append("(none — exit)")
        idx = prompts.select("Start work on an issue?", items)
        if idx < len(tasks):
            from wade.services.implementation_service import start as do_start

            success = do_start(target=tasks[idx].id)
            raise typer.Exit(0 if success else 1)

    raise typer.Exit(0)


@task_app.command()
def read(
    number: int = typer.Argument(..., help="Issue number to read."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Read a GitHub Issue."""
    from wade.services.task_service import read_task

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
    from wade.services.task_service import update_task

    plan_path = Path(plan_file).expanduser() if plan_file else None
    success = update_task(str(number), body_file=plan_path, comment=comment)
    raise typer.Exit(0 if success else 1)


@task_app.command()
def close(
    number: int = typer.Argument(..., help="Issue number to close."),
    comment: str | None = typer.Option(None, "--comment", help="Final comment before closing."),
) -> None:
    """Close a GitHub Issue."""
    from wade.services.task_service import close_task

    success = close_task(str(number), comment=comment)
    raise typer.Exit(0 if success else 1)


_DEPS_NUMBERS = typer.Argument(None, help="Issue numbers to analyze.")


@task_app.command()
def deps(
    numbers: list[int] = _DEPS_NUMBERS,
    ai: str | None = typer.Option(
        None, "--ai", help="AI tool for analysis.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help="Reasoning effort level: low, medium, high, max.",
        autocompletion=complete_effort_levels,
    ),
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="Delegation mode: prompt, headless, interactive.",
        autocompletion=complete_delegation_modes,
    ),
    check: bool = typer.Option(False, "--check", help="Validate existing dependencies."),
) -> None:
    """Analyze dependencies between issues."""
    from wade.services.deps_service import analyze_deps
    from wade.ui import prompts
    from wade.ui.console import console

    # Interactive issue selection if no numbers provided
    if not numbers and prompts.is_tty():
        from wade.services.task_service import prompt_multi_task_selection

        selected_ids = prompt_multi_task_selection("Select issues for dependency analysis")
        if len(selected_ids) < 2:
            console.error("Need at least 2 issues for dependency analysis.")
            raise typer.Exit(1)

        numbers = [int(id_str) for id_str in selected_ids]

    if not numbers:
        console.error("Provide at least 2 issue numbers.")
        raise typer.Exit(1)

    issue_ids = [str(n) for n in numbers]

    # --check mode: validate existing dependencies without re-running AI
    if check:
        from wade.config.loader import load_config
        from wade.models.task import parse_dependency_refs
        from wade.providers.registry import get_provider

        config = load_config()
        provider = get_provider(config)
        valid_set = set(issue_ids)

        console.header("Dependency validation")
        all_valid = True
        for issue_id in issue_ids:
            try:
                task = provider.read_task(issue_id)
                refs = parse_dependency_refs(task.body)
                for ref in refs["depends_on"]:
                    if ref not in valid_set:
                        console.warn(f"#{issue_id} depends on #{ref} (not in analyzed set)")
                        all_valid = False
                    else:
                        console.detail(f"#{issue_id} → #{ref} (valid)")
                for ref in refs["blocks"]:
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

    graph = analyze_deps(
        issue_numbers=issue_ids,
        ai_tool=ai,
        model=model,
        ai_explicit=ai is not None,
        model_explicit=model is not None,
        effort=effort,
        effort_explicit=effort is not None,
        mode=mode,
    )
    raise typer.Exit(0 if graph is not None else 1)
