"""Shared utility for JSON-based permission allowlist management.

Used by claude_allowlist and cursor_allowlist to avoid duplicating the
read→migrate→merge→write pattern.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import Path

import structlog

logger = structlog.get_logger()


def configure_json_allowlist(
    config_path: Path,
    *,
    wade_pattern: str,
    legacy_pattern: str,
    extra_patterns: list[str] | None = None,
    pattern_converter: Callable[[str], str] | None = None,
    log_event: str = "allowlist.configured",
) -> None:
    """Read a JSON config, ensure ``permissions.allow`` contains all required patterns, write back.

    Parameters
    ----------
    config_path:
        Path to the JSON settings file.
    wade_pattern:
        The current canonical wade allowlist entry (e.g. ``"Bash(wade:*)"``).
    legacy_pattern:
        A legacy pattern to migrate out if found (e.g. ``"Bash(wade *)"``).
    extra_patterns:
        Additional canonical patterns to convert and merge.
    pattern_converter:
        Callable that converts a canonical pattern to the tool's format.
        When ``None``, patterns are used as-is.
    log_event:
        Structlog event name emitted on success.
    """
    existing: dict[str, object] = {}
    if config_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw

    permissions = existing.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        permissions = {}
        existing["permissions"] = permissions

    allow_list = permissions.setdefault("allow", [])
    if not isinstance(allow_list, list):
        allow_list = []
        permissions["allow"] = allow_list

    changed = False

    # Migrate legacy pattern
    if legacy_pattern in allow_list:
        allow_list.remove(legacy_pattern)
        changed = True

    # Build patterns to ensure
    all_patterns = [wade_pattern]
    for pat in extra_patterns or []:
        tool_pat = pattern_converter(pat) if pattern_converter else pat
        if tool_pat not in all_patterns:
            all_patterns.append(tool_pat)

    for pat in all_patterns:
        if pat not in allow_list:
            allow_list.append(pat)
            changed = True

    if not changed:
        return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    logger.info(log_event, path=str(config_path))
