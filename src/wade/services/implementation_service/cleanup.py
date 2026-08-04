"""Worktree listing, removal, and cleanup for implementation sessions."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import structlog
from crossby.ai_tools import AbstractAITool
from crossby.models.ai import AIToolID

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
) -> bool:
    """Remove a worktree.

    Modes:
    - target: remove a specific worktree by issue number or name
    - stale: remove all stale (non-active) worktrees
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
        return _remove_stale(repo_root, main_branch, force, get_provider(config))

    if target:
        return _remove_target(repo_root, target, main_branch, force)

    console.error("Specify a target or use --stale")
    return False


def _remove_target(repo_root: Path, target: str, main_branch: str, force: bool = False) -> bool:
    """Remove a specific worktree by issue number or name."""
    wt_path = find_worktree_path(target, project_root=repo_root)
    if not wt_path:
        console.error(f"No worktree found for: {target}")
        return False

    if not force and not prompts.confirm(f"Remove worktree {wt_path.name}?"):
        return False

    return _cleanup_worktree(repo_root, wt_path, main_branch)


def _remove_stale(
    repo_root: Path,
    main_branch: str,
    force: bool,
    provider: AbstractTaskProvider,
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
        if _cleanup_worktree(repo_root, Path(wt["path"]), main_branch):
            removed += 1

    console.panel(f"  Removed {removed} stale worktree(s)", title="Stale cleanup")
    return removed > 0


def _preserve_session_data(repo_root: Path, wt_path: Path) -> None:
    """Preserve AI tool session data before worktree removal.

    Queries the DB for the AI tool used in this worktree; falls back to
    directory-presence detection via ``session_data_dirs()``.  Calls the
    adapter's ``preserve_session_data()``.  Any failure is logged but never
    propagates — preservation must never block worktree deletion.
    """
    try:
        from wade.db.engine import get_or_create_engine
        from wade.db.repositories import SessionRepository

        engine = get_or_create_engine(repo_root)
        session_repo = SessionRepository(engine)

        sessions = session_repo.get_by_worktree_path(str(wt_path))

        adapter: AbstractAITool | None = None
        if sessions:
            latest = max(sessions, key=lambda s: s.started_at)
            with contextlib.suppress(ValueError, KeyError):
                adapter = AbstractAITool.get(AIToolID(latest.ai_tool))

        # Fallback: detect via session_data_dirs
        if adapter is None:
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


def _cleanup_worktree(repo_root: Path, wt_path: Path, main_branch: str) -> bool:
    """Remove a single worktree and its branch."""
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

    _preserve_session_data(main_root, wt_path)

    try:
        git_worktree.remove_worktree(main_root, wt_path)
    except GitError as e:
        console.warn(f"Worktree removal failed: {e}")
        return False

    if branch_name and branch_name != main_branch:
        with contextlib.suppress(GitError):
            git_branch.delete_branch(main_root, branch_name, force=True)

    with contextlib.suppress(GitError):
        git_worktree.prune_worktrees(main_root)

    console.success(f"Removed {wt_path.name}")
    return True
