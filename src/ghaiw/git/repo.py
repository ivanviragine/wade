"""Repo introspection via subprocess — is_git_repo, branches, clean checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


class GitError(Exception):
    """Raised when a git command fails unexpectedly."""


def _run_git(
    *args: str,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the CompletedProcess result.

    Args:
        *args: Git subcommand and arguments (e.g., "rev-parse", "--git-dir").
        cwd: Working directory for the command.
        check: If True, raise GitError on non-zero exit.

    Returns:
        CompletedProcess with captured stdout/stderr.

    Raises:
        GitError: If check is True and the command fails.
    """
    cmd = ["git", *args]
    log.debug("git.run", cmd=cmd, cwd=str(cwd))
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=check,
        )
    except subprocess.CalledProcessError as exc:
        raise GitError(
            f"git {' '.join(args)} failed (exit {exc.returncode}): {exc.stderr.strip()}"
        ) from exc
    return result


def is_git_repo(path: Path) -> bool:
    """Check whether *path* is inside a git repository.

    Args:
        path: Directory to check.

    Returns:
        True if the path is inside a git repository.
    """
    result = _run_git("rev-parse", "--git-dir", cwd=path, check=False)
    return result.returncode == 0


def is_worktree(path: Path) -> bool:
    """Check whether *path* is inside a git worktree (not the main checkout).

    A linked worktree has a .git *file* (not directory) that points to the
    main repository's worktrees directory.

    Args:
        path: Directory to check.

    Returns:
        True if the path is inside a linked worktree.
    """
    result = _run_git("rev-parse", "--git-common-dir", cwd=path, check=False)
    if result.returncode != 0:
        return False
    common_dir = result.stdout.strip()

    result2 = _run_git("rev-parse", "--git-dir", cwd=path, check=False)
    if result2.returncode != 0:
        return False
    git_dir = result2.stdout.strip()

    # In the main checkout, --git-dir and --git-common-dir resolve to the same
    # directory.  In a linked worktree they differ.
    return Path(common_dir).resolve() != Path(git_dir).resolve()


def get_repo_root(path: Path) -> Path:
    """Return the repository root (top-level working directory).

    Args:
        path: Any directory inside the repo.

    Returns:
        Absolute path to the repo root.

    Raises:
        GitError: If *path* is not inside a git repository.
    """
    result = _run_git("rev-parse", "--show-toplevel", cwd=path)
    return Path(result.stdout.strip())


def get_current_branch(path: Path) -> str:
    """Return the name of the currently checked-out branch.

    Args:
        path: Any directory inside the repo.

    Returns:
        Branch name (e.g., "main", "feat/42-add-auth").

    Raises:
        GitError: If HEAD is detached or not in a repo.
    """
    result = _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=path)
    branch = result.stdout.strip()
    if branch == "HEAD":
        raise GitError("HEAD is detached — not on any branch")
    return branch


def detect_main_branch(path: Path) -> str:
    """Detect the main branch name — checks for 'main' then 'master'.

    Args:
        path: Any directory inside the repo.

    Returns:
        "main" or "master".

    Raises:
        GitError: If neither main nor master branch exists.
    """
    for candidate in ("main", "master"):
        result = _run_git(
            "rev-parse",
            "--verify",
            f"refs/heads/{candidate}",
            cwd=path,
            check=False,
        )
        if result.returncode == 0:
            log.debug("git.detect_main_branch", branch=candidate)
            return candidate
    raise GitError("Neither 'main' nor 'master' branch found")


def is_clean(path: Path) -> bool:
    """Check whether the working tree has no uncommitted changes.

    This checks both the index (staged) and the working tree (unstaged).

    Args:
        path: Any directory inside the repo.

    Returns:
        True if the working tree is clean.
    """
    result = _run_git("status", "--porcelain", cwd=path)
    return result.stdout.strip() == ""


def get_remote_url(path: Path) -> str | None:
    """Return the URL of the 'origin' remote, or None if not configured.

    Args:
        path: Any directory inside the repo.

    Returns:
        Remote URL string, or None.
    """
    result = _run_git("remote", "get-url", "origin", cwd=path, check=False)
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    return url if url else None
