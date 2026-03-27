"""Knowledge subcommands — add, get, and rate project learnings."""

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
    supersedes: str | None = typer.Option(
        None, "--supersedes", help="Entry ID that this new entry supersedes."
    ),
) -> None:
    """Add a knowledge entry (reads content from stdin)."""
    from pathlib import Path

    from wade.config.loader import load_config
    from wade.services.knowledge_service import (
        append_knowledge,
        find_entry_id,
        record_supersede,
        resolve_knowledge_path,
        resolve_ratings_path,
    )
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
    try:
        knowledge_path = resolve_knowledge_path(project_root, config.knowledge)
        if supersedes and not find_entry_id(knowledge_path, supersedes):
            console.error(f"Entry ID '{supersedes}' not found in knowledge file.")
            raise typer.Exit(1)

        result = append_knowledge(
            project_root=project_root,
            config=config.knowledge,
            content=content,
            session_type=session,
            issue_ref=issue,
        )

        if supersedes:
            ratings_path = resolve_ratings_path(knowledge_path)
            record_supersede(ratings_path, supersedes, result.entry_id)
            console.success(
                f"Knowledge entry {result.entry_id} added to {result.path.name} "
                f"(supersedes {supersedes})"
            )
        else:
            console.success(f"Knowledge entry {result.entry_id} added to {result.path.name}")
    except typer.Exit:
        raise
    except ValueError as exc:
        console.error_with_fix(
            str(exc),
            "Update .wade.yml so knowledge.path points to a file inside the current project",
        )
        raise typer.Exit(1) from exc
    except OSError as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc


@knowledge_app.command()
def get(
    min_score: int | None = typer.Option(
        None, "--min-score", help="Minimum net score (up - down) to include."
    ),
) -> None:
    """Print the project knowledge file to stdout."""
    from pathlib import Path

    from wade.config.loader import load_config
    from wade.services.knowledge_service import get_annotated_knowledge
    from wade.ui.console import console

    config = load_config()
    if not config.knowledge.enabled:
        console.error("Knowledge capture is not enabled. Run `wade init` to enable it.")
        raise typer.Exit(1)

    project_root = Path(config.project_root) if config.project_root else Path.cwd()
    try:
        content = get_annotated_knowledge(project_root, config.knowledge, min_score=min_score)
    except (ValueError, OSError) as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
    if content is None:
        print("No knowledge file found.", file=sys.stderr)
        raise typer.Exit(0)
    console.raw(content)


@knowledge_app.command()
def rate(
    entry_id: str = typer.Argument(help="Entry ID to rate."),
    direction: str = typer.Argument(help="Rating direction: up or down."),
) -> None:
    """Rate a knowledge entry (thumbs up or down)."""
    from pathlib import Path

    from wade.config.loader import load_config
    from wade.services.knowledge_service import (
        find_entry_id,
        record_rating,
        resolve_knowledge_path,
        resolve_ratings_path,
    )
    from wade.ui.console import console

    if direction not in ("up", "down"):
        console.error(f"Invalid direction '{direction}'. Must be 'up' or 'down'.")
        raise typer.Exit(1)

    config = load_config()
    if not config.knowledge.enabled:
        console.error("Knowledge capture is not enabled. Run `wade init` to enable it.")
        raise typer.Exit(1)

    project_root = Path(config.project_root) if config.project_root else Path.cwd()
    try:
        knowledge_path = resolve_knowledge_path(project_root, config.knowledge)
        if not find_entry_id(knowledge_path, entry_id):
            console.error(f"Entry ID '{entry_id}' not found in knowledge file.")
            raise typer.Exit(1)

        ratings_path = resolve_ratings_path(knowledge_path)
        record_rating(ratings_path, entry_id, direction)
    except typer.Exit:
        raise
    except ValueError as exc:
        console.error_with_fix(
            str(exc),
            "Update .wade.yml so knowledge.path points to a file inside the current project",
        )
        raise typer.Exit(1) from exc
    except OSError as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
    symbol = "+" if direction == "up" else "-"
    console.success(f"Recorded {symbol}1 for entry {entry_id}")
