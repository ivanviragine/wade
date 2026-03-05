"""Cursor CLI ~/.cursor/cli-config.json allowlist management.

Configures the Cursor CLI permission allowlist to include ``wade`` commands
and project scripts, so agents can run them without manual approval.

Unlike Claude Code (per-project .claude/settings.json), Cursor stores its
CLI config globally at ``~/.cursor/cli-config.json``.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Allowlist entry pattern for wade commands
WADE_ALLOW_PATTERN = "Shell(wade *)"

_CLI_CONFIG_PATH = Path.home() / ".cursor" / "cli-config.json"


def canonical_to_cursor(pattern: str) -> str:
    """Convert a canonical command pattern to Cursor CLI allowlist syntax.

    Canonical patterns use shell-style ``"cmd args"`` notation.
    Cursor expects ``"Shell(cmd args)"`` — the command string wrapped in
    ``Shell(…)``.

    Examples::

        "wade *"                → "Shell(wade *)"
        "./scripts/check.sh *"  → "Shell(./scripts/check.sh *)"
    """
    return f"Shell({pattern})"


def is_allowlist_configured(project_root: Path | None = None) -> bool:
    """Return True if Shell(wade *) is present in the global Cursor allowlist.

    ``project_root`` is accepted for interface compatibility but unused —
    Cursor stores its CLI config globally.
    """
    if not _CLI_CONFIG_PATH.is_file():
        return False
    with contextlib.suppress(json.JSONDecodeError, OSError):
        raw = json.loads(_CLI_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            allow = raw.get("permissions", {}).get("allow", [])
            return isinstance(allow, list) and WADE_ALLOW_PATTERN in allow
    return False


def configure_allowlist(
    project_root: Path | None = None,
    extra_patterns: list[str] | None = None,
) -> None:
    """Add wade commands to ~/.cursor/cli-config.json permissions allowlist.

    Args:
        project_root: Accepted for interface compatibility but unused —
            Cursor stores its CLI config globally.
        extra_patterns: Additional canonical command patterns (e.g.
            ``["./scripts/check.sh *"]``).  Translated to Cursor syntax
            and merged into the allowlist.

    Idempotent — each pattern is added at most once.  Non-destructive
    merge with existing config.
    """
    existing: dict[str, object] = {}
    if _CLI_CONFIG_PATH.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(_CLI_CONFIG_PATH.read_text(encoding="utf-8"))
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

    # Build the full set of patterns to ensure
    all_patterns = [WADE_ALLOW_PATTERN]
    for pat in extra_patterns or []:
        cursor_pat = canonical_to_cursor(pat)
        if cursor_pat not in all_patterns:
            all_patterns.append(cursor_pat)

    changed = False
    for pat in all_patterns:
        if pat not in allow_list:
            allow_list.append(pat)
            changed = True

    if not changed:
        return  # All patterns already present

    _CLI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CLI_CONFIG_PATH.write_text(
        json.dumps(existing, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("cursor_allowlist.configured", path=str(_CLI_CONFIG_PATH))
