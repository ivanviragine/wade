"""Knowledge evidence, usefulness, proposal ownership, and snapshot commands."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Annotated, Literal

import typer

if TYPE_CHECKING:
    from pathlib import Path

    from wade.models.config import KnowledgeConfig

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
    kind: str = typer.Option(
        "note", "--kind", help="Semantic claim kind; session labels are not kinds."
    ),
    claim_key: str | None = typer.Option(None, "--claim-key"),
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
            kind=kind,
            claim_key=claim_key,
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
        None,
        "--min-score",
        help="Explicit minimum historical utility score (up - down); never an assessment.",
    ),
    search: str | None = typer.Option(
        None, "--search", help="Boolean search query (AND, OR, NOT, quotes, parens)."
    ),
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Filter by tag (repeatable, OR semantics)."),
    ] = None,
    raw: bool = typer.Option(False, "--raw", help="Original Markdown with every member."),
    all_history: bool = typer.Option(
        False, "--all-history", help="Include every duplicate member and its own history."
    ),
    no_filter: bool = typer.Option(
        False,
        "--no-filter",
        help="Ignore explicit minimum utility filtering; no default correctness filtering.",
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
            raw=raw,
            all_history=all_history,
        )
    except (ValueError, OSError, RuntimeError) as exc:
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
    console.warn(
        "Deprecated historical utility feedback; votes never establish correctness or hide claims."
    )
    from wade.services.knowledge_ownership import actor

    if not actor(project_root).author:
        console.info(
            "Historical feedback retained as a pending owner-bound proposal; "
            "inspect `knowledge proposals list`."
        )
        return
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
    except (ValueError, OSError, RuntimeError) as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc

    if (
        not result.dirty_paths
        and not result.legacy_migration_pending
        and not result.staged_vote_count
        and not result.pending_proposal_count
        and not result.proposal_error
        # An unreadable staging log is a dirty session, not a clean one: the
        # handoff to main's spool will fail, and this command is how that gets
        # diagnosed (#462 review).
        and not result.staging_error
    ):
        console.success("Knowledge is clean — no uncommitted knowledge or ratings changes.")
        return

    if result.pending_proposal_count:
        console.info(
            f"{result.pending_proposal_count} owner-bound knowledge proposal(s) pending; "
            "inspect `knowledge proposals list`."
        )
    if result.proposal_error:
        console.warn(
            "Knowledge proposal ownership could not be inspected: " + result.proposal_error
        )
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
    if result.staging_error:
        console.warn(
            f"Staged rating votes cannot be read ({result.staging_error}); the handoff to "
            "the main ratings spool will fail until this is repaired."
        )
        if result.staging_path is not None:
            console.hint(
                f"Inspect {result.staging_path} — repair or remove the offending line(s). "
                "Removing the file discards those votes."
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


@knowledge_app.command("check")
def check_entry(
    entry_id: str = typer.Argument(help="Entry ID to assess."),
    outcome: str = typer.Argument(help="current or contradicted."),
    file: Annotated[
        list[str] | None, typer.Option("--file", help="Committed literal path, optionally #Lx-Ly.")
    ] = None,
    evidence_file: str | None = typer.Option(
        None, "--evidence-file", help="JSON array of typed evidence."
    ),
    note: str = typer.Option(..., "--note", help="Why the evidence supports this assessment."),
    replaces: Annotated[
        list[str] | None, typer.Option("--replaces", help="Explicit prior assessment ID.")
    ] = None,
    proposal: str | None = typer.Option(None, "--proposal", help="Accepted matching proposal ID."),
) -> None:
    """Append an immutable assessment bound to the claim and committed proof."""
    from pathlib import Path

    from wade.services.knowledge_assessment import check_entry as assess
    from wade.ui.console import console

    root, config = _knowledge_context()
    if outcome not in {"current", "contradicted"}:
        console.error("Outcome must be current or contradicted")
        raise typer.Exit(1)
    try:
        event = assess(
            root,
            config,
            entry_id,
            "current" if outcome == "current" else "contradicted",
            files=tuple(file or ()),
            evidence_file=Path(evidence_file) if evidence_file else None,
            note=note,
            replaces=tuple(replaces or ()),
            proposal_id=proposal,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
    console.raw(event.model_dump_json(indent=2))


def _knowledge_context() -> tuple[Path, KnowledgeConfig]:
    from pathlib import Path

    from wade.config.loader import load_config
    from wade.ui.console import console

    config = load_config()
    if not config.knowledge.enabled:
        console.error("Knowledge capture is not enabled")
        raise typer.Exit(1)
    return Path(config.project_root) if config.project_root else Path.cwd(), config.knowledge


@knowledge_app.command("feedback")
def feedback_entry(
    entry_id: str,
    value: str = typer.Argument(help="useful or not-useful; never correctness."),
    note: str = typer.Option("", "--note"),
) -> None:
    """Record utility feedback or retain an owner-bound observer proposal."""
    from wade.services.knowledge_assessment import feedback
    from wade.ui.console import console

    root, config = _knowledge_context()
    if value not in {"useful", "not-useful"}:
        console.error("Feedback must be useful or not-useful")
        raise typer.Exit(1)
    try:
        result = feedback(
            root, config, entry_id, "useful" if value == "useful" else "not-useful", note=note
        )
    except (ValueError, OSError, RuntimeError) as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
    console.raw(result if isinstance(result, str) else result.model_dump_json(indent=2))


@knowledge_app.command("identify")
def identify_entry(
    position: int = typer.Argument(help="One-based entry position from get --raw."),
) -> None:
    """Explicitly assign a local ID to a plain historical entry."""
    from wade.services.knowledge_assessment import assign_identity
    from wade.ui.console import console

    root, config = _knowledge_context()
    try:
        console.raw(assign_identity(root, config, position))
    except (ValueError, OSError, RuntimeError) as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc


@knowledge_app.command("reconcile")
def reconcile(
    against: str = typer.Option("HEAD", "--against"),
    check: bool = typer.Option(False, "--check", help="Read-only; also the default."),
    apply: bool = typer.Option(
        False, "--apply", help="Append deterministic exact-duplicate links."
    ),
    json_output: bool = typer.Option(False, "--json"),
    base: str | None = typer.Option(
        None, "--base", help="Effective local PR base for branch impact."
    ),
) -> None:
    """Derive assessment, publication and duplicate state from committed trees."""
    from wade.config.loader import load_config
    from wade.services.knowledge_reconcile import reconcile_knowledge, render_report
    from wade.ui.console import console

    root, config = _knowledge_context()
    if check and apply:
        console.error("Choose either --check or --apply")
        raise typer.Exit(1)
    try:
        report = reconcile_knowledge(
            root,
            config,
            against=against,
            main_ref=load_config(root).project.main_branch,
            effective_base=base,
            apply=apply,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
    console.raw(report.model_dump_json(indent=2) if json_output else render_report(report))
    if not report.valid:
        raise typer.Exit(1)


proposal_app = typer.Typer(help="Ignored owner-bound proposals; no automatic tracked delivery.")
knowledge_app.add_typer(proposal_app, name="proposals")


@proposal_app.command("list")
def list_proposals() -> None:
    """List durable pending proposals and auditable dispositions."""
    import json

    from wade.services.knowledge_proposals import dispositions, proposals
    from wade.ui.console import console

    root, _ = _knowledge_context()
    try:
        console.raw(
            json.dumps(
                {
                    "proposals": [p.model_dump(mode="json") for p in proposals(root)],
                    "dispositions": [d.model_dump(mode="json") for d in dispositions(root)],
                },
                indent=2,
            )
        )
    except (ValueError, OSError, RuntimeError) as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc


@proposal_app.command("accept")
def accept_proposal(proposal_id: str) -> None:
    """Accept only matching verified owner/content; a fresh check remains required."""
    from wade.services.knowledge_proposals import accept_proposal as accept
    from wade.ui.console import console

    root, config = _knowledge_context()
    try:
        console.raw(accept(root, config, proposal_id).model_dump_json(indent=2))
    except (ValueError, OSError, RuntimeError) as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc


@proposal_app.command("retain")
def retain_proposal(proposal_id: str, note: str = typer.Option(..., "--note")) -> None:
    """Record an explicit pending disposition without changing claim authority."""
    _proposal_disposition(proposal_id, "retained", note)


@proposal_app.command("discard")
def discard_proposal(proposal_id: str, note: str = typer.Option(..., "--note")) -> None:
    """Record an auditable discard; keep the original proposal bytes."""
    _proposal_disposition(proposal_id, "discarded", note)


def _proposal_disposition(
    proposal_id: str, action: Literal["retained", "discarded"], note: str
) -> None:
    from wade.services.knowledge_proposals import disposition
    from wade.ui.console import console

    root, _ = _knowledge_context()
    try:
        console.raw(disposition(root, proposal_id, action, note).model_dump_json(indent=2))
    except (ValueError, OSError, RuntimeError) as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc


@proposal_app.command("recover")
def recover_proposals(
    worktree: str = typer.Option(..., "--worktree", help="Original retained parent worktree."),
    from_main: bool = typer.Option(
        False,
        "--from-main",
        help="Recover this parent's explicit pending handoff IDs; preserve main bytes.",
    ),
) -> None:
    """Archive a legacy transport under its original owner, preserving all bytes."""
    import json
    from pathlib import Path

    from wade.services.knowledge_proposals import recover_legacy
    from wade.ui.console import console

    root, config = _knowledge_context()
    try:
        result = recover_legacy(root, Path(worktree).resolve(), config, from_main=from_main)
        console.raw(json.dumps([proposal.model_dump(mode="json") for proposal in result], indent=2))
    except (ValueError, OSError, RuntimeError) as exc:
        console.error(str(exc))
        raise typer.Exit(1) from exc
