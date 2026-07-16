"""Git stash helpers for wade auto-stash operations."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import structlog

from wade.git.repo import GitError, _run_git

log = structlog.get_logger(__name__)

AUTOSTASH_PREFIX = "wade-autostash"


def _stash_message(session_type: str, branch: str) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    safe_branch = branch.replace("/", "_")
    return f"{AUTOSTASH_PREFIX}/{session_type}/{safe_branch}/{ts}"


def create_named_stash(session_type: str, branch: str, cwd: Path) -> tuple[str, str]:
    """Stash staged+unstaged tracked-file changes and return (ref, message).

    Untracked files are intentionally left in place — call
    detect_untracked_collisions() before this to ensure they won't conflict.

    Args:
        session_type: Session type label (e.g. "implementation").
        branch: Current branch name (embedded in the stash message).
        cwd: Working directory inside the git repo.

    Returns:
        Tuple of (stash_ref, stash_message), e.g. (``stash@{0}``, ``wade-autostash/...``).

    Raises:
        GitError: If the stash command fails or nothing was stashed.
    """
    message = _stash_message(session_type, branch)
    result = _run_git("stash", "push", "-m", message, cwd=cwd, check=False)
    if result.returncode != 0:
        raise GitError(f"git stash push failed: {result.stderr.strip()}")
    stdout = result.stdout.strip()
    if stdout == "No local changes to save":
        raise GitError("No local changes to save")
    ref = _find_stash_ref(message, cwd)
    if ref is None:
        raise GitError(f"Stash created but ref not found for: {message!r}")
    log.debug("git.stash.created", ref=ref, message=message)
    return ref, message


def _find_stash_ref(message: str, cwd: Path) -> str | None:
    """Return the stash ref (e.g. ``stash@{0}``) whose subject contains *message*."""
    result = _run_git("stash", "list", "--format=%gd %gs", cwd=cwd, check=False)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        ref, subject = parts[0], parts[1]
        if message in subject:
            return ref
    return None


def pop_stash(stash_ref: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Apply and remove *stash_ref*. Returns CompletedProcess (never raises)."""
    return _run_git("stash", "pop", stash_ref, cwd=cwd, check=False)


def detect_untracked_collisions(cwd: Path, merge_ref: str) -> list[str]:
    """Return untracked file paths that would be overwritten by merging *merge_ref*.

    Uses ``git diff --name-only --diff-filter=A HEAD...<merge_ref>`` to find
    files newly introduced by the incoming commits, then intersects with the
    untracked files in the working directory.

    The collision check is conservative: it reports paths that appear in both
    sets, which is a superset of what git would actually reject (some cases are
    fine if the file content matches). This avoids any mutation before the check.

    Args:
        cwd: Working directory inside the git repo.
        merge_ref: The ref to merge (e.g. ``origin/main``).

    Returns:
        Sorted list of colliding paths relative to the repo root (may be empty).
    """
    diff_result = _run_git(
        "diff",
        "--name-only",
        "--diff-filter=A",
        f"HEAD...{merge_ref}",
        cwd=cwd,
        check=False,
    )
    if diff_result.returncode != 0:
        return []
    added_files = {line for line in diff_result.stdout.splitlines() if line.strip()}
    if not added_files:
        return []

    status_result = _run_git("status", "--porcelain", cwd=cwd, check=False)
    if status_result.returncode != 0:
        return []
    untracked: set[str] = set()
    for line in status_result.stdout.splitlines():
        if not line.startswith("?? "):
            continue
        path = line[3:]
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        if not path.endswith("/"):
            untracked.add(path)

    return sorted(added_files & untracked)
