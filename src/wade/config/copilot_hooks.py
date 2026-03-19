"""Copilot pre-tool-use hook configuration for plan-session worktrees.

Writes `.copilot/hooks.json` with a `preToolUse` entry that runs the
plan write guard script on file-write tools.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

logger = structlog.get_logger()


def configure_plan_hooks(worktree_path: Path, guard_script: Path) -> None:
    """Write .copilot/hooks.json with a preToolUse guard entry.

    Merges with any existing hooks config.  Idempotent — re-running
    with the same guard_script path is a no-op.
    """
    hooks_file = worktree_path / ".copilot" / "hooks.json"

    existing: dict[str, object] = {}
    if hooks_file.is_file():
        try:
            raw = json.loads(hooks_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw
        except (json.JSONDecodeError, OSError):
            pass

    hooks_list = existing.setdefault("preToolUse", [])
    if not isinstance(hooks_list, list):
        hooks_list = []
        existing["preToolUse"] = hooks_list

    guard_entry = {
        "event": "preToolUse",
        "command": f"python3 {guard_script}",
        "tools": ["edit", "create"],
    }

    # Check if already present (by command)
    for entry in hooks_list:
        if isinstance(entry, dict) and entry.get("command") == guard_entry["command"]:
            return  # Already configured

    hooks_list.append(guard_entry)

    hooks_file.parent.mkdir(parents=True, exist_ok=True)
    hooks_file.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    logger.info("copilot_hooks.configured", path=str(hooks_file))
