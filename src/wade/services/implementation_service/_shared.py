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
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.git.repo import GitError

__all__ = [
    "extract_issue_from_branch",
    "find_existing_branch_for_issue",
    "find_open_pr_branch_for_issue",
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
    """Extract the issue number from a branch name like ``feat/42-slug``.

    wade names branches ``{prefix}/{issue}-{slug}``, so the issue number is the
    ``/{digits}-`` segment — the digits immediately before the slug. Preferring
    that boundary keeps a *numeric* multi-segment prefix honest: e.g.
    ``release/2026`` yields ``release/2026/42-title``, which must resolve to
    ``42``, not the prefix's ``2026`` (#428 review). Fall back to a bare
    ``/{digits}`` for hyphenless branches (``feat/42``).
    """
    m = re.search(r"/(\d+)-", branch) or re.search(r"/(\d+)", branch)
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


# ``gh pr list`` fetches at most this many PRs per call. It comfortably exceeds
# any realistic open-PR count; a *full* page back means we cannot prove a missing
# issue PR is truly absent (it could lie beyond the page), so that case is treated
# as indeterminate rather than "no open PR" (#428 review).
_OPEN_PR_SCAN_LIMIT = 1000


def find_open_pr_branch_for_issue(repo_root: Path, issue_id: int | str) -> str | None:
    """Head branch of the OPEN PR that belongs to *issue_id*, or ``None``.

    Where :func:`find_existing_branch_for_issue` matches branches by name, this
    settles resume ambiguity by PR *state*: among any branches carrying the issue
    number, the live open PR is the one to resume — regardless of how the title
    has drifted or how many stale same-issue branches a closed PR left behind
    (which a branch-name-ordering tiebreak could otherwise pick). One
    ``gh pr list`` call.

    Returns ``None`` only when no open PR genuinely matches (nothing to resume —
    start fresh). Raises :class:`~wade.git.pr.GhCliError` when the result cannot be
    trusted as complete — either the listing failed, or a full page came back so an
    open PR could exist beyond it. Callers MUST NOT treat those as absence (a live
    PR may exist on another same-issue branch) and should abort/report rather than
    bootstrap a duplicate.
    """
    issue = str(int(issue_id))
    open_prs = git_pr.list_prs(
        repo_root, state="open", limit=_OPEN_PR_SCAN_LIMIT, raise_on_error=True
    )
    matches = [pr for pr in open_prs if extract_issue_from_branch(pr.head_ref_name) == issue]
    if matches:
        # Deterministic when several open PRs carry the issue number (rare): the
        # most recently updated wins. ``updated_at`` is an ISO-8601 string, so a
        # reverse lexical sort is chronological; the PR number breaks exact ties.
        matches.sort(key=lambda pr: (pr.updated_at or "", pr.number), reverse=True)
        return matches[0].head_ref_name
    if len(open_prs) >= _OPEN_PR_SCAN_LIMIT:
        # A full page with no match — an open PR for this issue could be beyond it,
        # so absence is unproven. Fail loud rather than risk a duplicate PR.
        raise git_pr.GhCliError(
            f"more than {_OPEN_PR_SCAN_LIMIT} open PRs — cannot rule out an open PR for #{issue}"
        )
    return None
