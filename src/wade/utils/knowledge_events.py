"""Pure versioned digests and order-independent knowledge event decoding."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from wade.models.knowledge import (
    AssessmentEvent,
    DecodedHistory,
    DuplicateGroup,
    TypedKnowledgeEvent,
)
from wade.utils.knowledge_file import ParsedEntry

_EVENT: TypeAdapter[TypedKnowledgeEvent] = TypeAdapter(TypedKnowledgeEvent)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_body(content: str) -> str:
    """v1 normalization preserves internal prose/spacing; no semantic guessing."""
    return content.replace("\r\n", "\n").replace("\r", "\n").strip()


def body_digest(entry: ParsedEntry) -> str:
    return digest_text(normalize_body(entry.content))


def entry_kind_key(entry: ParsedEntry) -> tuple[str, str | None]:
    kind, key = "note", None
    for piece in entry.heading_rest.split("|"):
        piece = piece.strip()
        if piece.startswith("kind:"):
            kind = piece[5:].strip() or "note"
        elif piece.startswith("key:"):
            key = piece[4:].strip() or None
    return kind, key


def duplicate_digest(entry: ParsedEntry) -> str:
    kind, key = entry_kind_key(entry)
    return digest_text(canonical_json([1, kind, key, normalize_body(entry.content)]))


def exact_duplicate_groups(entries: list[ParsedEntry]) -> tuple[DuplicateGroup, ...]:
    groups: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        if entry.entry_id is not None:
            groups[duplicate_digest(entry)].append(entry.entry_id)
    return tuple(
        DuplicateGroup(representative_id=ids[0], member_ids=tuple(ids), duplicate_digest=digest)
        for digest, members in sorted(groups.items())
        if len(ids := sorted(set(members))) > 1
    )


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key " + key)
        result[key] = value
    return result


def decode_history(text: str) -> DecodedHistory:
    """Keep legacy feedback readable; reject corruption at authoritative callers.

    No append-order or timestamp tie-breaker is used. Identical durable IDs are
    idempotent; different payloads under one ID are structural conflicts.
    """
    events: dict[str, TypedKnowledgeEvent] = {}
    legacy: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    problems: list[str] = []
    seeded: dict[str, str] = {}
    event: TypedKnowledgeEvent | None
    event_id: str | None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line, object_pairs_hook=_unique_keys)
            if not isinstance(record, dict):
                raise ValueError("event must be a JSON object")
            if "schema_version" in record or "type" in record:
                event = _EVENT.validate_python(record)
                payload = canonical_json(event.model_dump(mode="json"))
                event_id = event.event_id
            else:
                if not isinstance(record.get("id"), str) or not record["id"]:
                    raise ValueError("legacy feedback requires an entry id")
                if record.get("dir") not in {None, "up", "down", "stale"}:
                    raise ValueError("unknown legacy feedback direction")
                if not (
                    record.get("dir") or record.get("seed") is True or "superseded_by" in record
                ):
                    raise ValueError("unrecognized legacy feedback record")
                if record.get("seed") is True:
                    if any(
                        not isinstance(record.get(k, 0), int) or isinstance(record.get(k, 0), bool)
                        for k in ("up", "down", "stale")
                    ):
                        raise ValueError("legacy seed counters must be integers")
                    payload = canonical_json(record)
                    prior = seeded.get(record["id"])
                    if prior is not None and prior != payload:
                        raise ValueError("conflicting legacy seeds for " + record["id"])
                    seeded[record["id"]] = payload
                payload = canonical_json(record)
                event_id = record.get("event_id")
                if event_id is not None and (not isinstance(event_id, str) or not event_id):
                    raise ValueError("legacy event_id must be a nonempty string")
                event = None
            if event_id is not None:
                prior = seen.get(event_id)
                if prior is not None:
                    if prior != payload:
                        raise ValueError(f"conflicting payloads for event_id {event_id}")
                    continue
                seen[event_id] = payload
            if event is not None:
                events[event.event_id] = event
            else:
                legacy.append(record)
        except (ValueError, TypeError, ValidationError) as exc:
            problems.append(f"Knowledge history line {number}: {exc}")
    for event in events.values():
        if isinstance(event, AssessmentEvent):
            for replacement in event.replaces:
                previous_assessment = events.get(replacement)
                if (
                    not isinstance(previous_assessment, AssessmentEvent)
                    or previous_assessment.entry_id != event.entry_id
                ):
                    problems.append(
                        f"Assessment {event.event_id} replaces unavailable/different entry "
                        f"assessment {replacement}"
                    )
    # A replacement cycle cannot silently erase every assessment.
    graph = {e.event_id: set(e.replaces) for e in events.values() if isinstance(e, AssessmentEvent)}
    remaining = dict(graph)
    completed: set[str] = set()
    while remaining:
        ready = {key for key, refs in remaining.items() if refs <= completed}
        if not ready:
            problems.append("Assessment replacement cycle")
            break
        completed.update(ready)
        for key in ready:
            remaining.pop(key)
    return DecodedHistory(
        events=tuple(events[key] for key in sorted(events)),
        legacy_records=tuple(legacy),
        problems=tuple(problems),
    )


def live_assessments(history: DecodedHistory, entry_id: str) -> tuple[AssessmentEvent, ...]:
    assessments = tuple(
        e for e in history.events if isinstance(e, AssessmentEvent) and e.entry_id == entry_id
    )
    replaced = {key for event in assessments for key in event.replaces}
    return tuple(event for event in assessments if event.event_id not in replaced)


def utility_counts(history: DecodedHistory, entry_id: str) -> tuple[int, int, int]:
    from wade.models.knowledge import HistoricalFeedbackEvent, UtilityEvent

    up = down = stale = 0
    seeded: set[str] = set()
    for record in history.legacy_records:
        if record.get("id") != entry_id:
            continue
        if record.get("seed") is True:
            if entry_id in seeded:
                continue
            seeded.add(entry_id)
            up += cast(int, record.get("up", 0))
            down += cast(int, record.get("down", 0))
            stale += cast(int, record.get("stale", 0))
        direction = record.get("dir")
        up += direction == "up"
        down += direction == "down"
        stale += direction == "stale"
    for event in history.events:
        if event.entry_id != entry_id:
            continue
        if isinstance(event, UtilityEvent):
            up += event.feedback == "useful"
            down += event.feedback == "not-useful"
        elif isinstance(event, HistoricalFeedbackEvent):
            up += event.direction == "up"
            down += event.direction == "down"
            stale += event.direction == "stale"
    return up, down, stale


def legacy_yaml_history(text: str) -> str:
    """Lossless validated seeds; never silently discard malformed legacy counters."""
    import yaml

    try:
        values = yaml.safe_load(text)
        if values is None:
            values = {}
    except yaml.YAMLError as exc:
        raise ValueError("Malformed legacy knowledge ratings") from exc
    if not isinstance(values, dict):
        raise ValueError("Legacy knowledge ratings must be a mapping")
    records = []
    for entry_id, counters in sorted(values.items(), key=lambda item: str(item[0])):
        if not isinstance(entry_id, str) or not entry_id or not isinstance(counters, dict):
            raise ValueError("Legacy knowledge counters require entry IDs and mappings")
        if set(counters) - {"up", "down", "stale", "superseded_by"}:
            raise ValueError("Unrecognized legacy counter fields; preserve original ratings")
        for key in ("up", "down", "stale"):
            value = counters.get(key, 0)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("Legacy utility counters must be nonnegative integers")
        target = counters.get("superseded_by")
        if target is not None and not isinstance(target, str):
            raise ValueError("Legacy supersession reference must be an entry ID")
        records.append(
            canonical_json(
                {
                    "event_id": "legacy-seed:" + entry_id,
                    "id": entry_id,
                    "seed": True,
                    **{key: counters.get(key, 0) for key in ("up", "down", "stale")},
                    "superseded_by": target,
                }
            )
        )
    return "\n".join(records)
