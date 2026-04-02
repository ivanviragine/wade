"""Shared utility for reading, merging, deduplicating, and writing JSON hook configs.

Used by cursor_hooks, copilot_hooks, and gemini_hooks to avoid duplicating
the same read→ensure→dedup→append→write pattern.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

logger = structlog.get_logger()


def upsert_hook_entry(
    hooks_file: Path,
    entry: BaseModel,
    dedup_key: str,
    *,
    ensure_path: list[str] | None = None,
    top_level_defaults: dict[str, Any] | None = None,
    log_event: str = "hooks.configured",
) -> None:
    """Read a JSON hooks file, append *entry* if not already present, and write back.

    Parameters
    ----------
    hooks_file:
        Path to the JSON config file.
    entry:
        The hook entry model to append.
    dedup_key:
        The key within each entry dict used for deduplication (e.g. ``"command"``
        or ``"bash"``).  If an existing entry has the same value for this key,
        the function returns without writing (idempotent).
    ensure_path:
        Dotted path of nested keys leading to the hooks list.
        ``None`` or ``[]`` means the hooks list lives at ``root[<last segment>]``
        — which is derived from the first key of *entry* if needed.
        Example: ``["hooks", "preToolUse"]`` → ``root["hooks"]["preToolUse"]``.
    top_level_defaults:
        Extra keys to set on the root dict if they are missing
        (e.g. ``{"version": 1}``).
    log_event:
        Structlog event name emitted on success.
    """
    entry_dict = entry.model_dump()

    existing: dict[str, Any] = {}
    if hooks_file.is_file():
        raw = json.loads(hooks_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{hooks_file} must contain a JSON object, got {type(raw).__name__}")
        existing = raw

    if top_level_defaults:
        for k, v in top_level_defaults.items():
            existing.setdefault(k, v)

    # Walk/create the nested path to the hooks list
    path = ensure_path or []
    container = existing
    for segment in path[:-1]:
        nested = container.setdefault(segment, {})
        if not isinstance(nested, dict):
            # Stale data from a previous format — replace with empty dict
            logger.warning(
                "hooks.replaced_non_dict_segment",
                path=str(hooks_file),
                segment=segment,
                replaced_type=type(nested).__name__,
            )
            container[segment] = {}
            nested = container[segment]
        container = nested

    list_key = path[-1] if path else next(iter(entry_dict))

    hooks_list = container.setdefault(list_key, [])
    if not isinstance(hooks_list, list):
        raise ValueError(f"{hooks_file} has non-list data at {list_key!r}")

    # Dedup check — if entry already exists, still persist any new top-level defaults
    dedup_value = entry_dict.get(dedup_key)
    for existing_entry in hooks_list:
        if isinstance(existing_entry, dict) and existing_entry.get(dedup_key) == dedup_value:
            if top_level_defaults:
                hooks_file.parent.mkdir(parents=True, exist_ok=True)
                hooks_file.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
            return

    hooks_list.append(entry_dict)

    hooks_file.parent.mkdir(parents=True, exist_ok=True)
    hooks_file.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    logger.info(log_event, path=str(hooks_file))
