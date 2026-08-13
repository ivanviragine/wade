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


def remote_ref_exists(repo_root: Path, branch_name: str, remote: str = "origin") -> bool:
    """Check whether a remote-tracking ref (``<remote>/<branch>``) exists locally.

    Args:
        repo_root: Repository root directory.
        branch_name: Short branch name (without the remote prefix).
        remote: Remote name (default ``origin``).

    Returns:
        True if ``refs/remotes/<remote>/<branch_name>`` resolves.
    """
    result = _run_git(
        "rev-parse",
        "--verify",
        f"refs/remotes/{remote}/{branch_name}",
        cwd=repo_root,
        check=False,
    )
    return result.returncode == 0


def resolve_start_point(repo_root: Path, base_branch: str) -> str | None:
    """Resolve a base branch to a local commit-ish usable as a branch start point.

    A base declared in a plan (e.g. ``develop``) may exist only as a remote
    tracking ref. ``git branch <new> develop`` will not resolve a remote-only
    ``develop`` (short names resolve local heads, not ``origin/develop``), so this
    prefers the local branch and falls back to ``origin/<base>``.

    Returns:
        ``base_branch`` if a local branch exists, ``origin/<base_branch>`` if only
        the remote ref exists, or ``None`` if neither is found.
    """
    if branch_exists(repo_root, base_branch):
        return base_branch
    if remote_ref_exists(repo_root, base_branch):
        return f"origin/{base_branch}"
    return None


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
    # Retry transient ref-lock contention from parallel sessions (C3).
    _run_git_with_retry("branch", flag, branch_name, cwd=repo_root)


def reset_branch(
    repo_root: Path,
    branch_name: str,
    start_point: str,
) -> None:
    """Force-move an existing local branch ref to *start_point* (``git branch -f``).

    Re-roots a branch without a checkout. The branch must not be checked out in any
    worktree (git refuses ``-f`` on a checked-out branch). Used to rebuild a
    scaffold-only branch on a new base before retargeting its PR, so the old base's
    commits do not leak into the new base's diff (#376).

    Args:
        repo_root: Repository root directory.
        branch_name: Existing local branch to move.
        start_point: Commit, branch, or tag to move the branch onto.

    Raises:
        GitError: If the branch is checked out or the move fails.
    """
    log.info("branch.reset", branch=branch_name, start_point=start_point)
    _run_git_with_retry("branch", "-f", branch_name, start_point, cwd=repo_root)


def reset_worktree_hard(worktree_path: Path, start_point: str) -> None:
    """Force-move the branch checked out in *worktree_path* to *start_point*.

    ``git branch -f`` refuses to move a checked-out branch, so re-root such a branch by
    running ``git reset --hard`` *inside its worktree* instead. This discards uncommitted
    changes to **tracked** files in that worktree (untracked files are left in place), so
    callers must confirm the worktree carries no tracked changes first (see
    :func:`wade.git.repo.has_tracked_changes`). Used to re-root a scaffold-only branch
    that is checked out — e.g. after ``wade implement --cd`` — before retargeting its PR,
    so the old base's commits don't leak into the new base's diff (#376 review).

    Args:
        worktree_path: The worktree in which the target branch is checked out.
        start_point: Commit, branch, or tag to move the branch (and worktree) onto.

    Raises:
        GitError: If the reset fails.
    """
    log.info("branch.reset_worktree_hard", worktree=str(worktree_path), start_point=start_point)
    _run_git_with_retry("reset", "--hard", start_point, cwd=worktree_path)


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


def list_branch_names(repo_root: Path) -> set[str]:
    """Return the set of all local and remote branch short-names.

    Runs ``git branch`` and ``git branch -r`` with lock-contention retry —
    this is called during batch polling while parallel sessions may hold git
    locks. An empty set means the repo genuinely has no branches; a query
    failure raises ``GitError`` so callers can distinguish "no branch exists"
    from "the branch query failed" (the latter must not be read as
    "not started").

    Args:
        repo_root: Repository root directory.

    Returns:
        Union of local and remote branch short-names (e.g. ``"main"``,
        ``"origin/feat/42-add-auth"``).

    Raises:
        GitError: If either branch listing fails.
    """
    names: set[str] = set()
    for extra_args in (["-r"], []):
        result = _run_git_with_retry(
            "branch", *extra_args, "--format=%(refname:short)", cwd=repo_root
        )
        names.update(line.strip() for line in result.stdout.splitlines() if line.strip())
    return names


def is_merged_into(repo_root: Path, branch: str, base: str) -> bool | None:
    """Return whether *branch*'s tip is an ancestor of *base* (i.e. merged in).

    Wraps ``git merge-base --is-ancestor <branch> <base>`` through the git layer
    (consistent process handling + logging) rather than a raw subprocess call.

    Returns:
        - ``True``  — branch is fully contained in base (exit 0): merged.
        - ``False`` — branch is definitively NOT an ancestor of base (exit 1).
        - ``None``  — indeterminate: a ref could not be resolved / git error
          (any other exit). Callers may then try a different base.
    """
    result = _run_git(
        "merge-base",
        "--is-ancestor",
        branch,
        base,
        cwd=repo_root,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def all_patches_present(repo_root: Path, base: str, branch: str) -> bool:
    """Return True when every commit on *branch* already has a patch on *base*.

    Wraps ``git cherry <base> <branch>``, which compares *patch ids* rather than
    commit ids — so it recognizes a squash- or rebase-merged branch whose tip is
    NOT an ancestor of *base*. ``git cherry`` marks an already-applied commit
    with ``-`` and a genuinely-absent one with ``+``; this returns True only
    when the command succeeds and no line starts with ``+``.

    Returns False on any git error (fail closed) and when *branch* has no
    commits to compare (empty output) — an empty result must never read as
    "safe to delete".

    Caveat: ``git cherry`` compares individual patch ids, so squashing several
    commits into one may still report ``+`` for each original commit. Treat a
    True result as authoritative; a False result may still be a squash merge, so
    callers fall back to it only *after* an exact ancestor check.
    """
    result = _run_git("cherry", base, branch, cwd=repo_root, check=False)
    if result.returncode != 0:
        return False
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    return bool(lines) and not any(ln.startswith("+") for ln in lines)


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
