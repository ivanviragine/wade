"""Branch naming, creation, and comparison utilities."""

from __future__ import annotations

from pathlib import Path

import structlog

from wade.git.repo import _run_git, _run_git_with_retry
from wade.utils.slug import slugify

log = structlog.get_logger(__name__)


def make_branch_name(prefix: str, issue_number: int, title: str) -> str:
    """Build a branch name from a prefix, issue number, and title slug.

    Produces names like ``feat/42-add-user-auth``.

    Args:
        prefix: Branch prefix (e.g., "feat", "fix", "chore").
        issue_number: GitHub issue number.
        title: Human-readable title to slugify.

    Returns:
        A valid git branch name.
    """
    slug = slugify(title, max_length=50)
    return f"{prefix}/{issue_number}-{slug}"


def branch_exists(repo_root: Path, branch_name: str) -> bool:
    """Check whether a local branch exists.

    Args:
        repo_root: Repository root directory.
        branch_name: Name of the branch to check.

    Returns:
        True if the branch exists locally.
    """
    result = _run_git(
        "rev-parse",
        "--verify",
        f"refs/heads/{branch_name}",
        cwd=repo_root,
        check=False,
    )
    return result.returncode == 0


def create_branch(
    repo_root: Path,
    branch_name: str,
    start_point: str = "HEAD",
) -> None:
    """Create a new local branch.

    Args:
        repo_root: Repository root directory.
        branch_name: Name for the new branch.
        start_point: Commit, branch, or tag to base the branch on.

    Raises:
        GitError: If the branch already exists or the start_point is invalid.
    """
    log.info("branch.create", branch=branch_name, start_point=start_point)
    _run_git_with_retry("branch", branch_name, start_point, cwd=repo_root)


def delete_branch(
    repo_root: Path,
    branch_name: str,
    force: bool = False,
) -> None:
    """Delete a local branch.

    Args:
        repo_root: Repository root directory.
        branch_name: Name of the branch to delete.
        force: If True, use -D (force delete even if unmerged).

    Raises:
        GitError: If the branch does not exist or cannot be deleted.
    """
    flag = "-D" if force else "-d"
    log.info("branch.delete", branch=branch_name, force=force)
    _run_git("branch", flag, branch_name, cwd=repo_root)


def create_scaffold_commit(
    repo_root: Path,
    branch_name: str,
    message: str,
) -> None:
    """Create an empty commit on a branch without checking it out.

    Uses git plumbing (``commit-tree`` + ``update-ref``) to avoid touching
    the working directory or requiring a checkout.

    Args:
        repo_root: Repository root directory.
        branch_name: Target branch (must already exist locally).
        message: Commit message for the scaffold commit.

    Raises:
        GitError: If the branch does not exist or plumbing commands fail.
    """
    log.info("branch.scaffold_commit", branch=branch_name)
    tree = _run_git("rev-parse", f"{branch_name}^{{tree}}", cwd=repo_root).stdout.strip()
    parent = _run_git("rev-parse", branch_name, cwd=repo_root).stdout.strip()
    commit = _run_git(
        "commit-tree", tree, "-p", parent, "-m", message, cwd=repo_root
    ).stdout.strip()
    _run_git("update-ref", f"refs/heads/{branch_name}", commit, cwd=repo_root)


def commits_ahead(repo_root: Path, branch: str, base: str) -> int:
    """Count commits on *branch* that are not on *base*.

    Equivalent to ``git rev-list --count base..branch``.

    Args:
        repo_root: Repository root directory.
        branch: The branch to measure.
        base: The reference branch (e.g., "main").

    Returns:
        Number of commits ahead.

    Raises:
        GitError: If either ref is invalid.
    """
    result = _run_git(
        "rev-list",
        "--count",
        f"{base}..{branch}",
        cwd=repo_root,
    )
    return int(result.stdout.strip())
