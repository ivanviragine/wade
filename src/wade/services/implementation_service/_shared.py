"""Shared leaf helpers for the implementation service package.

Small, dependency-free resolution utilities used by several submodules
(``core``, ``cleanup``, ``done``, ``batch``). Kept here to avoid peer-to-peer
import cycles between those modules.
"""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path

from pydantic import BaseModel

from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.git.repo import GitError
from wade.models.config import ProjectConfig
from wade.models.task import Task, TaskReference
from wade.utils.task_identity import task_id_from_branch, validate_task_id

__all__ = [
    "DraftPRTarget",
    "WorktreeTarget",
    "extract_issue_from_branch",
    "find_existing_branch_for_issue",
    "find_open_pr_branch_for_issue",
    "find_worktree_path",
    "is_explicit_plan_path",
    "match_worktree_target",
    "resolve_draft_pr_target",
    "resolve_task_branch",
    "task_reference",
]


def task_reference(
    config: ProjectConfig, task: Task | str, url: str | None = None
) -> TaskReference:
    """Build the PR-content reference for *task* under the configured provider.

    The configured task provider decides whether a task is a GitHub issue — never
    the shape of its ID or the presence of a GitHub PR delegate.
    """
    if isinstance(task, Task):
        task_id, url = task.id, task.url or url
    else:
        task_id = task
    return TaskReference(provider=str(config.provider.name), task_id=task_id, url=url or None)


def is_explicit_plan_path(target: str) -> bool:
    """Whether *target* uses explicit path syntax and may therefore name a plan file.

    A bare token (``42``, ``abc1234``) is always a task ID even when a file with
    that name exists in the cwd; pass ``./name`` to select such a file.
    """
    separators = {"/", os.sep} | ({os.altsep} if os.altsep else set())
    return (
        any(sep in target for sep in separators)
        or target.startswith(("~", "."))
        or target.lower().endswith(".md")
    )


class WorktreeTarget(BaseModel):
    """One worktree selected for a command target, and how it was selected."""

    path: Path
    branch: str
    task_id: str | None = None
    by_identity: bool = False


def match_worktree_target(
    target: str,
    project_root: Path | None = None,
    *,
    fuzzy: bool = True,
) -> WorktreeTarget | None:
    """Select the worktree for *target* with deterministic precedence.

    1. a worktree whose branch decodes to exactly the task ID *target*;
    2. a worktree whose branch name is exactly *target*;
    3. a worktree whose directory name is exactly *target*;
    4. only with ``fuzzy``: a boundary-aware directory-name fragment (navigation
       conveniences such as ``wade cd add-auth`` and plan-title lookup).

    Exact identity always wins over a name match, and task identity is never
    inferred from a numeric substring, so ``42`` does not select ``0042`` and an
    opaque ID cannot select another task's worktree whose title happens to
    contain it — unless the caller explicitly allows fuzzy name matching.
    """
    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        return None

    entries = [
        WorktreeTarget(
            path=Path(wt.path),
            branch=wt.branch or "",
            task_id=task_id_from_branch(wt.branch) if wt.branch else None,
        )
        for wt in git_worktree.list_worktrees(repo_root)
    ]
    for entry in entries:
        if entry.task_id is not None and entry.task_id == target:
            return entry.model_copy(update={"by_identity": True})
    for entry in entries:
        if entry.branch == target:
            return entry
    for entry in entries:
        if entry.path.name == target:
            return entry
    if fuzzy:
        # Boundary-aware so target="1" cannot match "feat-10-something".
        pattern = re.compile(rf"(?:^|-){re.escape(target)}(?:-|$)")
        for entry in entries:
            if pattern.search(entry.path.name):
                return entry
    return None


def find_worktree_path(
    target: str,
    project_root: Path | None = None,
    *,
    fuzzy: bool = True,
) -> Path | None:
    """Find the worktree path for a task ID, branch, or worktree name.

    See :func:`match_worktree_target` for the precedence rules.
    """
    match = match_worktree_target(target, project_root, fuzzy=fuzzy)
    return match.path if match is not None else None


def extract_issue_from_branch(branch: str) -> str | None:
    """Recover the exact task ID from a WADE branch name like ``feat/42-slug``.

    Compatibility wrapper over :func:`wade.utils.task_identity.task_id_from_branch`.
    Only the final segment's identity token is decoded, so a numeric
    multi-segment prefix stays honest (``release/2026/42-title`` -> ``42``,
    #428 review), ``feat/idv1_61626331323334-x`` -> ``abc1234``, and a
    malformed token never falls back to a numeric substring
    (``feat/123abc`` -> ``None``).
    """
    return task_id_from_branch(branch)


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
    """Resolve the branch name for a task by its stable, exact task ID.

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
    issue = validate_task_id(str(issue_id))

    with contextlib.suppress(GitError):
        current_branch = git_repo.get_current_branch(repo_root)
        if extract_issue_from_branch(current_branch) == issue:
            return current_branch

    # Reconstruct the current-title name once: it is both the tiebreaker
    # preference for find_existing_branch_for_issue (prefer the freshest branch
    # when an issue has several) and the fallback when no branch exists yet.
    reconstructed = git_branch.make_branch_name(branch_prefix, issue, title)
    existing = find_existing_branch_for_issue(repo_root, issue, preferred=reconstructed)
    return existing if existing is not None else reconstructed


# ``gh pr list`` fetches at most this many PRs per call. It comfortably exceeds
# any realistic open-PR count; a *full* page back means we cannot prove a missing
# issue PR is truly absent (it could lie beyond the page), so that case is treated
# as indeterminate rather than "no open PR" (#428 review).
_OPEN_PR_SCAN_LIMIT = 1000


def find_open_pr_branch_for_issue(
    repo_root: Path, issue_id: int | str, *, require_complete: bool = False
) -> str | None:
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
    bootstrap a duplicate. With ``require_complete=True``, even a full page
    containing a match refuses: replanning must not select from a truncated set.
    """
    issue = validate_task_id(str(issue_id))
    open_prs = git_pr.list_prs(
        repo_root, state="open", limit=_OPEN_PR_SCAN_LIMIT, raise_on_error=True
    )
    if require_complete and len(open_prs) >= _OPEN_PR_SCAN_LIMIT:
        raise git_pr.GhCliError("open PR listing is incomplete; retry with a complete result")
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
            f"more than {_OPEN_PR_SCAN_LIMIT} open PRs — cannot rule out an open PR for {issue}"
        )
    return None


class DraftPRTarget(BaseModel):
    """One task identity decision carried through guarded planning persistence."""

    branch_name: str
    lookup: git_pr.PRLookup


def resolve_draft_pr_target(
    repo_root: Path, issue_id: str, title: str, branch_prefix: str
) -> DraftPRTarget:
    """Resolve a live task PR without allowing title drift or failed lookup reuse."""
    branch = resolve_task_branch(repo_root, issue_id, title, branch_prefix)
    lookup = git_pr.get_pr_for_branch(repo_root, branch)
    if lookup.lookup_failed:
        raise git_pr.GhCliError(f"Could not look up the PR for branch {branch}")
    if lookup.is_open:
        return DraftPRTarget(branch_name=branch, lookup=lookup)
    live_branch = find_open_pr_branch_for_issue(repo_root, issue_id, require_complete=True)
    if live_branch is not None:
        live_lookup = git_pr.get_pr_for_branch(repo_root, live_branch)
        if live_lookup.lookup_failed or not live_lookup.is_open:
            raise git_pr.GhCliError(f"Could not verify the open PR for branch {live_branch}")
        return DraftPRTarget(branch_name=live_branch, lookup=live_lookup)
    return DraftPRTarget(
        branch_name=git_branch.make_branch_name(branch_prefix, issue_id, title),
        lookup=git_pr.PRLookup(),
    )
