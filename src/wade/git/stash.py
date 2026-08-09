"""Git stash helpers for wade auto-stash operations.

The stash stack lives in ``$GIT_COMMON_DIR`` — every linked worktree of a repo
shares ONE stack. A positional ``stash@{N}`` ref held across the create→restore
window is unsafe: an intervening ``git stash push`` from another worktree (or
the user) shifts the positions, so the held ref would restore *someone else's*
work. wade therefore identifies its stash by its **commit SHA** (content-
addressed, never shifts) and by a unique message, never by position.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import structlog

from wade.git.repo import GitError, _index_lock_present, _run_git, _run_git_with_retry

log = structlog.get_logger(__name__)

AUTOSTASH_PREFIX = "wade-autostash"


def _stash_message(session_type: str, branch: str) -> str:
    # Include the PID so parallel worktrees (wade implement-batch) never collide
    # on the same message within the same second.
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    safe_branch = branch.replace("/", "_")
    return f"{AUTOSTASH_PREFIX}/{session_type}/{safe_branch}/{ts}-{os.getpid()}"


def create_named_stash(session_type: str, branch: str, cwd: Path) -> tuple[str, str]:
    """Stash staged+unstaged tracked-file changes and return (stash_sha, message).

    The returned SHA is the stash *commit* SHA — a content hash that is stable
    regardless of how the shared stash stack is later reordered by other
    worktrees. Restore with :func:`apply_stash_by_sha` (never a held positional
    ref).

    Untracked files are intentionally left in place — call
    detect_untracked_collisions() before this to ensure they won't conflict.

    Args:
        session_type: Session type label (e.g. "implementation").
        branch: Current branch name (embedded in the stash message).
        cwd: Working directory inside the git repo.

    Returns:
        Tuple of (stash_sha, stash_message).

    Raises:
        GitError: If the stash command fails or nothing was stashed.
    """
    message = _stash_message(session_type, branch)
    # Retry transient index-lock contention from parallel worktrees (C3).
    # probe_index_lock=True: older git (2.43.0) fails `stash push` under a held
    # index.lock with empty stderr, so stderr matching alone misses the
    # contention; the direct lock-file probe is version-independent (#374).
    result = _run_git_with_retry(
        "stash", "push", "-m", message, cwd=cwd, check=False, probe_index_lock=True
    )
    if result.returncode != 0:
        reason = result.stderr.strip()
        if not reason:
            # Older git swallows the child's stderr on a locked index, leaving no
            # reason to surface. Report the exit code and whether a lock file is
            # present so the failure is never blank (#374).
            locked = _index_lock_present(cwd)
            reason = f"exit {result.returncode}, no stderr; " + (
                "index.lock present (lock contention)" if locked else "no index.lock detected"
            )
        raise GitError(f"git stash push failed: {reason}")
    stdout = result.stdout.strip()
    if stdout == "No local changes to save":
        raise GitError("No local changes to save")
    sha = _find_stash_sha(message, cwd)
    if sha is None:
        raise GitError(f"Stash created but SHA not found for: {message!r}")
    log.debug("git.stash.created", sha=sha, message=message)
    return sha, message


def _find_stash_sha(message: str, cwd: Path) -> str | None:
    """Return the commit SHA of the stash whose subject contains *message*."""
    result = _run_git("stash", "list", "--format=%H %gs", cwd=cwd, check=False)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        sha, subject = parts[0], parts[1]
        if message in subject:
            return sha
    return None


def _find_stash_ref_by_sha(stash_sha: str, cwd: Path) -> str | None:
    """Return the positional ref (``stash@{N}``) whose commit SHA equals *stash_sha*.

    Re-resolved immediately before a drop so it always reflects the *current*
    stack ordering — never a stale position captured at creation time.
    """
    result = _run_git("stash", "list", "--format=%H %gd", cwd=cwd, check=False)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        entry_sha, ref = parts[0], parts[1].strip()
        if entry_sha == stash_sha:
            return ref
    return None


def apply_stash_by_sha(stash_sha: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Apply the stash identified by *stash_sha* (content-addressed).

    Uses ``git stash apply <sha>``: a raw stash-commit SHA restores exactly the
    stashed content no matter how the stack was reordered — the A1 race cannot
    apply the wrong changes. Does NOT drop the entry (call
    :func:`drop_stash_by_sha` only after a clean apply), so an apply conflict
    leaves the stash intact for recovery. Returns the CompletedProcess (never
    raises) so callers can surface a failed/conflicted apply.
    """
    return _run_git("stash", "apply", stash_sha, cwd=cwd, check=False)


def drop_stash_by_sha(stash_sha: str, cwd: Path) -> bool:
    """Drop the stash entry whose commit SHA equals *stash_sha*.

    Re-resolves the positional ref at call time (never a held one). Returns True
    if an entry was found and dropped; False if no matching entry exists (e.g.
    it was already dropped) or the drop failed.

    ``git`` has no drop-by-SHA primitive, so the resolved ``stash@{N}`` is
    re-verified immediately before the drop. A concurrent ``stash push`` that
    shifts positions between the two commands then makes the drop a no-op
    (return False) instead of removing a different, unrelated entry.
    """
    ref = _find_stash_ref_by_sha(stash_sha, cwd)
    if ref is None:
        return False
    # Re-verify: the position must still resolve to the same stash commit. This
    # narrows the shared-stack reordering window (A1) to a single command
    # boundary — a wrong drop becomes a no-op in the common case.
    check = _run_git("rev-parse", "--verify", f"{ref}^{{commit}}", cwd=cwd, check=False)
    if check.returncode != 0 or check.stdout.strip() != stash_sha:
        log.debug("git.stash.drop_skipped_reordered", sha=stash_sha, ref=ref)
        return False
    result = _run_git("stash", "drop", ref, cwd=cwd, check=False)
    return result.returncode == 0


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
