"""Owned committed-evidence checks, utility feedback and explicit local identity."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from wade.git import knowledge as git_knowledge
from wade.models.config import KnowledgeConfig
from wade.models.knowledge import AssessmentEvent, Evidence, UtilityEvent
from wade.services import knowledge_ownership, knowledge_proposals
from wade.services.knowledge_store import append_event
from wade.utils.filelock import file_lock
from wade.utils.knowledge_events import body_digest, decode_history
from wade.utils.knowledge_file import (
    ParsedEntry,
    parse_entries,
    resolve_knowledge_path,
    resolve_ratings_path,
    validate_knowledge_text,
)


def _entry(path: Path, entry_id: str) -> ParsedEntry:
    text = path.read_text()
    problems = validate_knowledge_text(text)
    if problems:
        raise ValueError("; ".join(problems))
    matches = [e for e in parse_entries(text) if e.entry_id == entry_id]
    if len(matches) != 1:
        raise ValueError("Knowledge entry ID is missing or ambiguous")
    return matches[0]


@knowledge_ownership.knowledge_operation
def check_entry(
    root: Path,
    config: KnowledgeConfig,
    entry_id: str,
    outcome: Literal["current", "contradicted"],
    *,
    files: tuple[str, ...] = (),
    evidence_file: Path | None = None,
    note: str,
    replaces: tuple[str, ...] = (),
    proposal_id: str | None = None,
) -> AssessmentEvent:
    """Derive proof from HEAD; never assess dirty source or auto-run tests/network."""
    knowledge_ownership.assert_author(root)
    head = git_knowledge.resolve_commit(root, "HEAD")
    evidence: list[Evidence] = []
    for spec in files:
        path, start, end = git_knowledge.parse_file_spec(spec)
        evidence.append(git_knowledge.file_evidence(root, head, path, start, end))
    if evidence_file is not None:
        supplied = TypeAdapter(list[Evidence]).validate_python(
            json.loads(evidence_file.read_text())
        )
        for item in supplied:
            # A supplied file receipt must still be freshly derived locally.
            if item.type == "git_file":
                derived = git_knowledge.file_evidence(
                    root, head, item.path, item.start_line, item.end_line
                )
                if item != derived:
                    raise ValueError("Supplied file evidence does not match committed HEAD")
            elif item.type == "test_run" and item.checked_commit != head:
                raise ValueError(
                    "New test receipts must identify current HEAD; old results stay history"
                )
        evidence.extend(supplied)
    if not evidence:
        raise ValueError("A check requires --file or explicitly typed --evidence-file proof")
    knowledge = resolve_knowledge_path(root, config)
    ratings = resolve_ratings_path(knowledge)
    with file_lock(knowledge):
        if head != git_knowledge.resolve_commit(root, "HEAD"):
            raise ValueError("HEAD changed while gathering evidence; retry the check")
        for item in evidence:
            if item.type == "git_file":
                git_knowledge.assert_source_clean(root, item.path)
        entry = _entry(knowledge, entry_id)
        if proposal_id is not None:
            knowledge_proposals.verify_acceptance(root, config, proposal_id, entry_id)
            if not any(
                d.proposal_id == proposal_id and d.action == "accepted"
                for d in knowledge_proposals.dispositions(root)
            ):
                raise ValueError("Explicitly accept the matching proposal before a fresh check")
        history = decode_history(ratings.read_text() if ratings.exists() else "")
        event = AssessmentEvent(
            event_id=uuid.uuid4().hex,
            entry_id=entry_id,
            body_digest=body_digest(entry),
            outcome=outcome,
            checked_at=datetime.now(UTC),
            note=note,
            evidence=tuple(evidence),
            replaces=replaces,
            proposal_id=proposal_id,
        )
        candidate = decode_history(
            "\n".join([e.model_dump_json() for e in history.events] + [event.model_dump_json()])
        )
        if history.problems or candidate.problems:
            raise ValueError("; ".join((*history.problems, *candidate.problems)))
        append_event(ratings, event.model_dump(mode="json"))
        if proposal_id is not None:
            knowledge_proposals.disposition(
                root, proposal_id, "assessed", "Fresh committed-evidence assessment", event.event_id
            )
        return event


def feedback(
    root: Path,
    config: KnowledgeConfig,
    entry_id: str,
    value: Literal["useful", "not-useful"],
    *,
    note: str = "",
) -> UtilityEvent | str:
    event = UtilityEvent(
        event_id=uuid.uuid4().hex,
        entry_id=entry_id,
        feedback=value,
        recorded_at=datetime.now(UTC),
        note=note,
    )
    path = resolve_knowledge_path(root, config)
    _entry(path, entry_id)
    if not knowledge_ownership.actor(root).author:
        return knowledge_proposals.stage_proposal(
            root, config, entry_id, event.model_dump(mode="json")
        ).proposal_id
    append_event(resolve_ratings_path(path), event.model_dump(mode="json"))
    return event


def assign_identity(root: Path, config: KnowledgeConfig, position: int) -> str:
    """Explicitly identify one previously plain entry by its displayed position."""
    knowledge_ownership.assert_author(root)
    path = resolve_knowledge_path(root, config)
    with file_lock(path):
        text = path.read_text()
        entries = parse_entries(text)
        if position < 1 or position > len(entries):
            raise ValueError("Entry position is outside the knowledge file")
        entry = entries[position - 1]
        if entry.entry_id is not None:
            raise ValueError("Entry already has an identity")
        entry_id = uuid.uuid4().hex[:12]
        heading = entry.raw.splitlines()[0]
        rest = heading.removeprefix("## ")
        replacement = f"## {entry_id} | {datetime.now(UTC):%Y-%m-%d} | {rest}"
        start = text.index(entry.raw)
        path.write_text(text[:start] + replacement + text[start + len(heading) :])
        return entry_id
