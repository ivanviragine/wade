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
    """Initialize ghaiw in the current project."""
    from ghaiw.services.init_service import init as do_init

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
    """Re-sync managed files from newer ghaiw version."""
    from ghaiw.services.init_service import update as do_update

    success = do_update(
        project_root=Path.cwd(),
        skip_self_upgrade=skip_self_upgrade,
    )
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


@admin_app.command("shell-init")
def shell_init(
    shell: str | None = typer.Option(None, "--shell", help="Target shell: bash, zsh, fish"),
) -> None:
    """Output a shell function wrapper for eval.

    Usage: eval "$(ghaiwpy shell-init)"

    Installs a shell function that intercepts `ghaiwpy work cd <n>`
    to perform a real `cd` in the caller's shell.
    """
    # Detect shell from --shell flag or SHELL env var
    shell_env: str = os.environ.get("SHELL", "/bin/bash")
    detected: str = shell if shell else shell_env
    # Print the shell function to stdout — no Rich formatting
    if "fish" in detected:
        print("""\
function ghaiwpy
  if test (count $argv) -ge 2; and test "$argv[1]" = "work"; and test "$argv[2]" = "cd"
    set -l dir (command ghaiwpy work cd $argv[3..])
    and builtin cd $dir
  else
    command ghaiwpy $argv
  end
end""")
    else:
        print("""\
ghaiwpy() {
  if [[ "${1:-}" == "work" && "${2:-}" == "cd" ]]; then
    local __p
    __p=$(command ghaiwpy "$@") && cd "$__p"
    return $?
  fi
  command ghaiwpy "$@"
}""")
    raise typer.Exit(0)


@admin_app.command()
def changelog(
    stdout: bool = typer.Option(
        False,
        "--stdout",
        help="Print to stdout instead of writing CHANGELOG.md.",
    ),
    tag: str | None = typer.Option(
        None,
        "--tag",
        help="Label unreleased commits as this version.",
    ),
) -> None:
    """Generate CHANGELOG.md from conventional commits."""
    import sys as _sys

    # Import the changelog generator from scripts/
    scripts_dir = Path(__file__).resolve().parent.parent.parent.parent / "scripts"
    _sys.path.insert(0, str(scripts_dir))
    try:
        from changelog import generate  # type: ignore
    except ImportError:
        typer.echo("Error: scripts/changelog.py not found.", err=True)
        raise typer.Exit(1) from None
    finally:
        _sys.path.pop(0)

    content = generate(next_tag=tag or "")

    if stdout:
        typer.echo(content, nl=False)
    else:
        changelog_path = Path.cwd() / "CHANGELOG.md"
        changelog_path.write_text(content)
        typer.echo(f"Generated {changelog_path.name}")
    raise typer.Exit(0)
