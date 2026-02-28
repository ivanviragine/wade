"""Claude Code .claude/settings.json allowlist management.

Configures the Claude Code permission allowlist to include `ghaiw` commands,
so agents can run `ghaiw work done`, `ghaiw new-task`, etc. without
manual approval.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Allowlist entry pattern for ghaiw commands
GHAIWPY_ALLOW_PATTERN = "Bash(ghaiw *)"


def is_allowlist_configured(project_root: Path) -> bool:
    """Return True if Bash(ghaiw *) is present in the allowlist at project_root."""
    settings_path = project_root / ".claude" / "settings.json"
    if not settings_path.is_file():
        return False
    with contextlib.suppress(json.JSONDecodeError, OSError):
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            allow = raw.get("permissions", {}).get("allow", [])
            return isinstance(allow, list) and GHAIWPY_ALLOW_PATTERN in allow
    return False


def configure_allowlist(project_root: Path) -> None:
    """Add ghaiw commands to .claude/settings.json permissions allowlist.

    Idempotent — skips if already present. Non-destructive merge with
    existing settings.
    """
    settings_path = project_root / ".claude" / "settings.json"

    existing: dict[str, object] = {}
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
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

    if GHAIWPY_ALLOW_PATTERN in allow_list:
        return  # Already present

    allow_list.append(GHAIWPY_ALLOW_PATTERN)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(existing, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("claude_allowlist.configured", path=str(settings_path))
