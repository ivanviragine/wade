"""Copilot pre-tool-use hook configuration for plan-session worktrees.

Writes `.github/hooks/hooks.json` with a `preToolUse` entry that runs the
plan write guard script.  Copilot CLI reads hooks from `.github/hooks/*.json`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from wade.config.hooks_util import upsert_hook_entry


class CopilotHookEntry(BaseModel):
    type: str
    bash: str
    comment: str


def configure_plan_hooks(worktree_path: Path, guard_script: Path) -> None:
    """Write .github/hooks/hooks.json with a preToolUse guard entry.

    Uses the official GitHub Copilot CLI hooks.json schema with
    ``version``, ``hooks.preToolUse`` array, and hook objects containing
    ``type``, ``bash``, and ``comment`` fields.

    Merges with any existing hooks config.  Idempotent — re-running
    with the same guard_script path is a no-op.
    """
    upsert_hook_entry(
        hooks_file=worktree_path / ".github" / "hooks" / "hooks.json",
        entry=CopilotHookEntry(
            type="command",
            bash=f"python3 {guard_script.resolve()}",
            comment="Plan write guard for file-write tools (edit, create)",
        ),
        dedup_key="bash",
        ensure_path=["hooks", "preToolUse"],
        top_level_defaults={"version": 1},
        log_event="copilot_hooks.configured",
    )
