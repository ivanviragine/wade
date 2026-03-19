"""Cursor CLI permission allowlist management.

Configures the Cursor CLI permission allowlist to include ``wade`` commands
and project scripts, so agents can run them without manual approval.

Cursor supports two config locations:

- **Per-project**: ``<project>/.cursor/cli.json`` (preferred for worktrees)
- **Global**: ``~/.cursor/cli-config.json`` (fallback / ``wade init``)

When a ``project_root`` is provided, the per-project config is used.
When ``project_root`` is ``None``, the global config is used.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Allowlist entry pattern for wade commands.
# Uses the cmd:args colon format supported by Cursor CLI's permission system.
WADE_ALLOW_PATTERN = "Shell(wade:*)"
# Legacy pattern written by older wade versions — migrated out on next write.
_WADE_ALLOW_PATTERN_LEGACY = "Shell(wade *)"

_GLOBAL_CONFIG_PATH = Path.home() / ".cursor" / "cli-config.json"


def _config_path(project_root: Path | None) -> Path:
    """Return the Cursor CLI config path for the given scope.

    Per-project: ``<project_root>/.cursor/cli.json``
    Global:      ``~/.cursor/cli-config.json``
    """
    if project_root is not None:
        return project_root / ".cursor" / "cli.json"
    return _GLOBAL_CONFIG_PATH


def canonical_to_cursor(pattern: str) -> str:
    """Convert a canonical command pattern to Cursor CLI allowlist syntax.

    Canonical patterns use ``"cmd:args"`` notation (colon-separated).
    Cursor expects ``"Shell(cmd:args)"`` — the command string wrapped in
    ``Shell(…)``.

    Examples::

        "wade:*"                  → "Shell(wade:*)"
        "./scripts/check.sh:*"    → "Shell(./scripts/check.sh:*)"
    """
    return f"Shell({pattern})"


def is_allowlist_configured(project_root: Path | None = None) -> bool:
    """Return True if a wade allowlist pattern is present in the Cursor allowlist.

    When ``project_root`` is given, checks the per-project config.
    Otherwise checks the global config.
    """
    config_file = _config_path(project_root)
    if not config_file.is_file():
        return False
    with contextlib.suppress(json.JSONDecodeError, OSError):
        raw = json.loads(config_file.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            allow = raw.get("permissions", {}).get("allow", [])
            return isinstance(allow, list) and (
                WADE_ALLOW_PATTERN in allow or _WADE_ALLOW_PATTERN_LEGACY in allow
            )
    return False


def configure_allowlist(
    project_root: Path | None = None,
    extra_patterns: list[str] | None = None,
) -> None:
    """Add wade commands to the Cursor CLI permissions allowlist.

    Args:
        project_root: When provided, writes to the per-project config
            ``<project_root>/.cursor/cli.json``.  When ``None``, writes
            to the global ``~/.cursor/cli-config.json``.
        extra_patterns: Additional canonical command patterns (e.g.
            ``["./scripts/check.sh *"]``).  Translated to Cursor syntax
            and merged into the allowlist.

    Idempotent — each pattern is added at most once.  Non-destructive
    merge with existing config.
    """
    config_file = _config_path(project_root)

    existing: dict[str, object] = {}
    if config_file.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(config_file.read_text(encoding="utf-8"))
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

    # Migrate: remove legacy space-format pattern if present
    if _WADE_ALLOW_PATTERN_LEGACY in allow_list:
        allow_list.remove(_WADE_ALLOW_PATTERN_LEGACY)
        changed = True

    # Build the full set of patterns to ensure
    all_patterns = [WADE_ALLOW_PATTERN]
    for pat in extra_patterns or []:
        cursor_pat = canonical_to_cursor(pat)
        if cursor_pat not in all_patterns:
            all_patterns.append(cursor_pat)

    for pat in all_patterns:
        if pat not in allow_list:
            allow_list.append(pat)
            changed = True

    if not changed:
        return  # All patterns already present

    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        json.dumps(existing, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("cursor_allowlist.configured", path=str(config_file))
