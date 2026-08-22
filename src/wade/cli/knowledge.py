"""Knowledge subcommands — add, get, rate, and tag project learnings."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from pathlib import Path

knowledge_app = typer.Typer(
    help="Project knowledge management.",
)
tag_app = typer.Typer(help="Manage tags on knowledge entries.")
knowledge_app.add_typer(tag_app, name="tag")

VALID_SESSION_TYPES = ("plan", "implementation")


def _refuse_write_in_throwaway_session(project_root: Path, command: str) -> None:
    """Render + exit when a knowledge *write* is refused in a throwaway detached session.

    The git-state policy (is this a throwaway detached-HEAD plan / ``task deps`` worktree?
    is a plan dir present?) lives in :func:`knowledge_service.refusal_for_throwaway_write`;
    this CLI helper only renders the returned domain result and performs the Typer exit.
    """
    from wade.services.knowledge_service import refusal_for_throwaway_write
    from wade.ui.console import console

    refusal = refusal_for_throwaway_write(project_root, command)
    if refusal is None:
        return
    console.error(
        "This worktree is discarded at session end and has no PR to carry the edit, "
        f"so `{refusal.command}` is unavailable here."
    )
    if refusal.plan_hint:
        console.hint(
            "Record the learning in the plan file; the implementation session will capture it."
        )
    raise typer.Exit(1)


@knowledge_app.command()
def add(
    session: str = typer.Option(..., "--session", "-s", help="Session type (plan/implementation)."),
    issue: str | None = typer.Option(None, "--issue", "-i", help="Issue number."),
    supersedes: str | None = typer.Option(
        None, "--supersedes", help="Entry ID that this new entry supersedes."
    ),
    tag: Annotated[
        list[str] | None, typer.Option("--tag", help="Tag for the entry (repeatable).")
    ] = None,
) -> None:
    """Add a knowledge entry (reads content from stdin)."""
    from pathlib import Path

    from wade.config.loader import load_config
    from wade.services.knowledge_service import (
        append_knowledge,
        find_entry_id,
        record_supersede,
        resolve_canonical_knowledge_path,
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
    # `add` writes an entry that must ride to origin in a PR. A throwaway
    # detached-HEAD plan/deps worktree has none, so refuse (see helper).
    _refuse_write_in_throwaway_session(project_root, "wade knowledge add")
    try:
        knowledge_path = resolve_canonical_knowledge_path(project_root, config.knowledge)
        if supersedes and not find_entry_id(knowledge_path, supersedes):
            console.error(f"Entry ID '{supersedes}' not found in knowledge file.")
            raise typer.Exit(1)

        result = append_knowledge(
            project_root=project_root,
            config=config.knowledge,
            content=content,
            session_type=session,
            issue_ref=issue,
            tags=tag,
        )

        if supersedes:
            ratings_path = resolve_ratings_path(knowledge_path)
            record_supersede(ratings_path, supersedes, result.entry_id)
            console.success(
                f"Knowledge entry {result.entry_id} added to {result.path} "
                f"(supersedes {supersedes})"
            )
        else:
            console.success(f"Knowledge entry {result.entry_id} added to {result.path}")
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
        None, "--min-score", help="Minimum net score (up - down) to include. Bypasses auto-filter."
    ),
    search: str | None = typer.Option(
        None, "--search", help="Boolean search query (AND, OR, NOT, quotes, parens)."
    ),
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Filter by tag (repeatable, OR semantics)."),
    ] = None,
    no_filter: bool = typer.Option(
        False, "--no-filter", help="Disable all score filtering (auto-filter and min-score)."
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
        result = get_annotated_knowledge(
            project_root,
            config.knowledge,
            min_score=min_score,
            search_query=search,
            filter_tags=tag,
            no_filter=no_filter,
        )
    except (ValueError, OSError) as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
    if result.content is None:
        print("No knowledge file found.", file=sys.stderr)
        raise typer.Exit(0)

    # Check if search or tag filters returned no results
    if (search or tag) and result.entries_count == 0:
        print("No entries matched your search.", file=sys.stderr)
        raise typer.Exit(0)

    console.raw(result.content)


@knowledge_app.command()
def rate(
    entry_id: str = typer.Argument(help="Entry ID to rate."),
    direction: str = typer.Argument(help="Rating direction: up, down, or stale."),
) -> None:
    """Rate a knowledge entry (up, down, or stale)."""
    from pathlib import Path

    from wade.config.loader import load_config
    from wade.services.knowledge_service import (
        find_entry_id,
        record_rating_for_session,
        resolve_knowledge_path,
    )
    from wade.ui.console import console

    if direction not in ("up", "down", "stale"):
        console.error(f"Invalid direction '{direction}'. Must be 'up', 'down', or 'stale'.")
        raise typer.Exit(1)

    config = load_config()
    if not config.knowledge.enabled:
        console.error("Knowledge capture is not enabled. Run `wade init` to enable it.")
        raise typer.Exit(1)

    project_root = Path(config.project_root) if config.project_root else Path.cwd()
    try:
        # A detached plan/deps worktree has the committed knowledge snapshot in
        # its own tree. Validate there so an otherwise-contained vote never
        # needs even read access to the main checkout before it can be staged.
        knowledge_path = resolve_knowledge_path(project_root, config.knowledge)
        if not find_entry_id(knowledge_path, entry_id):
            console.error(f"Entry ID '{entry_id}' not found in knowledge file.")
            raise typer.Exit(1)

        record_rating_for_session(project_root, config.knowledge, entry_id, direction)
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
    if direction == "stale":
        console.success(f"Recorded stale vote for entry {entry_id}")
    else:
        symbol = "+" if direction == "up" else "-"
        console.success(f"Recorded {symbol}1 for entry {entry_id}")


@knowledge_app.command()
def status() -> None:
    """Report local knowledge/ratings state, including detached staged votes."""
    from pathlib import Path

    from wade.config.loader import load_config
    from wade.services.knowledge_service import knowledge_status
    from wade.ui.console import console

    config = load_config()
    if not config.knowledge.enabled:
        console.error("Knowledge capture is not enabled. Run `wade init` to enable it.")
        raise typer.Exit(1)

    project_root = Path(config.project_root) if config.project_root else Path.cwd()
    try:
        result = knowledge_status(project_root, config.knowledge)
    except (ValueError, OSError) as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc

    if (
        not result.dirty_paths
        and not result.legacy_migration_pending
        and not result.staged_vote_count
    ):
        console.success("Knowledge is clean — no uncommitted knowledge or ratings changes.")
        return

    if result.dirty_paths:
        console.warn("Uncommitted knowledge/ratings changes:")
        for line in result.dirty_paths:
            console.detail(line)
    if result.legacy_migration_pending:
        console.info(
            "A legacy ratings YAML file is pending migration to .ratings.jsonl "
            "(converts on the next `wade knowledge rate`)."
        )
    if result.staged_vote_count:
        assert result.staging_path is not None
        console.info(
            f"{result.staged_vote_count} detached-session rating vote(s) staged at "
            f"{result.staging_path}; wade will flush them before this session is removed."
        )


@knowledge_app.command()
def enable(
    path: str | None = typer.Option(
        None, "--path", help="Custom path for knowledge file (relative to project root)."
    ),
) -> None:
    """Enable knowledge capture and optionally set the knowledge file path."""
    from pathlib import Path

    from wade.services.knowledge_service import enable_knowledge
    from wade.ui.console import console

    project_root = Path.cwd()
    try:
        enable_knowledge(project_root, path=path)
        if path:
            console.success(f"Knowledge capture enabled with path: {path}")
        else:
            console.success("Knowledge capture enabled")
    except FileNotFoundError as exc:
        console.error(str(exc))
        console.hint("Run `wade init` to initialize the project")
        raise typer.Exit(1) from exc
    except ValueError as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
    except OSError as exc:
        console.error(f"Failed to enable knowledge: {exc}")
        raise typer.Exit(1) from exc


@knowledge_app.command()
def disable() -> None:
    """Disable knowledge capture."""
    from pathlib import Path

    from wade.services.knowledge_service import disable_knowledge
    from wade.ui.console import console

    project_root = Path.cwd()
    try:
        disable_knowledge(project_root)
        console.success("Knowledge capture disabled")
    except FileNotFoundError as exc:
        console.error(str(exc))
        console.hint("Run `wade init` to initialize the project")
        raise typer.Exit(1) from exc
    except ValueError as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
    except OSError as exc:
        console.error(f"Failed to disable knowledge: {exc}")
        raise typer.Exit(1) from exc


@tag_app.command("add")
def tag_add(
    entry_id: str = typer.Argument(help="Entry ID to tag."),
    tag: str = typer.Argument(help="Tag to add."),
) -> None:
    """Add a tag to an existing knowledge entry."""
    from pathlib import Path

    from wade.config.loader import load_config
    from wade.services.knowledge_service import add_tag_to_entry, resolve_canonical_knowledge_path
    from wade.ui.console import console

    config = load_config()
    if not config.knowledge.enabled:
        console.error("Knowledge capture is not enabled. Run `wade init` to enable it.")
        raise typer.Exit(1)

    project_root = Path(config.project_root) if config.project_root else Path.cwd()
    _refuse_write_in_throwaway_session(project_root, "wade knowledge tag add")
    try:
        knowledge_path = resolve_canonical_knowledge_path(project_root, config.knowledge)
        add_tag_to_entry(knowledge_path, entry_id, tag)
        console.success(f"Tag '{tag}' added to entry {entry_id}")
    except typer.Exit:
        raise
    except ValueError as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
    except OSError as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc


@tag_app.command("remove")
def tag_remove(
    entry_id: str = typer.Argument(help="Entry ID to remove tag from."),
    tag: str = typer.Argument(help="Tag to remove."),
) -> None:
    """Remove a tag from an existing knowledge entry."""
    from pathlib import Path

    from wade.config.loader import load_config
    from wade.services.knowledge_service import (
        remove_tag_from_entry,
        resolve_canonical_knowledge_path,
    )
    from wade.ui.console import console

    config = load_config()
    if not config.knowledge.enabled:
        console.error("Knowledge capture is not enabled. Run `wade init` to enable it.")
        raise typer.Exit(1)

    project_root = Path(config.project_root) if config.project_root else Path.cwd()
    _refuse_write_in_throwaway_session(project_root, "wade knowledge tag remove")
    try:
        knowledge_path = resolve_canonical_knowledge_path(project_root, config.knowledge)
        remove_tag_from_entry(knowledge_path, entry_id, tag)
        console.success(f"Tag '{tag}' removed from entry {entry_id}")
    except typer.Exit:
        raise
    except ValueError as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
    except OSError as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc


@tag_app.command("list")
def tag_list(
    entry_id: str | None = typer.Argument(None, help="Entry ID (omit to list all tags)."),
) -> None:
    """List tags — all unique tags or tags for a specific entry."""
    from pathlib import Path

    from wade.config.loader import load_config
    from wade.services.knowledge_service import list_tags, resolve_knowledge_path
    from wade.ui.console import console

    config = load_config()
    if not config.knowledge.enabled:
        console.error("Knowledge capture is not enabled. Run `wade init` to enable it.")
        raise typer.Exit(1)

    project_root = Path(config.project_root) if config.project_root else Path.cwd()
    try:
        knowledge_path = resolve_knowledge_path(project_root, config.knowledge)
        result = list_tags(knowledge_path, entry_id=entry_id)
        if not result:
            print("No tags found.", file=sys.stderr)
        else:
            for t in result:
                print(t)
    except typer.Exit:
        raise
    except ValueError as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
    except OSError as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
