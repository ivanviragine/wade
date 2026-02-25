"""Work subcommands — start, done, sync, list, batch, remove, cd."""

from __future__ import annotations

from pathlib import Path

import typer

from ghaiw.cli.autocomplete import complete_ai_tools, complete_models

work_app = typer.Typer(
    help="Work session lifecycle.",
    invoke_without_command=True,
)


@work_app.callback()
def work_callback(ctx: typer.Context) -> None:
    """Show interactive menu when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return  # A subcommand was specified — let it run

    from ghaiw.ui import prompts
    from ghaiw.ui.console import console

    menu_items = [
        "Start a work session",
        "Finalize work (create PR or merge)",
        "Sync branch with main",
        "List active work sessions",
        "Start parallel work sessions",
        "Remove a worktree",
    ]
    hints = [
        "work start",
        "work done",
        "work sync",
        "work list",
        "work batch",
        "work remove",
    ]

    idx = prompts.menu("ghaiwpy work", menu_items, hints=hints)

    # Map menu selection to subcommand invocations
    subcommands = ["start", "done", "sync", "list", "batch", "remove"]
    selected = subcommands[idx]

    if selected == "start":
        target = prompts.input_prompt("Issue number or plan file")
        if target:
            from ghaiw.services.work_service import start as do_start

            success = do_start(target=target)
            raise typer.Exit(0 if success else 1)
    elif selected == "done":
        from ghaiw.services.work_service import done as do_done

        success = do_done()
        raise typer.Exit(0 if success else 1)
    elif selected == "sync":
        from ghaiw.services.work_service import sync as do_sync

        result = do_sync()
        raise typer.Exit(0 if result.success else 1)
    elif selected == "list":
        from ghaiw.services.work_service import list_sessions as do_list

        do_list()
        raise typer.Exit(0)
    elif selected == "batch":
        console.info("Use: ghaiwpy work batch <issue numbers>")
        raise typer.Exit(0)
    elif selected == "remove":
        target = prompts.input_prompt("Issue number or worktree name")
        if target:
            from ghaiw.services.work_service import remove as do_remove

            success = do_remove(target=target)
            raise typer.Exit(0 if success else 1)

    raise typer.Exit(0)


@work_app.command()
def start(
    target: str = typer.Argument(..., help="Issue number or plan file path."),
    ai: list[str] | None = typer.Option(  # noqa: B008
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
    detach: bool = typer.Option(False, "--detach", help="Launch AI in a new terminal."),
    cd_only: bool = typer.Option(
        False, "--cd", help="Create worktree and print path (no AI launch)."
    ),
) -> None:
    """Start a work session on an issue."""
    from ghaiw.services.work_service import start as do_start
    from ghaiw.ui import prompts

    # Resolve multiple --ai flags to a single value
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
        cd_only=cd_only,
    )
    raise typer.Exit(0 if success else 1)


@work_app.command()
def done(
    target: str | None = typer.Argument(None, help="Issue number, worktree name, or plan file."),
    plan: str | None = typer.Option(None, "--plan", help="Plan file to resolve worktree from."),
    no_close: bool = typer.Option(False, "--no-close", help="Don't close the issue on merge."),
    draft: bool = typer.Option(False, "--draft", help="Create PR as draft."),
    no_cleanup: bool = typer.Option(False, "--no-cleanup", help="Don't remove worktree."),
) -> None:
    """Finalize work — push branch and create PR (or direct merge)."""
    from ghaiw.services.work_service import done as do_done

    success = do_done(
        target=target,
        plan_file=Path(plan) if plan else None,
        no_close=no_close,
        draft=draft,
        no_cleanup=no_cleanup,
    )
    raise typer.Exit(0 if success else 1)


@work_app.command()
def sync(
    json_output: bool = typer.Option(False, "--json", help="Output structured JSON events."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without merging."),
    main_branch: str | None = typer.Option(
        None, "--main-branch", help="Override main branch name."
    ),
) -> None:
    """Sync current branch with main."""
    from ghaiw.services.work_service import sync as do_sync

    result = do_sync(
        dry_run=dry_run,
        main_branch=main_branch,
        json_output=json_output,
    )
    # Exit codes: 0=success, 2=conflict, 4=preflight failure, 1=other error
    if result.success:
        raise typer.Exit(0)
    elif result.conflicts:
        raise typer.Exit(2)
    elif any(
        e.event == "error"
        and e.data.get("reason")
        in (
            "not_git_repo",
            "detached_head",
            "no_main_branch",
            "on_main_branch",
            "dirty_worktree",
        )
        for e in result.events
    ):
        raise typer.Exit(4)
    else:
        raise typer.Exit(1)


@work_app.command("list")
def list_sessions(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
    show_all: bool = typer.Option(False, "--all", help="Show all worktrees including main."),
) -> None:
    """List active work sessions / worktrees."""
    from ghaiw.services.work_service import list_sessions as do_list

    do_list(show_all=show_all, json_output=json_output)
    raise typer.Exit(0)


_BATCH_NUMBERS = typer.Argument(None, help="Issue numbers to work on.")


@work_app.command()
def batch(
    numbers: list[int] = _BATCH_NUMBERS,
    ai: str | None = typer.Option(
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
) -> None:
    """Start parallel work sessions for multiple issues."""
    from ghaiw.services.work_service import batch as do_batch
    from ghaiw.ui import prompts
    from ghaiw.ui.console import console

    # Interactive picker if no numbers provided
    if not numbers and prompts.is_tty():
        from ghaiw.services.task_service import list_tasks as fetch_tasks

        console.info("No issue numbers provided. Fetching open issues...")
        tasks = fetch_tasks(state="open", json_mode=True)
        if not tasks:
            console.error("No open issues found.")
            raise typer.Exit(1)

        items = [f"#{t.id} — {t.title}" for t in tasks]
        console.info("Select issues for batch work (enter numbers separated by spaces):")
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
        console.error("Provide at least one issue number.")
        raise typer.Exit(1)

    issue_ids = [str(n) for n in numbers]
    success = do_batch(issue_numbers=issue_ids, ai_tool=ai, model=model)
    raise typer.Exit(0 if success else 1)


@work_app.command()
def remove(
    target: str | None = typer.Argument(None, help="Issue number or worktree name."),
    stale: bool = typer.Option(False, "--stale", help="Remove all stale worktrees."),
    show_all: bool = typer.Option(False, "--all", hidden=True, help="Alias for --stale."),
    force: bool = typer.Option(False, "--force", help="Skip confirmation."),
) -> None:
    """Remove a worktree."""
    from ghaiw.services.work_service import list_sessions
    from ghaiw.services.work_service import remove as do_remove
    from ghaiw.ui import prompts
    from ghaiw.ui.console import console

    effective_stale = stale or show_all

    # Interactive picker if no target and not --stale
    if not target and not effective_stale and prompts.is_tty():
        sessions = list_sessions(json_output=True)
        if not sessions:
            console.info("No worktrees to remove.")
            raise typer.Exit(0)

        items = []
        for s in sessions:
            issue_str = f"#{s['issue']}" if s["issue"] else "(no issue)"
            staleness = s["staleness"].upper().replace("_", " ")
            items.append(f"[{staleness}] {issue_str} — {s['branch']}")
        items.append("(cancel)")

        idx = prompts.select("Select worktree to remove", items)
        if idx < len(sessions):
            target = sessions[idx]["issue"] or sessions[idx]["branch"]
        else:
            raise typer.Exit(0)

    success = do_remove(target=target, stale=effective_stale, force=force)
    raise typer.Exit(0 if success else 1)


@work_app.command()
def cd(
    target: str = typer.Argument(..., help="Issue number or worktree name."),
) -> None:
    """Print the path to a worktree (for shell cd). Creates worktree if needed."""
    from ghaiw.services.work_service import find_worktree_path
    from ghaiw.services.work_service import start as do_start

    path = find_worktree_path(target)
    if path:
        typer.echo(str(path))
        raise typer.Exit(0)

    # Worktree doesn't exist — create it (cd_only mode, no AI launch)
    success = do_start(target=target, cd_only=True)
    raise typer.Exit(0 if success else 1)
