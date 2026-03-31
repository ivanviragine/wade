"""Gemini CLI pre-tool hook configuration for plan-session worktrees.

Writes `.gemini/settings.json` (project-level) with a `BeforeTool` entry
that runs the plan write guard script.  Gemini may only support global
config — writing project-level is a best-effort approach.

The hooks schema expected by Gemini CLI is an object keyed by event name:

    {
      "hooks": {
        "BeforeTool": [
          {
            "matcher": ".*",
            "hooks": [
              {"type": "command", "command": "python3 /path/to/guard.py"}
            ]
          }
        ]
      }
    }
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from wade.config.hooks_util import upsert_hook_entry


class GeminiHookCommand(BaseModel):
    type: str = "command"
    command: str


class GeminiHookEntry(BaseModel):
    matcher: str
    hooks: list[GeminiHookCommand]


def configure_worktree_hooks(worktree_path: Path, guard_script: Path) -> None:
    """Write .gemini/settings.json with a BeforeTool worktree guard entry.

    Merges with any existing settings.  Idempotent — re-running
    with the same guard_script path is a no-op.
    """
    upsert_hook_entry(
        hooks_file=worktree_path / ".gemini" / "settings.json",
        entry=GeminiHookEntry(
            matcher=".*",
            hooks=[GeminiHookCommand(command=f"python3 {guard_script.resolve()}")],
        ),
        dedup_key="matcher",
        ensure_path=["hooks", "BeforeTool"],
        log_event="gemini_worktree_hooks.configured",
    )


def configure_plan_hooks(worktree_path: Path, guard_script: Path) -> None:
    """Write .gemini/settings.json with a BeforeTool guard entry.

    Merges with any existing settings.  Idempotent — re-running
    with the same guard_script path is a no-op.
    """
    upsert_hook_entry(
        hooks_file=worktree_path / ".gemini" / "settings.json",
        entry=GeminiHookEntry(
            matcher=".*",
            hooks=[GeminiHookCommand(command=f"python3 {guard_script.resolve()}")],
        ),
        dedup_key="matcher",
        ensure_path=["hooks", "BeforeTool"],
        log_event="gemini_hooks.configured",
    )
