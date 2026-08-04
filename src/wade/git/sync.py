"""Fetch + merge operations for work sync."""

from __future__ import annotations

from pathlib import Path

import structlog

from wade.git.repo import GitError, _run_git, _run_git_with_retry, get_current_branch
from wade.models.session import SyncResult

log = structlog.get_logger(__name__)


def fetch_origin(repo_root: Path) -> None:
    """Fetch from the 'origin' remote.

    Args:
        repo_root: Repository root directory.

    Raises:
        GitError: If the fetch fails (network error, no remote, etc.).
    """
    log.info("sync.fetch")
    # Retry transient ref-lock contention — N parallel sessions fetch at once (C3).
    _run_git_with_retry("fetch", "origin", cwd=repo_root)


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

    # C5a: capture the REAL number of commits *branch* contributes, computed
    # before the merge folds them into *current* (git rev-list --count
    # current..branch). The old heuristic only ever returned 0 or 1.
    merged_count = _count_commits_to_merge(repo_root, current, branch)

    # Retry transient index-lock contention; a real conflict/failure is a
    # non-lock non-zero result and is returned for the caller to inspect (C3).
    result = _run_git_with_retry(
        "merge",
        "--no-edit",
        branch,
        cwd=repo_root,
        check=False,
    )

    if result.returncode == 0:
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


def _count_commits_to_merge(repo_root: Path, current: str, branch: str) -> int:
    """Return the true number of commits *branch* has that *current* does not.

    Equivalent to ``git rev-list --count current..branch`` — the exact number of
    commits a merge of *branch* into *current* would bring in (0 when already up
    to date). Computed before the merge so ``SyncResult.commits_merged`` is
    accurate, not a 0/1 heuristic (C5a). Returns 0 on any git error.
    """
    result = _run_git(
        "rev-list",
        "--count",
        f"{current}..{branch}",
        cwd=repo_root,
        check=False,
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


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
