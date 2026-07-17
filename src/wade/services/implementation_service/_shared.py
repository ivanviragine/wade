"""Shared leaf helpers for the implementation service package.

Small, dependency-free resolution utilities used by several submodules
(``core``, ``cleanup``, ``done``, ``batch``). Kept here to avoid peer-to-peer
import cycles between those modules.
"""

from __future__ import annotations

import re
from pathlib import Path

from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.git.repo import GitError

__all__ = [
    "extract_issue_from_branch",
    "find_worktree_path",
]


def find_worktree_path(
    target: str,
    project_root: Path | None = None,
) -> Path | None:
    """Find the worktree path for a given issue number or branch name."""
    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        return None

    worktrees = git_worktree.list_worktrees(repo_root)

    for wt in worktrees:
        wt_branch = wt.get("branch", "")
        wt_path = wt.get("path", "")

        # Match by issue number in branch name
        if f"/{target}-" in wt_branch or wt_branch.endswith(f"/{target}"):
            return Path(wt_path)

        # Match by worktree directory name (boundary-aware to avoid
        # target="1" matching "feat-10-something")
        if re.search(rf"(?:^|-){re.escape(target)}(?:-|$)", Path(wt_path).name):
            return Path(wt_path)

    return None


def extract_issue_from_branch(branch: str) -> str | None:
    """Extract the issue number from a branch name like ``feat/42-slug``."""
    m = re.search(r"/(\d+)", branch)
    return m.group(1) if m else None
