"""Root CLI application — entry point, interactive menu, and subcommand registration."""

from __future__ import annotations

import typer

import ghaiw

app = typer.Typer(
    name="ghaiwpy",
    help="AI-agent-driven git workflow management CLI.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=True,
)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ghaiwpy {ghaiw.__version__}")
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
    """ghaiwpy — AI-agent-driven git workflow management CLI."""
    from ghaiw.utils.terminal import set_terminal_title

    set_terminal_title("ghaiwpy")

    # Always configure logging (defaults to INFO level, stderr output).
    # Without this, structlog's default PrintLogger writes to stdout
    # and doesn't filter by level — polluting --json output.
    import ghaiw.logging.setup as log_setup

    log_setup.configure(verbose=verbose)

    if ctx.invoked_subcommand is not None:
        return

    _interactive_main_menu()


def _interactive_main_menu() -> None:
    """Show the main interactive menu when ghaiwpy is called with no args."""
    from ghaiw.ui import prompts

    menu_items = [
        "Start working on a task",
        "List active worktrees",
        "Create a new task",
        "Plan tasks with AI",
        "Show help",
    ]
    hints = [
        "work start",
        "work list",
        "task create",
        "task plan",
        "--help",
    ]

    idx = prompts.menu(
        "What would you like to do?",
        menu_items,
        hints=hints,
        version=f"ghaiwpy v{ghaiw.__version__}",
    )

    if idx == 0:  # Start working
        from ghaiw.services.task_service import prompt_task_selection

        target = prompt_task_selection("Issue number")
        if target:
            from ghaiw.services.work_service import start as do_start

            success = do_start(target=target)
            raise typer.Exit(0 if success else 1)
    elif idx == 1:  # List worktrees
        from ghaiw.services.work_service import list_sessions

        list_sessions()
    elif idx == 2:  # Create task
        from ghaiw.services.task_service import create_interactive

        create_interactive()
    elif idx == 3:  # Plan with AI
        from ghaiw.services.plan_service import plan

        plan()
    elif idx == 4:  # Help
        typer.echo(app.info.help or "")
        raise typer.Exit(0)

    raise typer.Exit(0)


# Register subcommand groups
from ghaiw.cli.admin import admin_app  # noqa: E402
from ghaiw.cli.task import task_app  # noqa: E402
from ghaiw.cli.work import work_app  # noqa: E402

app.add_typer(task_app, name="task", help="GitHub Issue CRUD + AI planning.")
app.add_typer(task_app, name="tasks", hidden=True)  # alias
app.add_typer(work_app, name="work", help="Work session lifecycle.")

# Admin commands are registered directly on the root app
for command in admin_app.registered_commands:
    app.registered_commands.append(command)
