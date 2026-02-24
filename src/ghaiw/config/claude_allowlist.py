"""Claude Code .claude/settings.json allowlist management.

Configures the Claude Code permission allowlist to include `ghaiwpy` commands,
so agents can run `ghaiwpy work done`, `ghaiwpy task create`, etc. without
manual approval.

Behavioral reference: Bash ghaiw_update() allowlist step
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Allowlist entry pattern for ghaiwpy commands
GHAIWPY_ALLOW_PATTERN = "Bash(ghaiwpy *)"


def configure_allowlist(project_root: Path) -> None:
    """Add ghaiwpy commands to .claude/settings.json permissions allowlist.

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
