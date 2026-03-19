"""Gemini CLI pre-tool hook configuration for plan-session worktrees.

Writes `.gemini/settings.json` (project-level) with a `BeforeTool` entry
that runs the plan write guard script.  Gemini may only support global
config — writing project-level is a best-effort approach.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

logger = structlog.get_logger()


def configure_plan_hooks(worktree_path: Path, guard_script: Path) -> None:
    """Write .gemini/settings.json with a BeforeTool guard entry.

    Merges with any existing settings.  Idempotent — re-running
    with the same guard_script path is a no-op.
    """
    settings_file = worktree_path / ".gemini" / "settings.json"

    existing: dict[str, object] = {}
    if settings_file.is_file():
        try:
            raw = json.loads(settings_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw
        except (json.JSONDecodeError, OSError):
            pass

    hooks_list = existing.setdefault("hooks", [])
    if not isinstance(hooks_list, list):
        hooks_list = []
        existing["hooks"] = hooks_list

    resolved_script = guard_script.resolve()
    guard_entry = {
        "event": "BeforeTool",
        "command": f"python3 {resolved_script}",
        "tools": [".*"],
    }

    # Check if already present (by command)
    for entry in hooks_list:
        if isinstance(entry, dict) and entry.get("command") == guard_entry["command"]:
            return  # Already configured

    hooks_list.append(guard_entry)

    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    logger.info("gemini_hooks.configured", path=str(settings_file))
