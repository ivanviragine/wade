"""Worktree subcommands — list, remove, cd."""

from __future__ import annotations

import typer

worktree_app = typer.Typer(
    help="Manage worktrees (list, remove, cd).",
    invoke_without_command=True,
)


@worktree_app.callback()
def worktree_callback(ctx: typer.Context) -> None:
    """Show interactive menu when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return

    from wade.ui import prompts

    menu_items = [
        "List active worktrees",
        "Remove a worktree",
        "Navigate to a worktree",
    ]
    hints = [
        "worktree list",
        "worktree remove",
        "worktree cd",
    ]

    idx = prompts.menu("wade worktree", menu_items, hints=hints)

    subcommands = ["list", "remove", "cd"]
    selected = subcommands[idx]

    if selected == "list":
        from wade.services.work_service import list_sessions as do_list

        do_list()
        raise typer.Exit(0)
    elif selected == "remove":
        target = prompts.input_prompt("Issue number or worktree name")
        if target:
            from wade.services.work_service import remove as do_remove

            success = do_remove(target=target)
            raise typer.Exit(0 if success else 1)
    elif selected == "cd":
        target = prompts.input_prompt("Issue number or worktree name")
        if target:
            from wade.services.work_service import find_worktree_path

            path = find_worktree_path(target)
            if path:
                typer.echo(str(path))
            raise typer.Exit(0 if path else 1)

    raise typer.Exit(0)


@worktree_app.command("list")
def list_worktrees(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
    show_all: bool = typer.Option(False, "--all", help="Show all worktrees including main."),
) -> None:
    """List active worktrees."""
    from wade.services.work_service import list_sessions as do_list

    do_list(show_all=show_all, json_output=json_output)
    raise typer.Exit(0)


@worktree_app.command()
def remove(
    target: str | None = typer.Argument(None, help="Issue number or worktree name."),
    stale: bool = typer.Option(False, "--stale", help="Remove all stale worktrees."),
    show_all: bool = typer.Option(False, "--all", hidden=True, help="Alias for --stale."),
    force: bool = typer.Option(False, "--force", help="Skip confirmation."),
) -> None:
    """Remove a worktree."""
    from wade.services.work_service import list_sessions
    from wade.services.work_service import remove as do_remove
    from wade.ui import prompts
    from wade.ui.console import console

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


@worktree_app.command()
def cd(
    target: str = typer.Argument(..., help="Issue number or worktree name."),
) -> None:
    """Print the path to a worktree (for shell cd). Creates worktree if needed."""
    from wade.services.work_service import find_worktree_path
    from wade.services.work_service import start as do_start

    path = find_worktree_path(target)
    if path:
        typer.echo(str(path))
        raise typer.Exit(0)

    # Worktree doesn't exist — create it (cd_only mode, no AI launch)
    success = do_start(target=target, cd_only=True)
    raise typer.Exit(0 if success else 1)
