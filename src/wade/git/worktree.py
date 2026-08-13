"""Worktree lifecycle — create, remove, list, prune."""

from __future__ import annotations

from pathlib import Path

import structlog

from wade.git.repo import _run_git, _run_git_with_retry
from wade.models import Worktree

log = structlog.get_logger(__name__)


def create_worktree(
    repo_root: Path,
    branch_name: str,
    worktree_dir: Path,
    base_branch: str = "main",
) -> Path:
    """Create a new git worktree with a new branch.

    Args:
        repo_root: Root of the main repository checkout.
        branch_name: Name for the new branch.
        worktree_dir: Directory where the worktree will be created.
        base_branch: Branch to base the new branch on.

    Returns:
        Absolute path to the created worktree directory.

    Raises:
        GitError: If the worktree could not be created.
    """
    worktree_path = worktree_dir.resolve()
    log.info(
        "worktree.create",
        branch=branch_name,
        worktree=str(worktree_path),
        base=base_branch,
    )
    _run_git_with_retry(
        "worktree",
        "add",
        "-b",
        branch_name,
        str(worktree_path),
        base_branch,
        cwd=repo_root,
    )
    return worktree_path


def create_detached_worktree(
    repo_root: Path,
    worktree_dir: Path,
    ref: str = "HEAD",
) -> Path:
    """Create a detached-HEAD worktree at the given ref.

    Args:
        repo_root: Root of the main repository checkout.
        worktree_dir: Directory where the worktree will be created.
        ref: Git ref to check out (default: HEAD).

    Returns:
        Absolute path to the created worktree directory.

    Raises:
        GitError: If the worktree could not be created.
    """
    worktree_path = worktree_dir.resolve()
    log.info(
        "worktree.create_detached",
        worktree=str(worktree_path),
        ref=ref,
    )
    _run_git_with_retry(
        "worktree",
        "add",
        "--detach",
        str(worktree_path),
        ref,
        cwd=repo_root,
    )
    return worktree_path


def checkout_existing_branch_worktree(
    repo_root: Path,
    branch_name: str,
    worktree_dir: Path,
) -> Path:
    """Create a worktree that checks out an existing branch.

    Unlike :func:`create_worktree`, this does **not** create a new branch.
    The branch must already exist (locally or as a remote tracking branch).

    Args:
        repo_root: Root of the main repository checkout.
        branch_name: Existing branch to check out.
        worktree_dir: Directory where the worktree will be created.

    Returns:
        Absolute path to the created worktree directory.

    Raises:
        GitError: If the worktree could not be created or the branch does not exist.
    """
    worktree_path = worktree_dir.resolve()
    log.info(
        "worktree.checkout_existing",
        branch=branch_name,
        worktree=str(worktree_path),
    )
    _run_git_with_retry(
        "worktree",
        "add",
        str(worktree_path),
        branch_name,
        cwd=repo_root,
    )
    return worktree_path


def remove_worktree(repo_root: Path, worktree_path: Path, force: bool = True) -> None:
    """Remove a linked worktree and clean up its administrative files.

    Args:
        repo_root: Root of the main repository checkout.
        worktree_path: Path to the worktree to remove.
        force: If True (default), use --force flag to remove even if worktree is dirty.

    Raises:
        GitError: If the worktree could not be removed.
    """
    log.info("worktree.remove", worktree=str(worktree_path), force=force)
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path))
    # Retry transient worktree-lock contention from parallel sessions (C3).
    _run_git_with_retry(*args, cwd=repo_root)


def list_worktrees(repo_root: Path) -> list[Worktree]:
    """List all worktrees for a repository.

    Args:
        repo_root: Root of the main repository checkout.

    Returns:
        List of :class:`~wade.models.Worktree` entries with typed ``path``,
        ``head``, and ``branch`` attributes. The branch value is the short ref
        name (e.g., "main") or "(detached)" for detached HEAD worktrees.
    """
    result = _run_git("worktree", "list", "--porcelain", cwd=repo_root)
    worktrees: list[Worktree] = []
    current: Worktree | None = None

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current is not None:
                worktrees.append(current)
            current = Worktree(path=line[len("worktree ") :])
        elif current is None:
            # Porcelain output always opens an entry with a "worktree " line;
            # ignore anything before the first one.
            continue
        elif line.startswith("HEAD "):
            current.head = line[len("HEAD ") :]
        elif line.startswith("branch "):
            # Refs come as "refs/heads/branch-name"
            current.branch = line[len("branch ") :].removeprefix("refs/heads/")
        elif line.strip() == "detached":
            current.branch = "(detached)"
        elif line.strip() == "":
            worktrees.append(current)
            current = None

    if current is not None:
        worktrees.append(current)

    return worktrees


def prune_worktrees(repo_root: Path) -> None:
    """Prune stale worktree administrative data.

    Removes administrative files for worktrees whose directory no longer
    exists on disk.

    Args:
        repo_root: Root of the main repository checkout.

    Raises:
        GitError: If the prune command fails.
    """
    log.info("worktree.prune")
    _run_git_with_retry("worktree", "prune", cwd=repo_root)
