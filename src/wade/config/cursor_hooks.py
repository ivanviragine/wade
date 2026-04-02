"""Cursor pre-tool-use hook configuration for plan-session worktrees.

Writes `.cursor/hooks.json` with a `preToolUse` entry that runs the
plan write guard script on file-write tools.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel

from wade.config.hooks_util import upsert_hook_entry


class CursorHookEntry(BaseModel):
    event: str
    command: str
    tools: list[str]


def _configure_cursor_hook(worktree_path: Path, guard_script: Path, log_event: str) -> None:
    """Write .cursor/hooks.json with a preToolUse guard entry.

    Merges with any existing hooks config.  Idempotent — re-running
    with the same guard_script path is a no-op.
    """
    upsert_hook_entry(
        hooks_file=worktree_path / ".cursor" / "hooks.json",
        entry=CursorHookEntry(
            event="preToolUse",
            command=f"{sys.executable} {guard_script.resolve()}",
            tools=["Write", "Edit", "Delete"],
        ),
        dedup_key="command",
        ensure_path=["preToolUse"],
        log_event=log_event,
    )


def configure_worktree_hooks(worktree_path: Path, guard_script: Path) -> None:
    """Write .cursor/hooks.json with a preToolUse worktree guard entry.

    Merges with any existing hooks config.  Idempotent — re-running
    with the same guard_script path is a no-op.
    """
    _configure_cursor_hook(worktree_path, guard_script, "cursor_worktree_hooks.configured")


def configure_plan_hooks(worktree_path: Path, guard_script: Path) -> None:
    """Write .cursor/hooks.json with a preToolUse guard entry.

    Merges with any existing hooks config.  Idempotent — re-running
    with the same guard_script path is a no-op.
    """
    _configure_cursor_hook(worktree_path, guard_script, "cursor_hooks.configured")
