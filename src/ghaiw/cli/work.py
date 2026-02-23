"""Work subcommands — start, done, sync, list, batch, remove, cd."""

from __future__ import annotations

import typer

work_app = typer.Typer(
    help="Work session lifecycle.",
    no_args_is_help=True,
)


@work_app.command()
def start(
    target: str = typer.Argument(..., help="Issue number or plan file path."),
    ai: str | None = typer.Option(None, "--ai", help="AI tool to use."),
    model: str | None = typer.Option(None, "--model", help="AI model to use."),
) -> None:
    """Start a work session on an issue."""
    from ghaiw.services.work_service import start as do_start

    success = do_start(target=target, ai_tool=ai, model=model)
    raise typer.Exit(0 if success else 1)


@work_app.command()
def done(
    no_close: bool = typer.Option(
        False, "--no-close", help="Don't close the issue on merge."
    ),
    draft: bool = typer.Option(False, "--draft", help="Create PR as draft."),
) -> None:
    """Finalize work — push branch and create PR (or direct merge)."""
    from ghaiw.services.work_service import done as do_done

    success = do_done(no_close=no_close, draft=draft)
    raise typer.Exit(0 if success else 1)


@work_app.command()
def sync(
    json_output: bool = typer.Option(
        False, "--json", help="Output structured JSON events."
    ),
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
    # Exit codes: 0=success, 2=conflict, 1=other error
    if result.success:
        raise typer.Exit(0)
    elif result.conflicts:
        raise typer.Exit(2)
    else:
        raise typer.Exit(1)


@work_app.command("list")
def list_sessions(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
    show_all: bool = typer.Option(
        False, "--all", help="Show all worktrees including main."
    ),
) -> None:
    """List active work sessions / worktrees."""
    from ghaiw.services.work_service import list_sessions as do_list

    do_list(show_all=show_all, json_output=json_output)
    raise typer.Exit(0)


@work_app.command()
def batch(
    numbers: list[int] = typer.Argument(None, help="Issue numbers to work on."),
    ai: str | None = typer.Option(None, "--ai", help="AI tool to use."),
) -> None:
    """Start parallel work sessions for multiple issues."""
    from ghaiw.services.work_service import batch as do_batch
    from ghaiw.ui.console import console

    if not numbers:
        console.error("Provide at least one issue number.")
        raise typer.Exit(1)

    issue_ids = [str(n) for n in numbers]
    success = do_batch(issue_numbers=issue_ids, ai_tool=ai)
    raise typer.Exit(0 if success else 1)


@work_app.command()
def remove(
    target: str | None = typer.Argument(
        None, help="Issue number or worktree name."
    ),
    stale: bool = typer.Option(False, "--stale", help="Remove all stale worktrees."),
    force: bool = typer.Option(False, "--force", help="Skip confirmation."),
) -> None:
    """Remove a worktree."""
    from ghaiw.services.work_service import remove as do_remove

    success = do_remove(target=target, stale=stale, force=force)
    raise typer.Exit(0 if success else 1)


@work_app.command()
def cd(
    target: str = typer.Argument(..., help="Issue number or worktree name."),
) -> None:
    """Print the path to a worktree (for shell cd)."""
    from ghaiw.services.work_service import find_worktree_path

    path = find_worktree_path(target)
    if path:
        typer.echo(str(path))
        raise typer.Exit(0)
    else:
        typer.echo(f"No worktree found for: {target}", err=True)
        raise typer.Exit(1)
