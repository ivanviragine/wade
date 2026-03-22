"""Knowledge subcommands — add project learnings."""

from __future__ import annotations

import sys

import typer

knowledge_app = typer.Typer(
    help="Project knowledge management.",
)


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

    project_root = Path.cwd()
    path = append_knowledge(
        project_root=project_root,
        config=config.knowledge,
        content=content,
        session_type=session,
        issue_ref=issue,
    )
    console.success(f"Knowledge entry added to {path.name}")
