"""Admin commands — init, update, deinit, check, check-config."""

from __future__ import annotations

from pathlib import Path

import typer

admin_app = typer.Typer()


@admin_app.command()
def init(
    ai: str | None = typer.Option(None, "--ai", help="AI tool to configure."),
    non_interactive: bool = typer.Option(False, "--yes", "-y", help="Non-interactive mode."),
) -> None:
    """Initialize ghaiw in the current project."""
    from ghaiw.services.init_service import init as do_init

    success = do_init(
        project_root=Path.cwd(),
        ai_tool=ai,
        non_interactive=non_interactive,
    )
    raise typer.Exit(0 if success else 1)


@admin_app.command()
def update() -> None:
    """Re-sync managed files from newer ghaiw version."""
    from ghaiw.services.init_service import update as do_update

    success = do_update(project_root=Path.cwd())
    raise typer.Exit(0 if success else 1)


@admin_app.command()
def deinit() -> None:
    """Remove ghaiw from the current project."""
    from ghaiw.services.init_service import deinit as do_deinit

    success = do_deinit(project_root=Path.cwd())
    raise typer.Exit(0 if success else 1)


@admin_app.command()
def check() -> None:
    """Verify worktree safety for AI agents.

    Exit codes:
      0  IN_WORKTREE       — safe to work
      1  NOT_IN_GIT_REPO   — not inside a git repository
      2  IN_MAIN_CHECKOUT  — unsafe for agent work
    """
    from ghaiw.services.check_service import check_worktree

    result = check_worktree(Path.cwd())
    typer.echo(result.format_output())
    raise typer.Exit(result.exit_code)


@admin_app.command("check-config")
def check_config() -> None:
    """Validate .ghaiw.yml with field-level errors.

    Exit codes:
      0  valid config
      1  config not found
      3  invalid config
    """
    from ghaiw.services.check_service import validate_config

    result = validate_config(Path.cwd())
    typer.echo(result.format_output())
    raise typer.Exit(result.exit_code)
