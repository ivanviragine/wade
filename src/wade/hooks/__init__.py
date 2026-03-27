"""File-write guard hooks for plan and worktree sessions.

Provides standalone Python guard scripts that block AI tool writes
to files outside allowed paths.
"""

from __future__ import annotations

from pathlib import Path


def get_guard_script_path() -> Path:
    """Return the absolute path to the plan_write_guard.py script."""
    return Path(__file__).parent / "plan_write_guard.py"


def get_worktree_guard_script_path() -> Path:
    """Return the absolute path to the worktree_guard.py script."""
    return Path(__file__).parent / "worktree_guard.py"
