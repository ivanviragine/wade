"""Locked, durable append-only event persistence shared by knowledge services."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from wade.utils.filelock import file_lock
from wade.utils.knowledge_events import canonical_json, decode_history


def append_event(path: Path, record: dict[str, Any], *, tracked: bool = True) -> bool:
    if tracked:
        from wade.services.knowledge_ownership import assert_author_path

        assert_author_path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Knowledge history cannot follow symlinks")
    with file_lock(path):
        if tracked:
            from wade.services.knowledge_service import _materialize_migration_locked

            _materialize_migration_locked(path)
        text = path.read_text() if path.exists() else ""
        if tracked:
            decoded = decode_history(text)
            if decoded.problems:
                raise ValueError("; ".join(decoded.problems))
        event_id = record.get("event_id", record.get("disposition_id"))
        for line in text.splitlines():
            if not line.strip():
                continue
            prior = json.loads(line)
            if not isinstance(prior, dict):
                raise ValueError("Knowledge history records must be objects")
            if prior.get("event_id", prior.get("disposition_id")) == event_id:
                if canonical_json(prior) != canonical_json(record):
                    raise ValueError(f"Conflicting payload for durable event {event_id}")
                return False
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fd:
            fd.write(canonical_json(record) + "\n")
            fd.flush()
            os.fsync(fd.fileno())
        return True
