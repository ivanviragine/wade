"""Root CLI application — entry point, interactive menu, and subcommand registration."""

from __future__ import annotations

import sys

import typer

import wade
from wade.config.loader import ConfigError

app = typer.Typer(
    name="wade",
    help="AI-agent-driven git workflow management CLI.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=True,
)


def cli_main() -> None:
    """Console entrypoint — wraps ``app()`` to catch ConfigError gracefully."""
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        # Rewrite "wade 42 [flags]" → "wade implement-task 42 [flags]" so Typer
        # dispatches normally: the main callback runs (logging, update nag,
        # version banner) and any extra flags (--detach, --ai, etc.) are parsed.
        sys.argv = [sys.argv[0], "implement-task", *sys.argv[1:]]
    try:
        app()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"wade {wade.__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Print version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output.",
    ),
) -> None:
    """wade — AI-agent-driven git workflow management CLI."""
    import atexit

    from wade.utils.terminal import set_terminal_title
    from wade.utils.update_check import maybe_print_update_hint

    set_terminal_title("wade")

    # Always configure logging (defaults to ERROR level, stderr output).
    # Without this, structlog's default PrintLogger writes to stdout
    # and doesn't filter by level — polluting --json output.
    import wade.logging.setup as log_setup

    log_setup.configure(verbose=verbose)

    # Register background update nag — fires after command output, before shell prompt.
    atexit.register(maybe_print_update_hint, wade.__version__, ctx.invoked_subcommand)

    if ctx.invoked_subcommand is not None:
        if ctx.invoked_subcommand != "shell-init":
            from wade.ui.console import console

            console.err.print(f"  [dim]wade v{wade.__version__}[/]")
        return

    _interactive_main_menu()


def _interactive_main_menu() -> None:
    """Show the main interactive menu when wade is called with no args."""
    from wade.ui import prompts

    menu_items = [
        "Implement a task",
        "List active worktrees",
        "Create a new task",
        "Plan tasks with AI",
        "Show help",
    ]
    hints = [
        "implement-task",
        "work list",
        "new-task",
        "plan-task",
        "--help",
    ]

    idx = prompts.menu(
        "What would you like to do?",
        menu_items,
        hints=hints,
        version=f"wade v{wade.__version__}",
    )

    if idx == 0:  # Implement task
        from wade.services.task_service import prompt_task_selection

        target = prompt_task_selection("Issue number")
        if target:
            from wade.services.work_service import start as do_start

            success = do_start(target=target)
            raise typer.Exit(0 if success else 1)
    elif idx == 1:  # List worktrees
        from wade.services.work_service import list_sessions

        list_sessions()
    elif idx == 2:  # Create task
        from wade.services.task_service import create_interactive

        create_interactive()
    elif idx == 3:  # Plan with AI
        from wade.services.plan_service import plan

        plan()
    elif idx == 4:  # Help
        typer.echo(app.info.help or "")
        raise typer.Exit(0)

    raise typer.Exit(0)


# --- Top-level commands (clean break) ---

from wade.cli.autocomplete import complete_ai_tools, complete_models  # noqa: E402


@app.command("plan-task")
def plan_task_cmd(
    issue: str | None = typer.Option(None, "--issue", "-i", help="Plan an existing issue by ID."),
    ai: str | None = typer.Option(
        None, "--ai", help="AI tool to use for planning.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
) -> None:
    """Plan tasks with AI — creates lightweight issues + draft PRs."""
    from wade.services.plan_service import plan as do_plan

    success = do_plan(ai_tool=ai, model=model, issue_id=issue)
    raise typer.Exit(0 if success else 1)


@app.command("new-task")
def new_task_cmd() -> None:
    """Create a new GitHub Issue interactively."""
    from wade.services.task_service import create_interactive
    from wade.ui.console import console

    task = create_interactive()
    if task:
        console.empty()
        console.info("When you're ready to implement, run:")
        console.detail(f"wade implement-task {task.id}")
    raise typer.Exit(0 if task else 1)


@app.command("implement-task")
def implement_task_cmd(
    target: str = typer.Argument(..., help="Issue number."),
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
    """Start an implementation session on an issue — detects draft PR plans."""
    from wade.services.work_service import start as do_start
    from wade.ui import prompts

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


# Register subcommand groups
from wade.cli.admin import admin_app  # noqa: E402
from wade.cli.task import task_app  # noqa: E402
from wade.cli.work import work_app  # noqa: E402

app.add_typer(task_app, name="task", help="GitHub Issue management.")
app.add_typer(task_app, name="tasks", hidden=True)  # alias
app.add_typer(work_app, name="work", help="Work session lifecycle.")

# Admin commands are registered directly on the root app
for command in admin_app.registered_commands:
    app.registered_commands.append(command)
