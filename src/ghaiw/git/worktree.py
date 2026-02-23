"""Worktree lifecycle — create, remove, list, prune."""

from __future__ import annotations

from pathlib import Path

import structlog

from ghaiw.git.repo import GitError, _run_git

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
    _run_git(
        "worktree", "add",
        "-b", branch_name,
        str(worktree_path),
        base_branch,
        cwd=repo_root,
    )
    return worktree_path


def remove_worktree(repo_root: Path, worktree_path: Path) -> None:
    """Remove a linked worktree and clean up its administrative files.

    Args:
        repo_root: Root of the main repository checkout.
        worktree_path: Path to the worktree to remove.

    Raises:
        GitError: If the worktree could not be removed.
    """
    log.info("worktree.remove", worktree=str(worktree_path))
    _run_git(
        "worktree", "remove", "--force", str(worktree_path),
        cwd=repo_root,
    )


def list_worktrees(repo_root: Path) -> list[dict[str, str]]:
    """List all worktrees for a repository.

    Args:
        repo_root: Root of the main repository checkout.

    Returns:
        List of dicts, each with keys: "path", "head", "branch".
        The branch value is the short ref name (e.g., "main") or
        "(detached)" for detached HEAD worktrees.
    """
    result = _run_git("worktree", "list", "--porcelain", cwd=repo_root)
    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line[len("worktree "):]}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            # Refs come as "refs/heads/branch-name"
            ref = line[len("branch "):]
            current["branch"] = ref.removeprefix("refs/heads/")
        elif line.strip() == "detached":
            current["branch"] = "(detached)"
        elif line.strip() == "" and current:
            worktrees.append(current)
            current = {}

    if current:
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
    _run_git("worktree", "prune", cwd=repo_root)
