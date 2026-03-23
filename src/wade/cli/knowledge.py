"""Knowledge subcommands — add and read project learnings."""

from __future__ import annotations

import sys

import typer

knowledge_app = typer.Typer(
    help="Project knowledge management.",
)

VALID_SESSION_TYPES = ("plan", "implementation")


@knowledge_app.command()
def add(
    session: str = typer.Option(..., "--session", "-s", help="Session type (plan/implementation)."),
    issue: str | None = typer.Option(None, "--issue", "-i", help="Issue number."),
) -> None:
    """Add a knowledge entry (reads content from stdin)."""
    from pathlib import Path

    from wade.config.loader import load_config
    from wade.services.knowledge_service import append_knowledge
    from wade.ui.console import console

    if session not in VALID_SESSION_TYPES:
        console.error(
            f"Invalid session type '{session}'. Must be one of: {', '.join(VALID_SESSION_TYPES)}"
        )
        raise typer.Exit(1)

    if sys.stdin.isatty():
        console.error("No content provided. Pipe content via stdin.")
        console.hint('echo "Some learning" | wade knowledge add --session plan --issue 1')
        raise typer.Exit(1)

    content = sys.stdin.read().strip()
    if not content:
        console.error("Empty content — nothing to add.")
        raise typer.Exit(1)

    config = load_config()
    if not config.knowledge.enabled:
        console.warn("Knowledge capture is not enabled. Run `wade init` to enable it.")
        raise typer.Exit(1)

    project_root = Path(config.project_root) if config.project_root else Path.cwd()
    path = append_knowledge(
        project_root=project_root,
        config=config.knowledge,
        content=content,
        session_type=session,
        issue_ref=issue,
    )
    console.success(f"Knowledge entry added to {path.name}")


@knowledge_app.command()
def get() -> None:
    """Print the project knowledge file to stdout."""
    from pathlib import Path

    from wade.config.loader import load_config
    from wade.services.knowledge_service import read_knowledge, resolve_knowledge_path
    from wade.ui.console import console

    config = load_config()
    if not config.knowledge.enabled:
        console.error("Knowledge capture is not enabled. Run `wade init` to enable it.")
        raise typer.Exit(1)

    project_root = Path(config.project_root) if config.project_root else Path.cwd()
    path = resolve_knowledge_path(project_root, config.knowledge)

    if not path.exists():
        print("No knowledge file found.", file=sys.stderr)
        raise typer.Exit(0)

    try:
        content = read_knowledge(project_root, config.knowledge)
    except ValueError as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
    console.raw(content)
