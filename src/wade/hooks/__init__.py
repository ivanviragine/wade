"""Plan-mode file-write guard hooks.

Provides a standalone Python guard script that blocks AI tool writes
to codebase files in plan-session worktrees.
"""

from __future__ import annotations

from pathlib import Path


def get_guard_script_path() -> Path:
    """Return the absolute path to the plan_write_guard.py script."""
    return Path(__file__).parent / "plan_write_guard.py"
