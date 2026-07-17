"""Implementation sync / catchup — merge base branch into the worktree branch.

Also hosts worktree staleness classification, shared by the cleanup module.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import repo as git_repo
from wade.git import stash as git_stash
from wade.git import sync as git_sync
from wade.git.repo import GitError
from wade.models.session import (
    SyncEvent,
    SyncEventType,
    SyncResult,
    WorktreeState,
)
from wade.models.task import Task
from wade.providers.base import AbstractTaskProvider
from wade.services.implementation_service.bootstrap import (
    _check_tracked_managed_files,
    _format_uncommitted_summary,
    _get_dirty_file_paths,
    _identify_session_dirty_files,
)
from wade.ui.console import console

logger = structlog.get_logger()

__all__ = [
    "_merge_base",
    "_sync_preflight",
    "catchup",
    "classify_staleness",
    "sync",
]


def classify_staleness(
    repo_root: Path,
    branch: str,
    main_branch: str,
    issue_number: str | None = None,
    provider: AbstractTaskProvider | None = None,
    task: Task | None = None,
    task_lookup_attempted: bool = False,
    task_lookup_failed: bool = False,
) -> WorktreeState:
    """Classify a worktree's staleness.

    Returns one of:
    - ACTIVE — issue is open or could not determine
    - STALE_EMPTY — no commits ahead of main
    - STALE_MERGED — branch merged into main
    - STALE_REMOTE_GONE — remote tracking branch deleted

    If task_lookup_failed is True, issue state is treated as unknown and the
    worktree is kept ACTIVE as a fail-safe.
    If task_lookup_attempted is True, *task* is treated as the final result of
    that lookup (including None for deleted/missing issues) and no re-fetch
    occurs.
    If task_lookup_attempted is False but issue_number and provider are
    provided, the task is fetched on demand.
    """
    from wade.models.task import TaskState

    # 1. If issue number, check issue state
    if issue_number and provider:
        if task_lookup_failed:
            return WorktreeState.ACTIVE

        # Use provided lookup result (including None for deleted issues),
        # otherwise fetch it on demand.
        if task_lookup_attempted:
            issue_task = task
        else:
            try:
                issue_task = provider.read_task(issue_number)
            except Exception:
                logger.debug("staleness.issue_read_failed", issue=issue_number, exc_info=True)
                # Can't read issue — treat as active (fail-safe)
                return WorktreeState.ACTIVE

        if issue_task is not None and issue_task.state == TaskState.OPEN:
            return WorktreeState.ACTIVE

    # 2. Count commits ahead of main
    try:
        ahead = git_branch.commits_ahead(repo_root, branch, main_branch)
    except GitError:
        return WorktreeState.ACTIVE

    if ahead == 0:
        return WorktreeState.STALE_EMPTY

    # 3. Check if merged (merge-base equals branch tip)
    try:
        mb = git_repo.merge_base(repo_root, branch, main_branch)
        tip = git_repo.rev_parse(repo_root, branch)
        if mb == tip:
            return WorktreeState.STALE_MERGED
    except GitError:
        logger.debug("staleness.merge_base_check_failed", exc_info=True)

    # 4. Check if remote tracking branch gone
    try:
        tracking = git_repo.upstream_tracking_status(repo_root, branch)
        if tracking == "gone":
            return WorktreeState.STALE_REMOTE_GONE
    except GitError:
        logger.debug("staleness.remote_tracking_check_failed", exc_info=True)

    return WorktreeState.ACTIVE


class _DirtyCategory(StrEnum):
    """Categorization of the working tree's dirty state for sync pre-flight."""

    CLEAN = "clean"
    ARTIFACTS_ONLY = "artifacts_only"
    USER_DIRTY = "user_dirty"


class _PreflightOK(BaseModel):
    """Successful pre-flight result — git repo and branch checks passed."""

    repo_root: Path
    cwd: Path
    current_branch: str
    resolved_main: str
    dirty_category: _DirtyCategory
    session_files: list[str] = Field(default_factory=list)
    dirty_paths: list[str] = Field(default_factory=list)
    has_tracked_changes: bool = False


def _sync_preflight(
    cwd: Path,
    main_branch_override: str | None,
    config: Any,
    emit: Any,
) -> _PreflightOK | SyncResult:
    """Run pre-flight checks shared by sync() and catchup().

    Resolves the repo root, current branch, and base branch. Categorizes the
    working-tree dirty state but does NOT make the pass/fail decision for dirty
    trees — callers decide whether to auto-stash or error based on the returned
    _DirtyCategory.

    Emits ERROR events via ``emit`` on hard failures (not_git_repo, detached_head,
    no_main_branch, on_main_branch).

    Returns:
        _PreflightOK on success (caller handles dirty_category).
        SyncResult(success=False) on hard failure.
    """
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        emit(SyncEventType.ERROR, reason="not_git_repo")
        return SyncResult(success=False, current_branch="", main_branch=main_branch_override or "")

    try:
        current = git_repo.get_current_branch(cwd)
    except GitError:
        emit(SyncEventType.ERROR, reason="detached_head")
        return SyncResult(success=False, current_branch="", main_branch=main_branch_override or "")

    # Stacked branch: prefer the stored parent branch over main.
    main_branch = main_branch_override
    if not main_branch:
        base_branch_file = cwd / ".wade" / "base_branch"
        if base_branch_file.is_file():
            stored_base = base_branch_file.read_text().strip()
            if stored_base and git_branch.branch_exists(repo_root, stored_base):
                main_branch = stored_base

    resolved_main = main_branch or config.project.main_branch
    if not resolved_main:
        try:
            resolved_main = git_repo.detect_main_branch(repo_root)
        except GitError:
            emit(SyncEventType.ERROR, reason="no_main_branch")
            return SyncResult(success=False, current_branch=current, main_branch="")

    if current == resolved_main:
        emit(SyncEventType.ERROR, reason="on_main_branch")
        return SyncResult(success=False, current_branch=current, main_branch=resolved_main)

    if git_repo.is_clean(cwd):
        return _PreflightOK(
            repo_root=repo_root,
            cwd=cwd,
            current_branch=current,
            resolved_main=resolved_main,
            dirty_category=_DirtyCategory.CLEAN,
        )

    dirty_paths = _get_dirty_file_paths(cwd)
    session_files = _identify_session_dirty_files(dirty_paths)
    non_session_paths = [p for p in dirty_paths if p not in set(session_files)]

    if not non_session_paths:
        return _PreflightOK(
            repo_root=repo_root,
            cwd=cwd,
            current_branch=current,
            resolved_main=resolved_main,
            dirty_category=_DirtyCategory.ARTIFACTS_ONLY,
            session_files=session_files,
            dirty_paths=dirty_paths,
        )

    # Determine if staged/unstaged tracked changes exist (stash is only useful for these).
    dirty_status = git_repo.get_dirty_status(cwd)
    has_tracked_changes = dirty_status["staged"] > 0 or dirty_status["unstaged"] > 0
    return _PreflightOK(
        repo_root=repo_root,
        cwd=cwd,
        current_branch=current,
        resolved_main=resolved_main,
        dirty_category=_DirtyCategory.USER_DIRTY,
        session_files=session_files,
        dirty_paths=dirty_paths,
        has_tracked_changes=has_tracked_changes,
    )


def _emit_dirty_worktree_error(
    emit: Any,
    preflight: _PreflightOK,
    json_output: bool,
) -> None:
    """Emit ERROR dirty_worktree event and console output (--no-stash path)."""
    detail_str = _format_uncommitted_summary(preflight.cwd)
    if preflight.session_files:
        emit(
            SyncEventType.ERROR,
            reason="dirty_worktree",
            details=detail_str,
            session_files=preflight.session_files,
        )
    else:
        emit(SyncEventType.ERROR, reason="dirty_worktree", details=detail_str)
    if not json_output:
        console.error(f"Working tree is dirty ({detail_str})")
        if preflight.session_files:
            console.warn(
                "The following dirty files are wade session artifacts"
                " \N{EM DASH} do NOT commit them."
            )
            console.hint("Restore with: git checkout -- <file>")
            for sf in preflight.session_files:
                console.detail(sf)
            console.empty()
        console.hint(
            "Commit or stash your non-session changes first"
            if preflight.session_files
            else "Commit or stash your changes first"
        )


def _handle_stash_restoration(
    stash_ref: str,
    cwd: Path,
    result_success: bool,
    emit: Any,
    json_output: bool,
) -> bool:
    """Pop the stash and emit events. Returns True if stash was cleanly restored.

    On a stash-pop conflict the stash is left in place and STASH_LEFT_BEHIND is
    emitted. Callers should treat a False return as a sync failure.
    """
    pop_result = git_stash.pop_stash(stash_ref, cwd)
    if pop_result.returncode == 0:
        emit(SyncEventType.STASH_RESTORED, stash_ref=stash_ref)
        if not json_output:
            console.success("Restored stashed changes.")
        return True

    recovery_cmd = f"git stash apply {stash_ref}"
    emit(
        SyncEventType.STASH_LEFT_BEHIND,
        stash_ref=stash_ref,
        recovery_hint=recovery_cmd,
    )
    if not json_output:
        if result_success:
            console.error("Conflict while restoring stash \N{EM DASH} changes preserved:")
        else:
            console.error(
                "Could not restore stash after failed merge \N{EM DASH} changes preserved:"
            )
        console.hint(f"Recover with: {recovery_cmd}")
    return False


def _merge_base(
    repo_root: Path,
    current: str,
    resolved_main: str,
    emit: Any,
    *,
    dry_run: bool = False,
    json_output: bool = False,
    abort_on_conflict: bool = False,
    session_type: str = "implementation",
    already_fetched: bool = False,
) -> SyncResult:
    """Fetch, count commits behind, and merge base branch into current branch.

    Shared by both sync() and catchup(). Pre-flight checks are the caller's
    responsibility. Emits events via the provided ``emit`` callable (which
    also populates the caller's events list).

    Args:
        repo_root: Repository root.
        current: Current branch name.
        resolved_main: The resolved base/main branch name.
        emit: Callable(event, **data) that records events.
        dry_run: Preview without merging.
        json_output: Suppress console output (JSON mode).
        abort_on_conflict: When True, abort the merge on conflict (catchup
            path) so the worktree stays clean. When False (sync path), leave
            the merge in progress for the AI to resolve manually.
        session_type: Used in the conflict hint message.
        already_fetched: Skip the fetch step — the caller already fetched
            origin (e.g. for the autostash collision probe) and the local
            ``origin/<main>`` ref is fresh enough for the merge.

    Returns:
        SyncResult with success/conflicts/commits_merged (events=[]).
    """
    # Fetch
    merge_ref = resolved_main
    try:
        has_remote = git_repo.has_remote(repo_root)
    except GitError:
        has_remote = False

    if has_remote:
        if already_fetched:
            merge_ref = f"origin/{resolved_main}"
        else:
            try:
                git_sync.fetch_origin(repo_root)
                merge_ref = f"origin/{resolved_main}"
                if not json_output:
                    console.detail(f"Fetched latest from origin/{resolved_main}")
            except GitError:
                if not json_output:
                    console.warn("Fetch failed; using local main")

    # Count commits behind
    try:
        behind = git_branch.commits_ahead(repo_root, merge_ref, current)
    except GitError as exc:
        # Never report an unverified comparison as UP_TO_DATE — surface the
        # failure so the caller does not treat the sync as successful.
        emit(SyncEventType.ERROR, reason="behind_count_failed", details=str(exc))
        return SyncResult(
            success=False,
            current_branch=current,
            main_branch=resolved_main,
        )

    if behind == 0:
        emit(SyncEventType.UP_TO_DATE, branch=current, main=resolved_main)
        if not json_output:
            console.success("Already up to date.")
        emit(SyncEventType.DONE, branch=current, main=resolved_main)
        return SyncResult(success=True, current_branch=current, main_branch=resolved_main)

    if dry_run:
        emit(SyncEventType.DRY_RUN, action="merge_main_into_feature", behind=behind)
        if not json_output:
            console.info(f"Dry run: {behind} commit(s) would be merged.")
        emit(SyncEventType.DONE, branch=current, main=resolved_main)
        return SyncResult(success=True, current_branch=current, main_branch=resolved_main)

    # Merge
    if not json_output:
        console.step(f"Merging {merge_ref} ({behind} commit(s) behind)")

    merge_result = git_sync.merge_branch(repo_root, merge_ref)

    if merge_result.success:
        emit(SyncEventType.MERGED, commits_merged=behind)
        if not json_output:
            console.success(f"Merged {behind} commit(s).")
        emit(SyncEventType.DONE, branch=current, main=resolved_main)
        return SyncResult(
            success=True,
            current_branch=current,
            main_branch=resolved_main,
            commits_merged=behind,
        )

    # Conflicts
    conflicts = merge_result.conflicts

    if abort_on_conflict:
        try:
            git_sync.abort_merge(repo_root)
        except GitError:
            logger.error("catchup.abort_merge_failed", exc_info=True)
            emit(SyncEventType.ERROR, reason="abort_merge_failed")
            return SyncResult(
                success=False,
                current_branch=current,
                main_branch=resolved_main,
            )

    emit(SyncEventType.CONFLICT, source=resolved_main, target=current, files="\n".join(conflicts))
    if not json_output:
        console.error(f"Merge conflict in {len(conflicts)} file(s):")
        for f in conflicts:
            console.detail(f)
        console.empty()
        if not abort_on_conflict:
            console.hint("Resolve conflicts, then run:")
            console.out.print(f"      [prompt.dimmed]$ wade {session_type}-session sync[/]")

    if not abort_on_conflict:
        try:
            diff_output = git_repo.diff_stat(repo_root)
            if diff_output.strip():
                emit(SyncEventType.CONFLICT_DIFF, diff=diff_output)
        except GitError:
            logger.debug("sync.conflict_diff_read_failed", exc_info=True)

    return SyncResult(
        success=False,
        current_branch=current,
        main_branch=resolved_main,
        conflicts=conflicts,
    )


def sync(
    dry_run: bool = False,
    main_branch: str | None = None,
    json_output: bool = False,
    project_root: Path | None = None,
    session_type: str = "implementation",
    no_stash: bool = False,
) -> SyncResult:
    """Sync current branch with main.

    Flow:
    1. Pre-flight checks (in git repo, not on main)
    2. Handle dirty worktree: auto-stash user changes or fail with --no-stash
    3. Fetch origin, count commits behind, merge
    4. Restore stash (or emit STASH_LEFT_BEHIND on pop conflict)
    5. Emit structured events
    """
    config = load_config(project_root)
    cwd = project_root or Path.cwd()
    events: list[SyncEvent] = []

    def emit(event: SyncEventType, **data: str | int | list[str]) -> None:
        ev = SyncEvent(event=event, data=data)
        events.append(ev)
        if json_output:
            console.raw(json.dumps({"event": event, **data}) + "\n")

    preflight = _sync_preflight(cwd, main_branch, config, emit)
    if isinstance(preflight, SyncResult):
        return SyncResult(
            success=preflight.success,
            current_branch=preflight.current_branch,
            main_branch=preflight.main_branch,
            events=events,
        )
    repo_root = preflight.repo_root
    current = preflight.current_branch
    resolved_main = preflight.resolved_main

    stash_ref: str | None = None
    already_fetched = False

    if preflight.dirty_category == _DirtyCategory.USER_DIRTY:
        if no_stash:
            _emit_dirty_worktree_error(emit, preflight, json_output)
            return SyncResult(
                success=False, current_branch=current, main_branch=resolved_main, events=events
            )

        # Fetch to get the latest merge target for the collision probe.
        merge_ref_for_probe = resolved_main
        has_remote = False
        try:
            has_remote = git_repo.has_remote(repo_root)
            if has_remote:
                git_sync.fetch_origin(repo_root)
                merge_ref_for_probe = f"origin/{resolved_main}"
                already_fetched = True
        except GitError:
            pass

        collisions = git_stash.detect_untracked_collisions(cwd, merge_ref_for_probe)
        if collisions:
            emit(SyncEventType.UNTRACKED_CONFLICT, paths="\n".join(collisions))
            if not json_output:
                console.error("Untracked files would be overwritten by the merge:")
                for p in collisions:
                    console.detail(p)
                console.hint("Commit, move, or delete these files before syncing.")
            return SyncResult(
                success=False, current_branch=current, main_branch=resolved_main, events=events
            )

        if preflight.has_tracked_changes:
            try:
                stash_ref, stash_name = git_stash.create_named_stash(session_type, current, cwd)
                emit(SyncEventType.AUTOSTASHED, stash_ref=stash_ref, stash_name=stash_name)
                if not json_output:
                    console.info(f"Stashed local changes: {stash_ref}")
            except GitError as exc:
                emit(SyncEventType.ERROR, reason="stash_failed", details=str(exc))
                if not json_output:
                    console.error(f"Could not stash local changes: {exc}")
                return SyncResult(
                    success=False, current_branch=current, main_branch=resolved_main, events=events
                )
    elif preflight.dirty_category == _DirtyCategory.ARTIFACTS_ONLY and no_stash:
        _emit_dirty_worktree_error(emit, preflight, json_output)
        return SyncResult(
            success=False, current_branch=current, main_branch=resolved_main, events=events
        )

    # Non-blocking: warn if wade-only session files are tracked in git
    tracked_managed = _check_tracked_managed_files(cwd)
    if tracked_managed:
        if json_output:
            console.raw(
                json.dumps(
                    {"event": "tracked_session_files_warning", "tracked_files": tracked_managed}
                )
                + "\n"
            )
        else:
            console.warn(
                "Wade-managed files are tracked in git \N{EM DASH} these should not be committed"
            )
            for path in tracked_managed:
                console.detail(path)
            console.hint("Untrack with: git rm --cached <file>")

    emit(SyncEventType.PREFLIGHT_OK, current_branch=current, main_branch=resolved_main)
    if not json_output:
        console.step(f"Syncing {current} with {resolved_main}")

    # When we stashed, abort on conflict so the worktree stays clean.
    result = _merge_base(
        repo_root,
        current,
        resolved_main,
        emit,
        dry_run=dry_run,
        json_output=json_output,
        abort_on_conflict=stash_ref is not None,
        session_type=session_type,
        already_fetched=already_fetched,
    )

    if stash_ref is not None:
        if result.success:
            stash_ok = _handle_stash_restoration(stash_ref, cwd, True, emit, json_output)
            if not stash_ok:
                return SyncResult(
                    success=False,
                    current_branch=result.current_branch,
                    main_branch=result.main_branch,
                    events=events,
                )
        else:
            # Merge was aborted (abort_on_conflict=True) — restore stash.
            _handle_stash_restoration(stash_ref, cwd, False, emit, json_output)

    return SyncResult(
        success=result.success,
        current_branch=result.current_branch,
        main_branch=result.main_branch,
        conflicts=result.conflicts,
        commits_merged=result.commits_merged,
        events=events,
    )


def catchup(
    dry_run: bool = False,
    main_branch: str | None = None,
    json_output: bool = False,
    project_root: Path | None = None,
    no_stash: bool = False,
) -> SyncResult:
    """Sync the worktree branch with its base branch at session startup.

    Similar to sync() but aborts the merge on conflict so the worktree
    stays clean for the AI. Called automatically from start() before AI
    launch, and also available as a CLI command for manual retries.

    Does not push after merge — that is done()'s job.

    Returns:
        SyncResult with up_to_date/merged/conflict/error status.
    """
    config = load_config(project_root)
    cwd = project_root or Path.cwd()
    events: list[SyncEvent] = []

    def emit(event: SyncEventType, **data: str | int | list[str]) -> None:
        ev = SyncEvent(event=event, data=data)
        events.append(ev)
        if json_output:
            console.raw(json.dumps({"event": event, **data}) + "\n")

    preflight = _sync_preflight(cwd, main_branch, config, emit)
    if isinstance(preflight, SyncResult):
        return SyncResult(
            success=preflight.success,
            current_branch=preflight.current_branch,
            main_branch=preflight.main_branch,
            events=events,
        )
    repo_root = preflight.repo_root
    current = preflight.current_branch
    resolved_main = preflight.resolved_main

    stash_ref: str | None = None
    already_fetched = False

    if preflight.dirty_category == _DirtyCategory.USER_DIRTY:
        if no_stash:
            _emit_dirty_worktree_error(emit, preflight, json_output)
            return SyncResult(
                success=False, current_branch=current, main_branch=resolved_main, events=events
            )

        # Fetch to get the latest merge target for the collision probe.
        merge_ref_for_probe = resolved_main
        try:
            if git_repo.has_remote(repo_root):
                git_sync.fetch_origin(repo_root)
                merge_ref_for_probe = f"origin/{resolved_main}"
                already_fetched = True
        except GitError:
            pass

        collisions = git_stash.detect_untracked_collisions(cwd, merge_ref_for_probe)
        if collisions:
            emit(SyncEventType.UNTRACKED_CONFLICT, paths="\n".join(collisions))
            if not json_output:
                console.error("Untracked files would be overwritten by the merge:")
                for p in collisions:
                    console.detail(p)
                console.hint("Commit, move, or delete these files before syncing.")
            return SyncResult(
                success=False, current_branch=current, main_branch=resolved_main, events=events
            )

        if preflight.has_tracked_changes:
            try:
                stash_ref, stash_name = git_stash.create_named_stash("catchup", current, cwd)
                emit(SyncEventType.AUTOSTASHED, stash_ref=stash_ref, stash_name=stash_name)
                if not json_output:
                    console.info(f"Stashed local changes: {stash_ref}")
            except GitError as exc:
                emit(SyncEventType.ERROR, reason="stash_failed", details=str(exc))
                if not json_output:
                    console.error(f"Could not stash local changes: {exc}")
                return SyncResult(
                    success=False, current_branch=current, main_branch=resolved_main, events=events
                )
    elif preflight.dirty_category == _DirtyCategory.ARTIFACTS_ONLY and no_stash:
        _emit_dirty_worktree_error(emit, preflight, json_output)
        return SyncResult(
            success=False, current_branch=current, main_branch=resolved_main, events=events
        )

    emit(SyncEventType.PREFLIGHT_OK, current_branch=current, main_branch=resolved_main)
    if not json_output:
        console.step(f"Catching up {current} with {resolved_main}")

    result = _merge_base(
        repo_root,
        current,
        resolved_main,
        emit,
        dry_run=dry_run,
        json_output=json_output,
        abort_on_conflict=True,
        already_fetched=already_fetched,
    )

    stash_restored = True
    if stash_ref is not None:
        # Merge aborted on conflict or succeeded — restore stash either way.
        stash_restored = _handle_stash_restoration(
            stash_ref, cwd, result.success, emit, json_output
        )

    return SyncResult(
        success=result.success and stash_restored,
        current_branch=result.current_branch,
        main_branch=result.main_branch,
        conflicts=result.conflicts,
        commits_merged=result.commits_merged,
        events=events,
    )
