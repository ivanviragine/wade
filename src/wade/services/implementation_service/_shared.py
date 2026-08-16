"""Shared leaf helpers for the implementation service package.

Small, dependency-free resolution utilities used by several submodules
(``core``, ``cleanup``, ``done``, ``batch``). Kept here to avoid peer-to-peer
import cycles between those modules.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

from wade.git import branch as git_branch
from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.git.repo import GitError

__all__ = [
    "extract_issue_from_branch",
    "find_existing_branch_for_issue",
    "find_worktree_path",
    "resolve_task_branch",
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
        wt_branch = wt.branch or ""
        wt_path = wt.path

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


def find_existing_branch_for_issue(
    repo_root: Path, issue: str, preferred: str | None = None
) -> str | None:
    """Return the name of an existing branch that belongs to *issue*, or ``None``.

    Matches by the stable issue *number* — first the worktrees, then local and
    remote branches — never by a freshly slugified title. The slug is frozen into
    the branch at ``wade implement`` time, so a title edited afterward (commonly:
    the issue renamed to conventional-commit form when its PR opens) would make a
    reconstructed name drift from the real branch. A worktree's branch is
    preferred because it is the exact ref checked out for the issue.

    When more than one branch carries the issue number (e.g. a closed PR was
    retitled and implementation restarted), selection is deterministic:
    ``list_branch_names`` returns an unordered set, so prefer *preferred* — the
    name reconstructed from the issue's *current* title, i.e. the freshest
    branch — and otherwise fall back to sorted order rather than returning a
    hash-ordered (across-run nondeterministic) element.
    """
    with contextlib.suppress(GitError):
        for wt in git_worktree.list_worktrees(repo_root):
            if wt.branch and extract_issue_from_branch(wt.branch) == issue:
                return wt.branch

    # No live worktree (e.g. it was cleaned up while the PR stayed open) — fall
    # back to any local or remote branch carrying the issue number so callers
    # act on the *real* branch rather than a drifted name.
    with contextlib.suppress(GitError):
        matches: set[str] = set()
        for name in git_branch.list_branch_names(repo_root):
            short = name[len("origin/") :] if name.startswith("origin/") else name
            if extract_issue_from_branch(short) == issue:
                matches.add(short)
        if preferred is not None and preferred in matches:
            return preferred
        if matches:
            return sorted(matches)[0]

    return None


def resolve_task_branch(
    repo_root: Path,
    issue_id: int | str,
    title: str,
    branch_prefix: str,
) -> str:
    """Resolve the branch name for a task by its stable issue *number*.

    Resolution order:

    1. the currently checked-out branch, when it already carries this issue
       (an in-worktree caller — authoritative);
    2. an existing worktree / local / remote branch carrying this issue number
       (see :func:`find_existing_branch_for_issue`);
    3. a name reconstructed from the current title — only when nothing for this
       issue exists yet (first-time creation).

    Resolving by number rather than by re-slugifying ``title`` is deliberate: the
    branch slug is frozen at ``wade implement`` time, so a title edited afterward
    regenerates a *different* name and orphans the real worktree/PR. ``done``
    routinely rewrites the title to add the required conventional-commit prefix,
    so ``implement``/``smart_start`` re-runs would otherwise miss the live PR and
    re-bootstrap a duplicate.
    """
    issue = str(int(issue_id))

    with contextlib.suppress(GitError):
        current_branch = git_repo.get_current_branch(repo_root)
        if extract_issue_from_branch(current_branch) == issue:
            return current_branch

    # Reconstruct the current-title name once: it is both the tiebreaker
    # preference for find_existing_branch_for_issue (prefer the freshest branch
    # when an issue has several) and the fallback when no branch exists yet.
    reconstructed = git_branch.make_branch_name(branch_prefix, int(issue_id), title)
    existing = find_existing_branch_for_issue(repo_root, issue, preferred=reconstructed)
    return existing if existing is not None else reconstructed
