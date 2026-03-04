"""Admin commands — init, update, deinit, check, check-config, shell-init."""

from __future__ import annotations

import os
from pathlib import Path

import typer

admin_app = typer.Typer()


@admin_app.command()
def init(
    ai: str | None = typer.Option(None, "--ai", help="AI tool to configure."),
    non_interactive: bool = typer.Option(False, "--yes", "-y", help="Non-interactive mode."),
) -> None:
    """Initialize wade in the current project."""
    from wade.services.init_service import init as do_init

    success = do_init(
        project_root=Path.cwd(),
        ai_tool=ai,
        non_interactive=non_interactive,
    )
    raise typer.Exit(0 if success else 1)


@admin_app.command()
def update(
    skip_self_upgrade: bool = typer.Option(
        False,
        "--skip-self-upgrade",
        help="Skip source-version self-upgrade check.",
    ),
) -> None:
    """Re-sync managed files from newer wade version."""
    from wade.services.init_service import update as do_update

    success = do_update(
        project_root=Path.cwd(),
        skip_self_upgrade=skip_self_upgrade,
    )
    raise typer.Exit(0 if success else 1)


@admin_app.command()
def deinit(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt."),
) -> None:
    """Remove wade from the current project."""
    from wade.services.init_service import deinit as do_deinit

    success = do_deinit(project_root=Path.cwd(), force=force)
    raise typer.Exit(0 if success else 1)


@admin_app.command()
def check() -> None:
    """Verify worktree safety for AI agents.

    Exit codes:
      0  IN_WORKTREE       — safe to work
      1  NOT_IN_GIT_REPO   — not inside a git repository
      2  IN_MAIN_CHECKOUT  — unsafe for agent work
    """
    from wade.services.check_service import check_worktree

    result = check_worktree(Path.cwd())
    typer.echo(result.format_output())
    raise typer.Exit(result.exit_code)


@admin_app.command("check-config")
def check_config() -> None:
    """Validate .wade.yml with field-level errors.

    Exit codes:
      0  valid config
      1  config not found
      3  invalid config
    """
    from wade.services.check_service import validate_config

    result = validate_config(Path.cwd())
    typer.echo(result.format_output())
    raise typer.Exit(result.exit_code)


@admin_app.command("shell-init")
def shell_init(
    shell: str | None = typer.Option(None, "--shell", help="Target shell: bash, zsh, fish"),
) -> None:
    """Output a shell function wrapper for eval.

    Usage: eval "$(wade shell-init)"

    The Problem: When you run 'wade work cd <n>', the command runs in a
    subprocess and prints a directory path. But a subprocess cannot change
    your shell's working directory — only the shell itself can do that
    with the 'cd' builtin.

    The Solution: This outputs a shell function that intercepts
    'wade work cd' calls and performs the actual 'cd' in your shell,
    so you end up in the worktree directory instead of staying put.

    Without this, 'wade work cd 5' just prints a path. With it, you'll
    actually change into the worktree.
    """
    # Detect shell from --shell flag or SHELL env var
    shell_env: str = os.environ.get("SHELL", "/bin/bash")
    detected: str = shell if shell else shell_env
    # Print the shell function to stdout — no Rich formatting
    if "fish" in detected:
        print("""\
function wade
  if test (count $argv) -ge 2; and test "$argv[1]" = "work"; and test "$argv[2]" = "cd"
    set -l dir (command wade work cd $argv[3..])
    and builtin cd $dir
  else
    command wade $argv
  end
end""")
    else:
        print("""\
wade() {
  if [[ "${1:-}" == "work" && "${2:-}" == "cd" ]]; then
    local __p
    __p=$(command wade "$@") && cd "$__p"
    return $?
  fi
  command wade "$@"
}""")
    raise typer.Exit(0)
