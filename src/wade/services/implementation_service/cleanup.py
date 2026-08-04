"""Worktree listing, removal, and cleanup for implementation sessions."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import structlog
from crossby.ai_tools import AbstractAITool

from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.git.repo import GitError
from wade.models.session import WorktreeState
from wade.models.task import Task
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services.implementation_service._shared import (
    extract_issue_from_branch,
    find_worktree_path,
)
from wade.services.implementation_service.sync import classify_staleness
from wade.ui import prompts
from wade.ui.console import console

logger = structlog.get_logger()

__all__ = [
    "_cleanup_worktree",
    "_preserve_session_data",
    "_remove_stale",
    "_remove_target",
    "list_sessions",
    "remove",
]


def list_sessions(
    show_all: bool = False,
    json_output: bool = False,
    project_root: Path | None = None,
    silent: bool = False,
) -> list[dict[str, Any]]:
    """List active implementation sessions / worktrees.

    Returns a list of dicts with worktree info (path, branch, issue, staleness).
    When *silent* is True, skips all console output (useful for callers that
    only need the data, e.g. interactive pickers).
    """
    config = load_config(project_root)
    cwd = project_root or Path.cwd()

    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error("Not inside a git repository")
        return []

    main_branch = config.project.main_branch
    if not main_branch:
        try:
            main_branch = git_repo.detect_main_branch(repo_root)
        except GitError:
            main_branch = "main"

    worktrees = git_worktree.list_worktrees(repo_root)
    sessions: list[dict[str, Any]] = []
    provider_inst = get_provider(config)

    # The first worktree is the main checkout — skip unless --all
    for i, wt in enumerate(worktrees):
        wt_branch = wt.get("branch", "")
        wt_path = wt.get("path", "")

        # Skip main checkout unless --all
        if i == 0 and not show_all:
            continue

        # Skip non-wade branches unless --all
        issue_number = extract_issue_from_branch(wt_branch)
        if not issue_number and not show_all:
            continue

        # Fetch issue info once (for both staleness classification and display)
        task_info: Task | None = None
        issue_state: str | None = None
        issue_title: str | None = None
        task_lookup_attempted = False
        task_lookup_failed = False
        if issue_number:
            task_lookup_attempted = True
            try:
                task_info = provider_inst.read_task_or_none(issue_number)
            except Exception:
                logger.debug(
                    "implementation.list_issue_read_failed",
                    issue=issue_number,
                    branch=wt_branch,
                    exc_info=True,
                )
                task_info = None
                task_lookup_failed = True
            if task_info:
                issue_state = task_info.state.value
                issue_title = task_info.title

        staleness = classify_staleness(
            repo_root=repo_root,
            branch=wt_branch,
            main_branch=main_branch,
            issue_number=issue_number,
            provider=provider_inst,
            task=task_info,
            task_lookup_attempted=task_lookup_attempted,
            task_lookup_failed=task_lookup_failed,
        )

        # Count commits ahead
        try:
            ahead = git_branch.commits_ahead(repo_root, wt_branch, main_branch)
        except GitError:
            ahead = 0

        session_info = {
            "path": wt_path,
            "branch": wt_branch,
            "issue": issue_number,
            "issue_state": issue_state,
            "issue_title": issue_title,
            "staleness": staleness.value,
            "commits_ahead": ahead,
        }
        sessions.append(session_info)

    if silent:
        return sessions

    if json_output:
        console.raw(json.dumps(sessions, indent=2) + "\n")
        return sessions

    if not sessions:
        console.info("No active wade worktrees found.")
        return sessions

    console.rule(f"Implementation sessions ({len(sessions)})")
    for s in sessions:
        staleness_label = s["staleness"].upper().replace("_", " ")
        issue_str = f"#{s['issue']}" if s["issue"] else "(no issue)"
        state_str = f" [{s['issue_state'].upper()}]" if s.get("issue_state") else ""
        title_str = f" {s['issue_title']}" if s.get("issue_title") else ""
        console.step(f"[{staleness_label}] {issue_str}{state_str}{title_str}")
        console.detail(f"Path: {s['path']}")
        console.detail(f"Branch: {s['branch']} ({s['commits_ahead']} commit(s) ahead)")

    return sessions


def remove(
    target: str | None = None,
    stale: bool = False,
    force: bool = False,
    project_root: Path | None = None,
    discard_dirty: bool = False,
) -> bool:
    """Remove a worktree.

    Modes:
    - target: remove a specific worktree by issue number or name
    - stale: remove all stale (non-active) worktrees

    ``force`` only skips the interactive confirmation; it never discards work.
    ``discard_dirty`` is the separate opt-in that permits removing a worktree
    with uncommitted changes or unmerged local commits (A2).
    """
    config = load_config(project_root)
    cwd = project_root or Path.cwd()

    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error("Not inside a git repository")
        return False

    main_branch = config.project.main_branch
    if not main_branch:
        try:
            main_branch = git_repo.detect_main_branch(repo_root)
        except GitError:
            main_branch = "main"

    if stale:
        return _remove_stale(
            repo_root, main_branch, force, get_provider(config), discard_dirty=discard_dirty
        )

    if target:
        return _remove_target(repo_root, target, main_branch, force, discard_dirty=discard_dirty)

    console.error("Specify a target or use --stale")
    return False


def _remove_target(
    repo_root: Path,
    target: str,
    main_branch: str,
    force: bool = False,
    *,
    discard_dirty: bool = False,
) -> bool:
    """Remove a specific worktree by issue number or name."""
    wt_path = find_worktree_path(target, project_root=repo_root)
    if not wt_path:
        console.error(f"No worktree found for: {target}")
        return False

    if not force and not prompts.confirm(f"Remove worktree {wt_path.name}?"):
        return False

    return _cleanup_worktree(repo_root, wt_path, main_branch, discard_dirty=discard_dirty)


def _remove_stale(
    repo_root: Path,
    main_branch: str,
    force: bool,
    provider: AbstractTaskProvider,
    *,
    discard_dirty: bool = False,
) -> bool:
    """Remove all stale worktrees."""
    worktrees = git_worktree.list_worktrees(repo_root)
    stale_wts: list[dict[str, Any]] = []

    for i, wt in enumerate(worktrees):
        if i == 0:
            continue  # Skip main

        wt_branch = wt.get("branch", "")
        wt_path = wt.get("path", "")
        issue_number = extract_issue_from_branch(wt_branch)

        # Look up the issue exactly as list_sessions() does so classify_staleness
        # can honor its fail-safe contract: an open issue is never STALE_EMPTY.
        # Without the provider, an open issue with zero commits would be
        # misclassified STALE_EMPTY and deleted by --force.
        task_info: Task | None = None
        task_lookup_attempted = False
        task_lookup_failed = False
        if issue_number:
            task_lookup_attempted = True
            try:
                task_info = provider.read_task_or_none(issue_number)
            except Exception:
                logger.debug(
                    "implementation.remove_stale_issue_read_failed",
                    issue=issue_number,
                    branch=wt_branch,
                    exc_info=True,
                )
                task_info = None
                task_lookup_failed = True

        staleness = classify_staleness(
            repo_root=repo_root,
            branch=wt_branch,
            main_branch=main_branch,
            issue_number=issue_number,
            provider=provider,
            task=task_info,
            task_lookup_attempted=task_lookup_attempted,
            task_lookup_failed=task_lookup_failed,
        )

        if staleness != WorktreeState.ACTIVE:
            stale_wts.append(
                {
                    "path": wt_path,
                    "branch": wt_branch,
                    "staleness": staleness.value,
                }
            )

    if not stale_wts:
        console.info("No stale worktrees found.")
        return True

    console.rule(f"Stale worktrees ({len(stale_wts)})")
    for wt in stale_wts:
        console.step(f"[{wt['staleness'].upper()}] {wt['branch']}")
        console.detail(f"Path: {wt['path']}")

    if not force:
        console.info("Use --force to remove these worktrees.")
        return True

    removed = 0
    for wt in stale_wts:
        # A2: even with --force (skip confirmation), _cleanup_worktree still
        # refuses a worktree carrying uncommitted changes or unmerged local
        # commits unless --discard-dirty was passed. STALE_REMOTE_GONE says
        # nothing about local commits, so a remote-gone branch with unpushed
        # work is preserved here, not silently nuked.
        if _cleanup_worktree(repo_root, Path(wt["path"]), main_branch, discard_dirty=discard_dirty):
            removed += 1

    console.panel(f"  Removed {removed} stale worktree(s)", title="Stale cleanup")
    return removed > 0


def _preserve_session_data(repo_root: Path, wt_path: Path) -> None:
    """Preserve AI tool session data before worktree removal.

    Detects the AI tool that ran in this worktree by directory presence
    (``session_data_dirs()``) and calls the adapter's
    ``preserve_session_data()``.  Any failure is logged but never propagates —
    preservation must never block worktree deletion.

    (C5b) The old SQLite ``SessionRepository.get_by_worktree_path`` lookup was
    removed: nothing in wade ever wrote a session record, so it always returned
    empty and the directory-presence detection below is the only real path.
    """
    try:
        adapter: AbstractAITool | None = None
        for tool_id in AbstractAITool.available_tools():
            candidate = AbstractAITool.get(tool_id)
            for dir_name in candidate.session_data_dirs():
                if (wt_path / dir_name).exists():
                    adapter = candidate
                    break
            if adapter is not None:
                break

        if adapter is None:
            return

        adapter.preserve_session_data(wt_path, repo_root)
    except Exception:
        logger.warning(
            "worktree.preserve_session_data_failed",
            worktree=str(wt_path),
            exc_info=True,
        )


def _worktree_loss_risk(
    main_root: Path,
    wt_path: Path,
    branch_name: str | None,
    main_branch: str,
) -> list[str]:
    """Return human-readable descriptions of work removing this worktree destroys.

    An empty list means removal is safe: a clean working tree AND a branch that
    is either empty (zero commits ahead of main) or fully merged into main.
    A non-empty list names each hazard — uncommitted changes and/or unmerged
    local commits — so the caller can refuse and tell the user exactly what
    ``--discard-dirty`` would throw away (A2).
    """
    losses: list[str] = []

    # 1. Uncommitted changes in the worktree's working directory.
    if wt_path.is_dir():
        try:
            if not git_repo.is_clean(wt_path):
                status = git_repo.get_dirty_status(wt_path)
                losses.append(
                    f"uncommitted changes ({status['staged']} staged, "
                    f"{status['unstaged']} unstaged, {status['untracked']} untracked)"
                )
        except GitError:
            losses.append("uncommitted changes (could not verify — treating as unsafe)")

    # 2. Local commits not merged into main — would be lost by `git branch -D`.
    if branch_name and branch_name != main_branch:
        # Prefer the remote-tracking base: a local main that is behind origin
        # makes an already-merged branch look unmerged (a false loss).
        base_ref = main_branch
        with contextlib.suppress(GitError):
            git_repo.rev_parse(main_root, f"origin/{main_branch}")
            base_ref = f"origin/{main_branch}"
        try:
            ahead = git_branch.commits_ahead(main_root, branch_name, base_ref)
        except GitError:
            # Fail CLOSED — an unverifiable branch state is treated as unsafe,
            # same as the is_clean check above. Never read "could not check" as
            # "no work to lose".
            losses.append(f"could not verify commits on '{branch_name}' — treating as unsafe")
            return losses
        if ahead > 0:
            merged = False
            try:
                mb = git_repo.merge_base(main_root, branch_name, base_ref)
                tip = git_repo.rev_parse(main_root, branch_name)
                merged = mb == tip
            except GitError:
                merged = False
            if not merged:
                # Squash/rebase merge: the branch tip is not an ancestor of the
                # base, yet every patch is already applied there. `git cherry`
                # (patch-id based) recognizes this — the completion path for this
                # PR is squash-merge, so this is the routine cleanup case, not a
                # loss. Fails closed on any git error.
                merged = git_branch.all_patches_present(main_root, base_ref, branch_name)
            if not merged:
                losses.append(f"{ahead} local commit(s) not merged into {base_ref}")

    return losses


def _cleanup_worktree(
    repo_root: Path,
    wt_path: Path,
    main_branch: str,
    *,
    discard_dirty: bool = False,
) -> bool:
    """Remove a single worktree and its branch.

    Refuses to remove a worktree with uncommitted changes or unmerged local
    commits unless ``discard_dirty`` is set, naming exactly what would be lost
    (A2). ``--force`` (skip confirmation) never implies ``discard_dirty``.
    """
    console.step(f"Removing worktree: {wt_path}")

    # Worktree removal, branch deletion, and pruning must run against the main
    # checkout — not a linked worktree root — or git refuses / corrupts state.
    main_root = git_repo.main_checkout_root(repo_root)

    # Find the branch name for this worktree
    worktrees = git_worktree.list_worktrees(main_root)
    branch_name: str | None = None
    for wt in worktrees:
        if wt.get("path") == str(wt_path):
            branch_name = wt.get("branch")
            break

    # A2 loss guard — never silently discard a dirty worktree or force-delete a
    # branch carrying unmerged commits.
    losses = _worktree_loss_risk(main_root, wt_path, branch_name, main_branch)
    if losses and not discard_dirty:
        console.error(f"Refusing to remove {wt_path.name} — this would lose work:")
        for loss in losses:
            console.detail(loss)
        console.hint("Push/commit the work, or re-run with --discard-dirty to discard it.")
        return False

    _preserve_session_data(main_root, wt_path)

    try:
        git_worktree.remove_worktree(main_root, wt_path)
    except GitError as e:
        console.warn(f"Worktree removal failed: {e}")
        return False

    if branch_name and branch_name != main_branch:
        # `-d` refuses to delete an unmerged branch (safe); `-D` (force) is used
        # ONLY when the caller explicitly opted into discarding work. Never
        # escalate a `-d` refusal to `-D` automatically — that would bypass the
        # discard_dirty gate and could force-delete unmerged commits (the loss
        # guard already refused branches it could verify as unmerged, but a
        # refusal here — e.g. a transient error, or main_root's local main being
        # behind — must not become a silent force-delete). A lingering branch is
        # not data loss; a blind `-D` could be.
        try:
            git_branch.delete_branch(main_root, branch_name, force=discard_dirty)
        except GitError as e:
            console.warn(f"Could not delete branch '{branch_name}': {e}")
            console.hint(
                f"If it is safe, delete it manually: git branch -d {branch_name} (or -D to force)."
            )

    with contextlib.suppress(GitError):
        git_worktree.prune_worktrees(main_root)

    console.success(f"Removed {wt_path.name}")
    return True
