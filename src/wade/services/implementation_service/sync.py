"""Implementation sync / catchup — merge base branch into the worktree branch.

Also hosts worktree staleness classification, shared by the cleanup module.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
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
from wade.models.config import ProjectConfig
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
from wade.utils import stale_base

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
    stash_sha: str,
    cwd: Path,
    result_success: bool,
    emit: Any,
    json_output: bool,
) -> bool:
    """Restore the stash by its commit SHA and emit events.

    Applies by content-addressed SHA — never a positional ref held from
    creation, which a concurrent worktree's ``stash push`` could have shifted
    onto someone else's work (A1) — then drops the entry only after a clean
    apply. On an apply conflict the stash is left in place and
    STASH_LEFT_BEHIND is emitted. A clean apply whose drop fails still counts
    as a restore — the changes are back — but the leftover entry is reported so
    the user can remove it. Callers should treat a False return as a sync
    failure.
    """
    apply_result = git_stash.apply_stash_by_sha(stash_sha, cwd)
    if apply_result.returncode == 0:
        if not git_stash.drop_stash_by_sha(stash_sha, cwd) and not json_output:
            console.warn(f"Restored the changes, but the stash entry {stash_sha} remains.")
            console.hint(f"Remove it with: git stash drop {stash_sha}")
        emit(SyncEventType.STASH_RESTORED, stash_ref=stash_sha)
        if not json_output:
            console.success("Restored stashed changes.")
        return True

    recovery_cmd = f"git stash apply {stash_sha}"
    emit(
        SyncEventType.STASH_LEFT_BEHIND,
        stash_ref=stash_sha,
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


def _resolve_merge_ref(
    repo_root: Path,
    resolved_main: str,
    already_fetched: bool,
    json_output: bool,
) -> str:
    """Fetch origin (unless ``already_fetched``) and return the ref to merge from.

    Falls back to the local ``resolved_main`` branch when there is no remote, or the
    fetch fails — the caller must NOT assume the returned ref is ``origin/<main>``.
    """
    try:
        has_remote = git_repo.has_remote(repo_root)
    except GitError:
        has_remote = False

    if not has_remote:
        return resolved_main

    if already_fetched:
        return f"origin/{resolved_main}"

    try:
        git_sync.fetch_origin(repo_root)
        if not json_output:
            console.detail(f"Fetched latest from origin/{resolved_main}")
        return f"origin/{resolved_main}"
    except GitError:
        if not json_output:
            console.warn("Fetch failed; using local main")
        return resolved_main


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
    merge_ref: str | None = None,
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
            ``origin/<main>`` ref is fresh enough for the merge. Ignored when
            ``merge_ref`` is given.
        merge_ref: Pre-resolved ref to merge from, bypassing the internal
            fetch/fallback resolution entirely. Callers that invoke
            ``_merge_base`` more than once for the same logical attempt (e.g.
            the catchup skip-worktree retry in ``_catchup_merge``) must resolve
            the ref once via ``_resolve_merge_ref`` and pass it here for every
            call — re-resolving per call could silently swap a fetch-failure
            fallback (local ``resolved_main``) for a stale cached
            ``origin/<main>`` on retry, and vice versa.

    Returns:
        SyncResult with success/conflicts/commits_merged (events=[]).
    """
    if merge_ref is None:
        merge_ref = _resolve_merge_ref(repo_root, resolved_main, already_fetched, json_output)

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
        # Caught up → any prior stale-base warning no longer applies (#407). ``repo_root``
        # is the worktree root, where ``.wade/`` lives.
        stale_base.clear_stale_base(repo_root)
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
        # Merge succeeded → branch advanced onto base; clear any stale-base warning (#407).
        stale_base.clear_stale_base(repo_root)
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

    stash_sha: str | None = None
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
                stash_sha, stash_name = git_stash.create_named_stash(session_type, current, cwd)
                emit(SyncEventType.AUTOSTASHED, stash_ref=stash_sha, stash_name=stash_name)
                if not json_output:
                    console.info(f"Stashed local changes: {stash_sha}")
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
        abort_on_conflict=stash_sha is not None,
        session_type=session_type,
        already_fetched=already_fetched,
    )

    if stash_sha is not None:
        if result.success:
            stash_ok = _handle_stash_restoration(stash_sha, cwd, True, emit, json_output)
            if not stash_ok:
                return SyncResult(
                    success=False,
                    current_branch=result.current_branch,
                    main_branch=result.main_branch,
                    events=events,
                )
        else:
            # Merge was aborted (abort_on_conflict=True) — restore stash.
            _handle_stash_restoration(stash_sha, cwd, False, emit, json_output)

    return SyncResult(
        success=result.success,
        current_branch=result.current_branch,
        main_branch=result.main_branch,
        conflicts=result.conflicts,
        commits_merged=result.commits_merged,
        events=events,
    )


# ---------------------------------------------------------------------------
# Catchup-only migration reconcile (#407)
# ---------------------------------------------------------------------------
#
# These helpers run ONLY on the catchup() path — session startup, before the AI
# launches — where an untracked KNOWLEDGE.md / .gitattributes holds nothing but
# bootstrap/carry-forward content that is safe to discard. They must NEVER be wired
# into sync() (mid/end-session): by then the agent may have ``knowledge add``ed real,
# still-uncommitted entries into that untracked KNOWLEDGE.md, and a blind
# remove-and-remerge would destroy them. The delete/strip semantics below are
# deliberately not extracted into a shared helper sync() also calls (#407 Part B).

# Tracked, wade-managed files bootstrap marks ``--skip-worktree`` (so their injected
# blocks never read as dirty). When ``main`` also changed one, git refuses to merge over
# the hidden local change with a *non-conflict* GitError — the collision this reconciles.
# CLAUDE.md is included because ``_do_suppress_pointer_artifacts`` marks it skip-worktree
# when it is the pointer target (a project with a tracked CLAUDE.md and no AGENTS.md).
_POINTER_FILES = ("AGENTS.md", "CLAUDE.md")
_MANAGED_SKIP_WORKTREE_FILES = (*_POINTER_FILES, ".gitignore")


def _wade_owned_untracked_paths(cwd: Path, config: ProjectConfig) -> set[str]:
    """Repo-relative untracked paths catchup may safely discard so the merge can bring in
    ``main``'s now-tracked versions: ``.gitattributes`` plus the knowledge file and its
    ``.ratings.jsonl`` sibling (resolved exactly as knowledge resolution does)."""
    owned = {".gitattributes"}
    from wade.utils.knowledge_file import resolve_knowledge_path, resolve_ratings_path

    try:
        kpath = resolve_knowledge_path(cwd, config.knowledge)
        root = cwd.resolve()
        owned.add(kpath.relative_to(root).as_posix())
        owned.add(resolve_ratings_path(kpath).relative_to(root).as_posix())
    except (ValueError, OSError):
        pass
    return owned


def _strip_wade_gitattributes_block(content: str) -> str:
    """Remove wade's managed knowledge ``merge=union`` block, leaving only whatever
    unrelated, user-authored rules the local ``.gitattributes`` also carries."""
    from wade.skills.installer import (
        KNOWLEDGE_ATTRIBUTES_MARKER_END,
        KNOWLEDGE_ATTRIBUTES_MARKER_START,
    )
    from wade.utils.markdown import remove_marker_block

    return remove_marker_block(
        content, KNOWLEDGE_ATTRIBUTES_MARKER_START, KNOWLEDGE_ATTRIBUTES_MARKER_END
    )


def _is_discardable_untracked(cwd: Path, rel: str, merge_ref: str) -> bool:
    """True when deleting the untracked local ``rel`` loses no data.

    Startup catchup's "safe to discard" assumption holds for a freshly bootstrapped
    worktree, but ``catchup()`` also runs on **resumed** sessions (``start()`` calls it on
    every ``wade implement``, including the reused-worktree path) — where the agent may have
    ``knowledge add``ed real, still-uncommitted entries into an untracked ``KNOWLEDGE.md`` /
    ``.ratings.jsonl``. Deleting those would destroy agent-authored knowledge, the exact
    class this issue guards against. So a data file is discardable only when it adds nothing
    over ``main``'s incoming version (empty, or every non-blank line already present there).
    ``.gitattributes`` gets the same treatment with wade's managed ``merge=union`` block
    (regenerated post-merge by ``ensure_knowledge_merge_attributes``) stripped first — a
    resumed worktree can carry unrelated, user-authored rules alongside that block, and the
    incoming base need not contain them, so it is NOT unconditionally discardable (#407
    review).

    Lines are compared as **multisets** (not sets): an append-only ``.ratings.jsonl`` may
    carry the *same* serialized vote record twice while the incoming base has it once —
    deleting the local copy would silently drop the duplicate. Requiring at-least-equal
    multiplicity keeps that from being treated as discardable (#408 review).
    """
    local_path = cwd / rel
    try:
        local = local_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return True  # nothing on disk to lose
    except (OSError, UnicodeDecodeError):
        return False  # unreadable → do not risk deleting it
    if rel == ".gitattributes":
        local = _strip_wade_gitattributes_block(local)
    if not local.strip():
        return True  # empty (or wade-managed content only) → nothing to lose
    incoming = git_repo.show_file_at_ref(cwd, merge_ref, rel)
    if incoming is None:
        return False  # no incoming version to fall back on → do not delete
    local_lines = Counter(ln for ln in local.splitlines() if ln.strip())
    incoming_lines = Counter(ln for ln in incoming.splitlines() if ln.strip())
    return local_lines <= incoming_lines


def _reconcile_untracked_migration_files(cwd: Path, collisions: list[str]) -> None:
    """Delete the untracked local copies so the merge brings in main's tracked versions.

    Catchup-only; the caller verifies each path is wade-owned AND discardable first
    (:func:`_is_discardable_untracked`) so real agent-authored knowledge is never deleted.
    """
    for rel in collisions:
        target = cwd / rel
        try:
            if target.is_symlink() or target.is_file():
                target.unlink()
                logger.debug("catchup.reconcile_removed_untracked", path=rel)
        except OSError:
            logger.debug("catchup.reconcile_unlink_failed", path=rel, exc_info=True)


def _backup_untracked_files(cwd: Path, paths: list[str]) -> dict[str, bytes] | None:
    """Read *paths* before :func:`_reconcile_untracked_migration_files` deletes them, so
    they can be put back if the merge that was meant to replace them never completes.

    Returns ``None`` when *any* path cannot be read — the caller must then skip the
    reconcile entirely rather than delete a file it could not back up, or the exact
    silent-data-loss the backup guards against reappears on the failure path (#408 review).
    """
    backup: dict[str, bytes] = {}
    for rel in paths:
        try:
            backup[rel] = (cwd / rel).read_bytes()
        except OSError:
            logger.debug("catchup.reconcile_backup_failed", path=rel, exc_info=True)
            return None
    return backup


def _restore_untracked_files(cwd: Path, backup: dict[str, bytes]) -> None:
    """Write back files removed for a reconcile whose merge did not complete (conflict,
    abort, or a failure raised before the merge even ran) — the same class of data loss
    the reconcile itself guards against must not reappear on the failure path (#407 review).
    """
    for rel, data in backup.items():
        target = cwd / rel
        if target.exists():
            continue  # merge already produced a (tracked) version — do not clobber it
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            logger.debug("catchup.reconcile_restored_untracked", path=rel)
        except OSError:
            logger.debug("catchup.reconcile_restore_failed", path=rel, exc_info=True)


def _files_blocking_merge(error_text: str) -> list[str]:
    """Parse the file paths from git's 'would be overwritten by merge' abort message."""
    files: list[str] = []
    capturing = False
    for line in error_text.splitlines():
        if "would be overwritten by merge" in line:
            capturing = True
            continue
        if not capturing:
            continue
        if line.startswith(("\t", "    ")):
            stripped = line.strip()
            if stripped:
                files.append(stripped)
        elif line.strip():
            break
    return files


def _pointer_diff_is_only_block(cwd: Path, filename: str) -> bool:
    """True when the ONLY difference between the working ``filename`` (an ``AGENTS.md`` /
    ``CLAUDE.md`` pointer target) and its committed (HEAD) version is the wade-managed
    ``wade:pointer`` block.

    This is the safety guard ``recovery.md`` prescribes before stripping the block — a real
    pointer-file edit mixed in must NOT be discarded (return False → defer to the loud
    Part A path). A skip-worktree'd file shows no ``git diff``, so compare the working copy
    (pointer block removed) against HEAD directly.
    """
    from wade.skills import pointer
    from wade.utils.markdown import remove_marker_block

    head = git_repo.show_file_at_head(cwd, filename)
    if head is None:
        return False
    try:
        working = (cwd / filename).read_text(encoding="utf-8")
    except OSError:
        return False
    stripped = remove_marker_block(working, pointer.MARKER_START, pointer.MARKER_END)
    return stripped.strip() == head.strip()


def _reconcile_skip_worktree_collision(cwd: Path, blocking: list[str]) -> Callable[[], None] | None:
    """Clear wade-managed ``--skip-worktree`` blocks that block the merge; return a
    ``restore()`` thunk to re-inject + re-skip them afterward.

    Returns ``None`` (do NOT reconcile → defer to Part A's loud path) when a *non-managed*
    file also blocks the merge, or when a pointer file (``AGENTS.md`` / ``CLAUDE.md``)
    carries a real edit beyond its pointer block. Guards are checked before any mutation so
    a refusal leaves the tree untouched.
    """
    managed = [f for f in blocking if f in _MANAGED_SKIP_WORKTREE_FILES]
    if not managed or set(blocking) - set(managed):
        return None
    pointer_files = [f for f in managed if f in _POINTER_FILES]
    # Guard first (no mutation yet): never strip a pointer file with a real edit mixed in.
    for pf in pointer_files:
        if not _pointer_diff_is_only_block(cwd, pf):
            logger.debug("catchup.reconcile_pointer_real_edit_abort", file=pf)
            return None

    from wade.services.implementation_service.bootstrap import (
        strip_worktree_gitignore,
        write_worktree_gitignore,
    )
    from wade.skills import pointer

    reconcile_gitignore = ".gitignore" in managed
    for f in managed:
        if f == ".gitignore":
            git_repo.unskip_worktree_file(cwd, ".gitignore")
            strip_worktree_gitignore(cwd)
        else:  # pointer file
            git_repo.unskip_worktree_file(cwd, f)
            pointer.remove_pointer(cwd / f)

    def restore() -> None:
        # Re-inject the managed blocks after the merge and re-apply --skip-worktree so
        # they stay hidden for the rest of the session (mirrors the recovery.md flow).
        if reconcile_gitignore:
            write_worktree_gitignore(cwd)
            if git_repo.is_file_tracked(cwd, ".gitignore"):
                git_repo.skip_worktree_file(cwd, ".gitignore")
        if pointer_files:
            # Re-skip the file ensure_pointer ACTUALLY wrote to — not the original
            # pointer_files. The merge may have introduced a higher-priority target (e.g.
            # the base adds AGENTS.md while the worktree used CLAUDE.md), so ensure_pointer
            # rewrites the block into that new target; skipping the old one instead would
            # leave the freshly written block visible as a dirty change (#408 review).
            written = pointer.ensure_pointer(cwd)  # rewrites the appropriate pointer target
            if written is not None:
                rel = Path(written).name  # AGENTS.md / CLAUDE.md live at the repo root
                if git_repo.is_file_tracked(cwd, rel):
                    git_repo.skip_worktree_file(cwd, rel)

    return restore


def _catchup_merge(
    repo_root: Path,
    cwd: Path,
    current: str,
    resolved_main: str,
    emit: Any,
    *,
    dry_run: bool,
    json_output: bool,
    already_fetched: bool,
) -> SyncResult:
    """Run the catchup merge, transparently reconciling a wade-managed ``--skip-worktree``
    collision (``.gitignore`` / ``AGENTS.md`` / ``CLAUDE.md`` pointer block) that would
    otherwise abort the merge with a *non-conflict* ``GitError``.

    Catchup-only. On the first such abort the blocks are cleared, the merge is retried
    once, then the blocks are re-injected. A collision touching any unmanaged file, or a
    pointer file carrying a real edit, re-raises so Part A surfaces it loudly.

    The merge ref is resolved exactly ONCE — via ``_resolve_merge_ref`` — and reused for
    both the initial attempt and the retry. Re-resolving per attempt would let a failed
    fetch on the first attempt (which falls back to the local ``resolved_main``) get
    silently replaced by a stale cached ``origin/<main>`` on retry, recreating the #407
    silent stale-session failure (#408 review).
    """
    merge_ref = _resolve_merge_ref(repo_root, resolved_main, already_fetched, json_output)
    try:
        return _merge_base(
            repo_root,
            current,
            resolved_main,
            emit,
            dry_run=dry_run,
            json_output=json_output,
            abort_on_conflict=True,
            merge_ref=merge_ref,
        )
    except GitError as exc:
        blocking = _files_blocking_merge(str(exc))
        if not blocking:
            raise
        restore = _reconcile_skip_worktree_collision(cwd, blocking)
        if restore is None:
            raise
        if not json_output:
            # Present-continuous: the retry below may still fail/raise — do not claim done.
            console.detail(
                "Clearing wade-managed skip-worktree blocks and retrying merge: "
                + ", ".join(blocking)
            )
        try:
            return _merge_base(
                repo_root,
                current,
                resolved_main,
                emit,
                dry_run=dry_run,
                json_output=json_output,
                abort_on_conflict=True,
                merge_ref=merge_ref,
            )
        finally:
            restore()


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

    stash_sha: str | None = None
    already_fetched = False
    knowledge_reconciled = False
    reconcile_targets: list[str] = []

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
            # Auto-reconcile ONLY when EVERY colliding path is wade-owned AND discardable —
            # remove the untracked copies so the merge brings in main's now-tracked versions
            # across the #386 knowledge-store migration. "Discardable" (per
            # _is_discardable_untracked) guards the RESUME case: catchup runs on every
            # `wade implement`, so an untracked KNOWLEDGE.md here may hold real, uncommitted
            # agent entries (not just fresh bootstrap content) — those are never deleted.
            # Any other untracked path, or one carrying unique local data, still aborts and
            # is surfaced loudly by Part A. This delete is deliberately catchup-only (sync()
            # keeps aborting so it never destroys agent-authored knowledge — #407). The actual
            # delete is deferred to just before the merge attempt below — a --dry-run must
            # not mutate the worktree, and a failure between here and the merge (stash, or
            # the merge itself) must not strand the files gone with nothing to show for it
            # (#407 review).
            if set(collisions) <= _wade_owned_untracked_paths(cwd, config) and all(
                _is_discardable_untracked(cwd, c, merge_ref_for_probe) for c in collisions
            ):
                reconcile_targets = collisions
            else:
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
                stash_sha, stash_name = git_stash.create_named_stash("catchup", current, cwd)
                emit(SyncEventType.AUTOSTASHED, stash_ref=stash_sha, stash_name=stash_name)
                if not json_output:
                    console.info(f"Stashed local changes: {stash_sha}")
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

    result: SyncResult | None = None
    stash_restored = True
    untracked_backup: dict[str, bytes] = {}
    try:
        if reconcile_targets:
            if dry_run:
                if not json_output:
                    console.detail(
                        "Dry run: would reconcile migration-owned untracked files: "
                        + ", ".join(reconcile_targets)
                    )
            else:
                # Backed up, then deleted right before the merge that is meant to replace
                # them — never at collision-detection time. If the merge below does not
                # complete (conflict, abort, or a raise from an unrelated blocking file),
                # the ``finally`` below restores them rather than leaving them silently
                # gone (#407 review). If ANY target can't be backed up, skip the reconcile
                # entirely — deleting a file we couldn't back up would strand it with no
                # merge to show for it; the merge then aborts and Part A surfaces the stale
                # base loudly instead (#408 review).
                backed_up = _backup_untracked_files(cwd, reconcile_targets)
                if backed_up is None:
                    logger.debug("catchup.reconcile_skipped_backup_failed")
                else:
                    untracked_backup = backed_up
                    _reconcile_untracked_migration_files(cwd, reconcile_targets)
                    knowledge_reconciled = True
                    if not json_output:
                        console.detail(
                            "Reconciled migration-owned untracked files: "
                            + ", ".join(reconcile_targets)
                        )

        result = _catchup_merge(
            repo_root,
            cwd,
            current,
            resolved_main,
            emit,
            dry_run=dry_run,
            json_output=json_output,
            already_fetched=already_fetched,
        )

        # After a reconcile-and-merge that pulled in main's now-tracked knowledge files,
        # restore the wade-managed ``merge=union`` block in .gitattributes (idempotent).
        if knowledge_reconciled and result.success and config.knowledge.enabled:
            from wade.skills.installer import ensure_knowledge_merge_attributes

            try:
                ensure_knowledge_merge_attributes(cwd, config)
            except Exception:
                logger.debug("catchup.reensure_knowledge_attrs_failed", exc_info=True)
    finally:
        # The merge that was supposed to replace the reconciled files did not complete —
        # put them back so a conflict/abort/raise never leaves them silently gone.
        if untracked_backup and not (result is not None and result.success):
            _restore_untracked_files(cwd, untracked_backup)
        # Always restore an autostash — even if _catchup_merge raised an unreconcilable
        # skip-worktree GitError — so a session's stashed tracked changes are never
        # silently orphaned (the exact silent-data-at-risk class #407 targets). On the
        # raise path this runs, then the exception propagates so Part A (core.py) still
        # surfaces the staleness loudly; the ``return`` below is skipped.
        if stash_sha is not None:
            merge_ok = result.success if result is not None else False
            stash_restored = _handle_stash_restoration(stash_sha, cwd, merge_ok, emit, json_output)

    # ``result`` is always set here: the only path past the try/finally is a normal
    # return from _catchup_merge — a raise propagates through the finally and skips this.
    assert result is not None
    return SyncResult(
        success=result.success and stash_restored,
        current_branch=result.current_branch,
        main_branch=result.main_branch,
        conflicts=result.conflicts,
        commits_merged=result.commits_merged,
        events=events,
    )
