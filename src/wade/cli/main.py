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
        # Rewrite "wade 42 [flags]" → "wade smart-start 42 [flags]" so Typer
        # dispatches normally: the main callback runs (logging, update nag,
        # version banner) and any extra flags (--detach, --ai, etc.) are parsed.
        # smart-start detects PR state and routes to implement or address-reviews.
        sys.argv = [sys.argv[0], "smart-start", *sys.argv[1:]]
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
        "Address PR reviews",
        "Plan tasks with AI",
        "Create a new task",
        "List worktrees",
        "Show help",
    ]
    hints = [
        "implement / i",
        "address-reviews / r",
        "plan / p",
        "task create",
        "worktree list",
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
    elif idx == 1:  # Address reviews
        from wade.services.task_service import prompt_task_selection

        target = prompt_task_selection("Issue number")
        if target:
            from wade.services.review_service import start as do_review

            success = do_review(target=target)
            raise typer.Exit(0 if success else 1)
    elif idx == 2:  # Plan with AI
        from wade.services.plan_service import plan

        plan()
    elif idx == 3:  # Create task
        from wade.services.task_service import create_interactive

        create_interactive()
    elif idx == 4:  # List worktrees
        from wade.services.work_service import list_sessions

        list_sessions()
    elif idx == 5:  # Help
        typer.echo(app.info.help or "")
        raise typer.Exit(0)

    raise typer.Exit(0)


# --- Top-level commands ---

from wade.cli.autocomplete import complete_ai_tools, complete_effort_levels, complete_models  # noqa: E402, I001


@app.command("plan", rich_help_panel="Workflow")
def plan_cmd(
    issue: str | None = typer.Option(None, "--issue", "-i", help="Plan an existing issue by ID."),
    ai: str | None = typer.Option(
        None, "--ai", help="AI tool to use for planning.", autocompletion=complete_ai_tools
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
) -> None:
    """Start a planning session with AI."""
    from wade.services.plan_service import plan as do_plan

    success = do_plan(
        ai_tool=ai,
        model=model,
        issue_id=issue,
        ai_explicit=ai is not None,
        model_explicit=model is not None,
        effort=effort,
        effort_explicit=effort is not None,
    )
    raise typer.Exit(0 if success else 1)


@app.command("implement", rich_help_panel="Workflow")
def implement_cmd(
    target: str = typer.Argument(..., help="Issue number."),
    ai: list[str] | None = typer.Option(  # noqa: B008
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
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
    detach: bool = typer.Option(False, "--detach", help="Launch AI in a new terminal."),
    cd_only: bool = typer.Option(
        False, "--cd", help="Create worktree and print path (no AI launch)."
    ),
) -> None:
    """Start an implementation session on an issue."""
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
        ai_explicit=selected_ai is not None,
        model_explicit=model is not None,
        effort=effort,
        effort_explicit=effort is not None,
    )
    raise typer.Exit(0 if success else 1)


_BATCH_NUMBERS = typer.Argument(None, help="Issue numbers to work on.")


@app.command("implement-batch", rich_help_panel="Workflow")
def implement_batch_cmd(
    numbers: list[int] = _BATCH_NUMBERS,
    ai: str | None = typer.Option(
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
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
) -> None:
    """Start parallel implementation sessions. [beta]"""
    from wade.services.work_service import batch as do_batch
    from wade.ui import prompts
    from wade.ui.console import console

    console.warn("Batch mode is in beta — please report issues.")
    console.empty()

    # Interactive picker if no numbers provided
    if not numbers and prompts.is_tty():
        from wade.services.task_service import prompt_multi_task_selection

        selected_ids = prompt_multi_task_selection("Select issues for batch work")
        if not selected_ids:
            console.error("No issues selected.")
            raise typer.Exit(1)

        numbers = [int(id_str) for id_str in selected_ids]

    if not numbers:
        console.error("Provide at least one issue number.")
        raise typer.Exit(1)

    issue_ids = [str(n) for n in numbers]
    success = do_batch(
        issue_numbers=issue_ids,
        ai_tool=ai,
        model=model,
        ai_explicit=ai is not None,
        model_explicit=model is not None,
        effort=effort,
        effort_explicit=effort is not None,
    )
    raise typer.Exit(0 if success else 1)


@app.command("address-reviews", rich_help_panel="Workflow")
def address_reviews_cmd(
    target: str = typer.Argument(..., help="Issue number."),
    ai: list[str] | None = typer.Option(  # noqa: B008
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
    detach: bool = typer.Option(False, "--detach", help="Launch AI in a new terminal."),
) -> None:
    """Address PR review comments."""
    from wade.services.review_service import start as do_start
    from wade.ui import prompts

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
        ai_explicit=selected_ai is not None,
        model_explicit=model is not None,
    )
    raise typer.Exit(0 if success else 1)


@app.command("cd", rich_help_panel="Workflow")
def cd_cmd(
    target: str = typer.Argument(..., help="Issue number or worktree name."),
) -> None:
    """Navigate to a worktree (requires shell integration)."""
    from wade.services.work_service import find_worktree_path
    from wade.services.work_service import start as do_start

    path = find_worktree_path(target)
    if path:
        typer.echo(str(path))
        raise typer.Exit(0)

    # Worktree doesn't exist — create it (cd_only mode, no AI launch)
    success = do_start(target=target, cd_only=True)
    raise typer.Exit(0 if success else 1)


@app.command("smart-start", rich_help_panel="Workflow")
def smart_start_cmd(
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
    """Smart start — routes to implement or address-reviews."""
    from wade.services.smart_start import smart_start

    selected_ai: str | None = None
    if ai and len(ai) > 1:
        from wade.ui import prompts

        idx = prompts.select("Select AI tool", ai)
        selected_ai = ai[idx]
    elif ai and len(ai) == 1:
        selected_ai = ai[0]

    success = smart_start(
        target=target,
        ai_tool=selected_ai,
        model=model,
        detach=detach,
        cd_only=cd_only,
        ai_explicit=selected_ai is not None,
        model_explicit=model is not None,
    )
    raise typer.Exit(0 if success else 1)


# --- Hidden short aliases ---


@app.command("p", hidden=True)
def plan_alias(
    issue: str | None = typer.Option(None, "--issue", "-i", help="Plan an existing issue by ID."),
    ai: str | None = typer.Option(
        None, "--ai", help="AI tool to use for planning.", autocompletion=complete_ai_tools
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
) -> None:
    """Alias for plan."""
    plan_cmd(issue=issue, ai=ai, model=model, effort=effort)


@app.command("i", hidden=True)
def implement_alias(
    target: str = typer.Argument(..., help="Issue number."),
    ai: list[str] | None = typer.Option(  # noqa: B008
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
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
    detach: bool = typer.Option(False, "--detach", help="Launch AI in a new terminal."),
    cd_only: bool = typer.Option(
        False, "--cd", help="Create worktree and print path (no AI launch)."
    ),
) -> None:
    """Alias for implement."""
    implement_cmd(target=target, ai=ai, model=model, effort=effort, detach=detach, cd_only=cd_only)


@app.command("r", hidden=True)
def reviews_alias(
    target: str = typer.Argument(..., help="Issue number."),
    ai: list[str] | None = typer.Option(  # noqa: B008
        None, "--ai", help="AI tool to use.", autocompletion=complete_ai_tools
    ),
    model: str | None = typer.Option(
        None, "--model", help="AI model to use.", autocompletion=complete_models
    ),
    detach: bool = typer.Option(False, "--detach", help="Launch AI in a new terminal."),
) -> None:
    """Alias for address-reviews."""
    address_reviews_cmd(target=target, ai=ai, model=model, detach=detach)


# --- Register subcommand groups ---

from wade.cli.address_reviews_session import reviews_session_app  # noqa: E402
from wade.cli.admin import admin_app  # noqa: E402
from wade.cli.implementation_session import impl_session_app  # noqa: E402
from wade.cli.plan_session import plan_session_app  # noqa: E402
from wade.cli.task import task_app  # noqa: E402
from wade.cli.worktree import worktree_app  # noqa: E402

app.add_typer(
    task_app,
    name="task",
    help="Manage GitHub Issues (create, list, read, update, close, deps).",
    rich_help_panel="Task Management",
)
app.add_typer(task_app, name="tasks", hidden=True)  # alias
app.add_typer(
    worktree_app,
    name="worktree",
    help="Manage worktrees (list, remove, cd).",
    rich_help_panel="Worktree Management",
)

app.add_typer(
    plan_session_app,
    name="plan-session",
    help="Plan session commands (done).",
    rich_help_panel="AI Session — Plan",
)
app.add_typer(
    impl_session_app,
    name="implementation-session",
    help="Implementation session commands (check, sync, done).",
    rich_help_panel="AI Session — Implementation",
)
app.add_typer(
    reviews_session_app,
    name="address-reviews-session",
    help="Review session commands (check, sync, done, fetch, resolve).",
    rich_help_panel="AI Session — Address Reviews",
)

# Admin commands are registered directly on the root app
for command in admin_app.registered_commands:
    command.rich_help_panel = "Setup"
    app.registered_commands.append(command)
