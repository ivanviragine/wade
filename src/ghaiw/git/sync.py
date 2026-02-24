"""Fetch + merge operations for work sync."""

from __future__ import annotations

from pathlib import Path

import structlog

from ghaiw.git.repo import GitError, _run_git, get_current_branch
from ghaiw.models.work import SyncResult

log = structlog.get_logger(__name__)


def fetch_origin(repo_root: Path) -> None:
    """Fetch from the 'origin' remote.

    Args:
        repo_root: Repository root directory.

    Raises:
        GitError: If the fetch fails (network error, no remote, etc.).
    """
    log.info("sync.fetch")
    _run_git("fetch", "origin", cwd=repo_root)


def merge_branch(repo_root: Path, branch: str) -> SyncResult:
    """Merge *branch* into the current branch.

    Attempts a ``git merge --no-edit`` and inspects the result.  If conflicts
    occur the merge is left in progress (caller should resolve or abort).

    Args:
        repo_root: Repository root directory.
        branch: Branch or ref to merge (e.g., "origin/main", "main").

    Returns:
        A SyncResult describing what happened.
    """
    current = get_current_branch(repo_root)
    log.info("sync.merge", current=current, merging=branch)

    result = _run_git(
        "merge",
        "--no-edit",
        branch,
        cwd=repo_root,
        check=False,
    )

    if result.returncode == 0:
        # Merge succeeded — count how many commits were merged
        merged_count = _count_merged_commits(result.stdout)
        return SyncResult(
            success=True,
            current_branch=current,
            main_branch=branch,
            commits_merged=merged_count,
        )

    # Check if there are conflicts
    conflicts = get_conflicted_files(repo_root)
    if conflicts:
        return SyncResult(
            success=False,
            current_branch=current,
            main_branch=branch,
            conflicts=conflicts,
        )

    # Non-conflict merge failure
    raise GitError(f"git merge {branch} failed (exit {result.returncode}): {result.stderr.strip()}")


def _count_merged_commits(merge_output: str) -> int:
    """Extract the number of merged commits from git merge output.

    Git merge output typically contains something like "Fast-forward" or
    "Merge made by the '...' strategy." — we return 0 for already-up-to-date
    and 1 as a minimum for any successful merge that wasn't a no-op.

    For a more accurate count the caller should use ``commits_ahead`` before
    and after.
    """
    if "Already up to date" in merge_output:
        return 0
    # Any successful merge moved at least one commit forward
    return 1


def get_conflicted_files(repo_root: Path) -> list[str]:
    """Return a list of files with merge conflicts.

    Uses ``git diff --name-only --diff-filter=U`` to find unmerged paths.

    Args:
        repo_root: Repository root directory.

    Returns:
        List of file paths relative to the repo root, or empty list.
    """
    result = _run_git(
        "diff",
        "--name-only",
        "--diff-filter=U",
        cwd=repo_root,
        check=False,
    )
    if result.returncode != 0:
        error_msg = (
            f"git diff --name-only --diff-filter=U failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
        raise GitError(error_msg)
    files = [f for f in result.stdout.strip().splitlines() if f]
    return files


def abort_merge(repo_root: Path) -> None:
    """Abort an in-progress merge.

    Args:
        repo_root: Repository root directory.

    Raises:
        GitError: If there is no merge to abort or abort fails.
    """
    log.info("sync.abort_merge")
    _run_git("merge", "--abort", cwd=repo_root)
