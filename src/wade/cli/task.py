"""Task subcommands — create, list, read, update, close, deps."""

from __future__ import annotations

import shlex
from pathlib import Path

import typer

from wade.cli.autocomplete import (
    complete_ai_tools,
    complete_delegation_modes,
    complete_effort_levels,
    complete_models,
    complete_permission_modes,
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
        from wade.utils.conventional import ConventionalTitleError

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

        try:
            task = create_task(title=title, body=resolved_body, extra_labels=list(label or []))
        except ConventionalTitleError as e:
            # The rejected title is echoed in the message — disable Rich markup so
            # bracket tokens in it can't be parsed as markup (which would crash).
            console.error(str(e), markup=False)
            console.hint(
                "The PR title is derived from the issue title, so it must be "
                "conventional-commit format."
            )
            raise typer.Exit(1) from None
    else:
        from wade.services.task_service import create_interactive

        task = create_interactive()

    if task:
        console.empty()
        console.info("When you're ready to implement, run:")
        console.command(f"wade implement {shlex.quote(task.id)}")
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
        items = [f"{t.id} — {t.title}" for t in tasks]
        items.append("(none — exit)")
        idx = prompts.select("Start work on an issue?", items)
        if idx < len(tasks):
            from wade.services.implementation_service import start as do_start

            result = do_start(target=tasks[idx].id)
            raise typer.Exit(0 if result.success else 1)

    raise typer.Exit(0)


@task_app.command()
def read(
    number: str = typer.Argument(..., help="Task ID to read (e.g. 42 or abc1234)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Read a task."""
    from wade.services.task_service import read_task

    task = read_task(number, json_mode=json_output)
    raise typer.Exit(0 if task else 1)


@task_app.command()
def update(
    number: str = typer.Argument(..., help="Task ID to update (e.g. 42 or abc1234)."),
    plan_file: str | None = typer.Option(
        None, "--plan-file", help="Path to markdown file with new body."
    ),
    comment: str | None = typer.Option(None, "--comment", help="Comment text to add."),
) -> None:
    """Update a task body or add a comment."""
    from wade.services.task_service import update_task

    plan_path = Path(plan_file).expanduser() if plan_file else None
    success = update_task(number, body_file=plan_path, comment=comment)
    raise typer.Exit(0 if success else 1)


@task_app.command()
def close(
    number: str = typer.Argument(..., help="Task ID to close (e.g. 42 or abc1234)."),
    comment: str | None = typer.Option(None, "--comment", help="Final comment before closing."),
) -> None:
    """Close a task."""
    from wade.services.task_service import close_task

    success = close_task(number, comment=comment)
    raise typer.Exit(0 if success else 1)


_DEPS_NUMBERS = typer.Argument(None, help="Task IDs to analyze (e.g. 42 43 or abc1234 def5678).")


@task_app.command()
def deps(
    numbers: list[str] = _DEPS_NUMBERS,
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
    yolo: bool = typer.Option(False, "--yolo", help="Skip AI tool permission prompts."),
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help="Autonomy tier: default, accept-edits, auto, or yolo.",
        autocompletion=complete_permission_modes,
    ),
    sandbox: bool | None = typer.Option(
        None,
        "--sandbox/--no-sandbox",
        help="Confine the AI runtime to its sandbox (default: off — the runtime runs "
        "unrestricted so delegated tools keep host credentials). Overrides ai.sandbox.",
    ),
    check: bool = typer.Option(False, "--check", help="Validate existing dependencies."),
    skill: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--skill",
        help="Dependency-analysis methodology skill ref. Repeat for an ordered binding.",
    ),
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

        numbers = list(selected_ids)

    if not numbers:
        console.error("Provide at least 2 issue numbers.")
        raise typer.Exit(1)

    issue_ids = list(numbers)

    # --check mode: validate existing dependencies without re-running AI
    if check:
        from wade.config.loader import load_config
        from wade.models.task import format_task_ref, parse_dependency_refs
        from wade.providers.registry import get_provider

        config = load_config()
        provider = get_provider(config)
        provider_name = str(config.provider.name)
        valid_set = set(issue_ids)

        def label(task_id: str) -> str:
            return console.escape_markup(format_task_ref(task_id, provider_name))

        console.header("Dependency validation")
        all_valid = True
        for issue_id in issue_ids:
            try:
                task = provider.read_task(issue_id)
                refs = parse_dependency_refs(task.body, provider=provider_name)
                for ref in refs["depends_on"]:
                    if ref not in valid_set:
                        console.warn(
                            f"{label(issue_id)} depends on {label(ref)} (not in analyzed set)"
                        )
                        all_valid = False
                    else:
                        console.detail(f"{label(issue_id)} → {label(ref)} (valid)")
                for ref in refs["blocks"]:
                    if ref not in valid_set:
                        console.warn(f"{label(issue_id)} blocks {label(ref)} (not in analyzed set)")
                        all_valid = False
                    else:
                        console.detail(f"{label(issue_id)} blocks {label(ref)} (valid)")
            except Exception as e:
                console.warn(console.escape_markup(f"Could not read {issue_id}: {e}"))
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
        yolo=yolo or None,
        permission_mode=permission_mode,
        permission_mode_explicit=permission_mode is not None,
        sandbox=sandbox,
        skills=skill,
    )
    raise typer.Exit(0 if graph is not None else 1)
