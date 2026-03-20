"""Gemini CLI pre-tool hook configuration for plan-session worktrees.

Writes `.gemini/settings.json` (project-level) with a `BeforeTool` entry
that runs the plan write guard script.  Gemini may only support global
config — writing project-level is a best-effort approach.
"""

from __future__ import annotations

from pathlib import Path

from wade.config.hooks_util import upsert_hook_entry


def configure_plan_hooks(worktree_path: Path, guard_script: Path) -> None:
    """Write .gemini/settings.json with a BeforeTool guard entry.

    Merges with any existing settings.  Idempotent — re-running
    with the same guard_script path is a no-op.
    """
    upsert_hook_entry(
        hooks_file=worktree_path / ".gemini" / "settings.json",
        entry={
            "event": "BeforeTool",
            "command": f"python3 {guard_script.resolve()}",
            "tools": [".*"],
        },
        dedup_key="command",
        ensure_path=["hooks"],
        log_event="gemini_hooks.configured",
    )
