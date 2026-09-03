"""Post-implementation lifecycle and PR-body composition.

Runs after the AI session exits: merge (PR or direct), worktree cleanup, main
pull, and issue close. Also owns the PR-body helpers shared with ``done``.
"""

from __future__ import annotations

import contextlib
import re
import shlex
import shutil
import webbrowser
from enum import StrEnum
from pathlib import Path

import structlog
from pydantic import BaseModel

from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.git.repo import GitError
from wade.models.config import ProjectConfig
from wade.models.session import MergeStatus
from wade.models.task import Task
from wade.providers.base import AbstractTaskProvider
from wade.services import bot_trigger
from wade.services.implementation_service.bootstrap import (
    _format_uncommitted_summary,
    _get_dirty_file_paths,
    _identify_session_dirty_files,
)
from wade.services.implementation_service.cleanup import _preserve_session_data
from wade.services.implementation_service.usage_tracking import IMPL_USAGE_MARKER_START
from wade.ui import prompts
from wade.ui.console import console

logger = structlog.get_logger()

__all__ = [
    "MAX_RESOLVE_ATTEMPTS",
    "ReviewStatus",
    "ReviewStatusKind",
    "SessionType",
    "_apply_pr_refs",
    "_build_pr_body",
    "_merge_pr",
    "_move_untracked_aside",
    "_parse_overwrite_paths",
    "_post_implementation_lifecycle",
    "_post_implementation_lifecycle_pr",
    "_pull_main_after_merge",
    "_render_review_status",
    "_restore_backed_up",
    "_strip_summary_section",
    "_warn_pull_sync_failed",
]


def _post_implementation_lifecycle(
    repo_root: Path,
    branch: str,
    issue_number: str | int | None,
    worktree_path: Path | None,
    provider: AbstractTaskProvider,
    *,
    ai_tool: str | None = None,
    model: str | None = None,
    detach: bool = False,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    permission_mode: str | None = None,
    permission_mode_explicit: bool = False,
    sandbox: bool | None = None,
) -> MergeStatus:
    """Run post-implementation lifecycle and return the merge status.

    PR is the only supported merge strategy (the ``direct`` strategy was
    retired in #357), so this delegates straight to the PR lifecycle.

    ``sandbox`` carries the caller's explicit ``--sandbox`` / ``--no-sandbox``
    (``None`` = unset) into a "wait for reviews → comments found" re-launch so an
    explicit profile survives instead of the follow-on review session silently
    re-resolving it from ``ai.review_pr_comments``/global config.
    """
    return _post_implementation_lifecycle_pr(
        repo_root,
        branch,
        issue_number,
        worktree_path,
        provider,
        ai_tool=ai_tool,
        model=model,
        detach=detach,
        ai_explicit=ai_explicit,
        model_explicit=model_explicit,
        permission_mode=permission_mode,
        permission_mode_explicit=permission_mode_explicit,
        sandbox=sandbox,
    )


_UNTRACKED_COLLISION_MARKER = "untracked working tree files would be overwritten by merge"
_LOCAL_CHANGES_MARKER = "Your local changes to the following files would be overwritten"


def _parse_overwrite_paths(stderr: str) -> list[str]:
    """Extract conflicting file paths from the untracked-collision error block.

    Anchors on :data:`_UNTRACKED_COLLISION_MARKER` specifically, not the generic
    "would be overwritten by merge" substring both this and
    :data:`_LOCAL_CHANGES_MARKER` share. When git reports both failure classes
    in one stderr (local-changes block first, untracked block second),
    matching the generic substring would start parsing at the local-changes
    block, so the caller would move tracked, locally-modified files aside as
    if they were untracked collisions instead of stashing them.
    """
    paths: list[str] = []
    in_block = False
    for line in stderr.splitlines():
        if _UNTRACKED_COLLISION_MARKER in line:
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if not stripped or stripped.startswith("Please"):
                break
            paths.append(stripped)
    return paths


def _warn_pull_sync_failed() -> None:
    console.warn("Could not sync local main branch after merge.")
    console.hint("Run 'git pull' manually to update your local branch.")


# Each loop iteration in ``_pull_main_after_merge`` resolves exactly ONE failure
# class (move-aside untracked collisions OR stash tracked local changes) and then
# retries the pull once. The worst *supported* real scenario is 2 iterations —
# untracked-collision -> move -> retry-fail -> local-changes -> stash ->
# retry-succeed — so the cap is 3 (one spare). The spare exists so a future edit
# that adds a third resolvable class (or reorders these two) cannot silently
# starve the combined path of a retry. If you raise or lower this, keep
# ``test_untracked_then_local_changes_combined`` green — it exercises the
# 2-iteration path this cap must always leave room for.
MAX_RESOLVE_ATTEMPTS = 3


def _move_untracked_aside(main_root: Path, rel_paths: list[str]) -> list[tuple[Path, Path]]:
    """Move still-present untracked collision files into ``.wade/pull-backups``.

    Returns ``(original, backup)`` pairs for every file actually moved, so the
    caller can either keep them (finalize on a successful retry) or hand them to
    :func:`_restore_backed_up` (roll back on terminal failure). Paths git named
    but already gone are skipped, so an empty return means "no progress possible"
    — the caller uses that to break out of the resolve loop rather than retry a
    pull that would fail identically. Preserves the historical
    ``mkdir(parents=True)`` create + suppressed-``rmdir`` parent cleanup.
    """
    backup_root = main_root / ".wade" / "pull-backups"
    moved: list[tuple[Path, Path]] = []
    for rel_path in rel_paths:
        original = main_root / rel_path
        if not original.exists():
            continue
        backup = backup_root / rel_path
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(original), str(backup))
        except OSError:
            logger.warning("pull.backup_untracked_failed", path=str(original), exc_info=True)
        else:
            moved.append((original, backup))
            # Only tidy up the now-empty parent after a *successful* move — on a
            # failed move ``original`` is still there, so its parent isn't empty.
            with contextlib.suppress(OSError):
                original.parent.rmdir()
    return moved


def _restore_backed_up(pairs: list[tuple[Path, Path]]) -> None:
    """Move each backed-up file back to its original path, in reverse order.

    Recreates the original parent dir first: :func:`_move_untracked_aside`
    ``rmdir``'s a parent it emptied (e.g. ``.claude/`` for
    ``.claude/settings.json``), so without the ``mkdir`` the restore of a nested
    path would raise ``FileNotFoundError`` on the *common* case, not just the
    truly-unrecoverable one. On ``OSError`` for a single pair, log it, print the
    exact ``mv`` recovery command, and keep going — one bad move must never
    strand the remaining files.
    """
    for original, backup in reversed(pairs):
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup), str(original))
        except OSError:
            logger.warning(
                "pull.restore_backup_failed",
                backup=str(backup),
                original=str(original),
                exc_info=True,
            )
            console.warn(f"Could not restore backed-up file to {original}.")
            console.hint(
                f"Restore it manually: mv {shlex.quote(str(backup))} {shlex.quote(str(original))}"
            )


def _cleanup_empty_backup_dirs(main_root: Path) -> None:
    """Best-effort removal of now-empty dirs under ``.wade/pull-backups``.

    Called only after a rollback moved files back out. ``rmdir`` refuses
    non-empty dirs, so backups accumulated by *earlier successful* syncs (kept on
    purpose — see :func:`_pull_main_after_merge`) are never touched.
    """
    backup_root = main_root / ".wade" / "pull-backups"
    if not backup_root.is_dir():
        return
    # Deepest-first so a child dir is cleared before its parent is tried.
    for path in sorted(backup_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            with contextlib.suppress(OSError):
                path.rmdir()
    with contextlib.suppress(OSError):
        backup_root.rmdir()


def _pop_stash_or_warn(main_root: Path) -> None:
    """Pop the stash taken during the resolve loop, warning on a pop conflict."""
    pop_result = git_repo.stash_pop(main_root)
    if pop_result.returncode != 0:
        console.warn("Could not restore stashed local changes.")
        console.hint("Resolve conflicts, then inspect `git stash list`.")


def _pull_main_after_merge(repo_root: Path) -> None:
    """Fast-forward the local main branch after a successful PR merge — atomically.

    **Contract:** if main cannot be fast-forwarded, the working tree is left
    *exactly as it was on entry* (a true no-op) and a warning is printed. On
    success, main is advanced and any set-aside untracked files are preserved in
    ``.wade/pull-backups`` with a notice (unchanged happy-path behavior). This
    atomicity covers the function's own **logical** failure paths (aborted pulls,
    a failed stash, restorable moves); it is *not* crash-safe against the process
    being killed mid-sequence — there is no journal/resume, so a hard kill
    between two ``shutil.move`` calls could still strand files.

    Two failure classes are resolved by a small bounded loop
    (:data:`MAX_RESOLVE_ATTEMPTS`), and they compose because they act on disjoint
    path sets:

    - **Untracked collisions** (wade-managed files installed by ``wade init`` as
      untracked, then introduced as *tracked* by the merged PR) → each colliding,
      still-present file is moved aside into ``.wade/pull-backups`` and the pull
      retried. We deliberately do **not** ``git stash --include-untracked`` here:
      after a successful pull brings the file back *tracked*, ``git stash pop``
      would collide ("already exists, no checkout") on the common, expected wade
      happy path (a previously-untracked managed file becoming tracked), turning
      today's clean success into a noisy stash-conflict on essentially every
      merge that introduces a managed file. Move-aside keeps the happy path
      clean; the rollback below is what makes it atomic. Do not "fix" this back.
    - **Local changes** to tracked files (e.g. ``wade init`` touching
      ``.gitignore``) → ``git stash`` (at most once), retry the pull, pop later.

    On any terminal failure (cap reached, unknown error, or no movable file left
    to make progress), the moved files are restored to their original paths and
    any stash is popped **before** warning, so the tree returns to entry state.

    Pulling main only makes sense in the *main checkout*: a ``git pull`` run from
    a linked worktree (on a feature branch) would target the wrong branch, so we
    resolve the main checkout root first.
    """
    main_root = git_repo.main_checkout_root(repo_root)

    result = git_repo.pull_ff_only(main_root)
    if result.returncode == 0:
        return

    moved: list[tuple[Path, Path]] = []
    stashed = False

    for _ in range(MAX_RESOLVE_ATTEMPTS):
        stderr = result.stderr
        if _UNTRACKED_COLLISION_MARKER in stderr:
            # NEVER delete colliding files — git reports every untracked collision
            # here, not just wade-managed ones, so unlinking could destroy user
            # data. Move each aside so the tracked versions can land on retry.
            newly_moved = _move_untracked_aside(main_root, _parse_overwrite_paths(stderr))
            if not newly_moved:
                # Nothing could be moved (all already gone) — no progress is
                # possible, so a retry would just fail identically. Bail out.
                break
            moved.extend(newly_moved)
        elif _LOCAL_CHANGES_MARKER in stderr:
            if stashed:
                # Already stashed once; a second local-changes error means the
                # stash did not clear the obstruction, so re-stashing would loop.
                break
            if git_repo.stash(main_root).returncode != 0:
                break
            stashed = True
        else:
            # Unknown error class — nothing actionable to resolve.
            break

        result = git_repo.pull_ff_only(main_root)
        if result.returncode == 0:
            # Success → finalize: keep moved files in the backup dir (with the
            # existing notice) and pop any stash we took.
            if moved:
                console.warn("Backed up untracked files that collided with the merge:")
                for _original, backup in moved:
                    console.detail(str(backup))
            if stashed:
                _pop_stash_or_warn(main_root)
            return

    # Terminal failure → roll back to the entry state (a failed sync is a no-op),
    # then warn. Restore moved files first, pop any stash, and best-effort clear
    # the now-empty backup dirs so nothing is stranded in .wade/pull-backups.
    _restore_backed_up(moved)
    if stashed:
        _pop_stash_or_warn(main_root)
    if moved:
        _cleanup_empty_backup_dirs(main_root)
    _warn_pull_sync_failed()


def _post_implementation_lifecycle_pr(
    repo_root: Path,
    branch: str,
    issue_number: str | int | None,
    worktree_path: Path | None,
    provider: AbstractTaskProvider,
    *,
    ai_tool: str | None = None,
    model: str | None = None,
    detach: bool = False,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    permission_mode: str | None = None,
    permission_mode_explicit: bool = False,
    sandbox: bool | None = None,
) -> MergeStatus:
    """Run the PR-based post-implementation lifecycle."""
    lookup = git_pr.get_pr_for_branch(repo_root, branch)
    if lookup.lookup_failed:
        console.warn(f"Could not look up the PR for branch '{branch}'. Skipping lifecycle.")
        return MergeStatus.NOT_MERGED
    if lookup.is_closed_or_merged:
        # A merged/closed PR is not ours to merge — never offer "Merge PR" on it.
        console.info(f"PR #{lookup.number} is already {lookup.state.lower()}. Nothing to merge.")
        return MergeStatus.NOT_MERGED
    if not lookup.is_open or lookup.pr is None:
        console.warn(f"No open PR found for branch '{branch}'. Skipping lifecycle.")
        return MergeStatus.NOT_MERGED

    pr_number = lookup.pr.number
    pr_url = lookup.pr.url
    if pr_url and prompts.is_tty() and prompts.confirm("Open PR in browser?", default=True):
        webbrowser.open(pr_url)

    if not prompts.is_tty():
        return MergeStatus.NOT_MERGED

    # Keep the config attached to every poll, not just the trigger branch: an
    # ordinary "Wait for reviews" still needs its expected-bot gating (#448).
    # Config load failure remains non-fatal so a convenience menu never blocks
    # an otherwise valid merge/wait lifecycle.
    poll_config: ProjectConfig | None = None
    try:
        from wade.config.loader import load_config

        poll_config = load_config(repo_root)
    except Exception:
        logger.debug("bot_trigger.poll_config_load_failed", exc_info=True)

    # Offer the bot-review triggers here too (#464): with `auto_trigger` off, the
    # agent's `done` did not post them, and this menu is the human's first TTY
    # moment afterwards — waiting for a review no one asked for is the failure
    # mode this avoids. Hidden when every enabled bot already fired for this
    # commit (see `bot_trigger.pending_names`).
    bot_config, trigger_option = bot_trigger.menu_entry(
        repo_root, pr_number, worktree_path, suffix=", then wait", config=poll_config
    )
    if bot_config is not None:
        # ``menu_entry`` may have retried config loading after the best-effort
        # load above; reuse that successful result for the ordinary wait path.
        poll_config = bot_config
    options = ["Merge PR", "Wait for reviews"]
    # Bind the entry's index at append time (not `len(options) - 1` at read time)
    # so a future option appended after it can't silently steal the branch below.
    trigger_index = -1
    if trigger_option:
        trigger_index = len(options)
        options.append(trigger_option)

    choice = prompts.select(f"PR #{pr_number} — what next?", options)

    if bot_config is not None and choice == trigger_index:
        if bot_trigger.post_pending_triggers(
            bot_config, repo_root, int(pr_number), worktree_path or repo_root
        ):
            choice = 1  # ...then fall through into the wait-for-reviews flow.
            # The trigger marker resets each bot's arrival window. Keep both the
            # config and its worktree-local marker root attached to the poll so a
            # commit pushed long ago does not make this fresh trigger look expired.
            poll_config = bot_config
        else:
            # Every trigger post failed (e.g. a GitHub outage) — no bot was asked
            # to review, so don't drop into a wait for a review no one requested
            # (the exact silent-wait this option exists to avoid).
            return MergeStatus.NOT_MERGED

    if choice == 1:  # Wait for reviews
        from wade.models.review import PollOutcome
        from wade.services import review_service

        outcome = review_service.poll_for_reviews(
            provider,
            repo_root,
            int(pr_number),
            branch,
            config=poll_config,
            marker_root=worktree_path or repo_root,
        )
        if outcome == PollOutcome.COMMENTS_FOUND and issue_number:
            _ = review_service.start(
                str(issue_number),
                ai_tool=ai_tool,
                model=model,
                project_root=repo_root,
                detach=detach,
                ai_explicit=ai_explicit,
                model_explicit=model_explicit,
                permission_mode=permission_mode,
                permission_mode_explicit=permission_mode_explicit,
                sandbox=sandbox,
            )
        elif outcome in (PollOutcome.QUIET_TIMEOUT, PollOutcome.REVIEW_COMPLETE):
            review_service._quiet_next_steps_prompt(
                repo_root,
                branch,
                issue_number,
                worktree_path,
                int(pr_number),
                provider,
                ai_tool=ai_tool,
                model=model,
                detach=detach,
                ai_explicit=ai_explicit,
                model_explicit=model_explicit,
                permission_mode=permission_mode,
                permission_mode_explicit=permission_mode_explicit,
                sandbox=sandbox,
                config=poll_config,
            )
        return MergeStatus.NOT_MERGED

    # Merge flow
    return _merge_pr(repo_root, branch, int(pr_number), issue_number, worktree_path, provider)


def _merge_pr(
    repo_root: Path,
    branch: str,
    pr_number: int,
    issue_number: str | int | None,
    worktree_path: Path | None,
    provider: AbstractTaskProvider,
) -> MergeStatus:
    """Merge a PR via squash, clean up worktree, pull main, close issue."""
    # `gh pr merge --delete-branch`, branch deletion, and worktree pruning all
    # target the *main checkout*, which is a different directory from the
    # worktree we may be running inside. Resolve it explicitly so we never run
    # main-checkout bookkeeping against a linked worktree root (see C2).
    main_root = git_repo.main_checkout_root(repo_root)

    # Warn if the worktree has uncommitted changes before proceeding. Classify
    # the dirty paths into genuine user work vs. regenerable wade session
    # artifacts (the scaffold `done` re-exposes when it strips the worktree
    # gitignore block), list every file, then ask an informed confirm whose
    # wording and default match the stakes.
    if worktree_path and worktree_path.is_dir() and not git_repo.is_clean(worktree_path):
        dirty_paths = _get_dirty_file_paths(worktree_path)
        artifacts = _identify_session_dirty_files(dirty_paths, worktree_path)
        artifact_set = set(artifacts)
        genuine = [p for p in dirty_paths if p not in artifact_set]

        summary = _format_uncommitted_summary(worktree_path)
        console.warn(f"Worktree has uncommitted changes ({summary}).")
        if genuine:
            console.detail("Your uncommitted changes:")
            for path in genuine:
                console.detail(f"  {path}", markup=False)
        if artifacts:
            console.detail("wade session files (regenerable):")
            for path in artifacts:
                console.detail(f"  {path}", markup=False)

        # Default to the safe/regenerable wording (default=Yes) ONLY when we
        # positively enumerated the dirty tree and every path is regenerable
        # scaffold. If `git status` reported dirty (is_clean=False) but
        # `_get_dirty_file_paths` came back empty (a transient git failure returns
        # `[]`), we cannot vouch that it is all scaffold — fall back to the
        # conservative fail-closed prompt so an empty list never reads as "safe".
        if genuine or not dirty_paths:
            question = "Uncommitted changes will be lost. Proceed with the merge?"
            default = False
        else:
            question = (
                "Only wade session files are uncommitted (regenerable). Proceed with the merge?"
            )
            default = True
        if not prompts.confirm(question, default=default):
            return MergeStatus.NOT_MERGED

    # Guard the main checkout's HEAD state BEFORE the irreversible merge. `gh pr
    # merge --delete-branch` runs with cwd=main_root and resolves its current
    # branch during the post-merge --delete-branch bookkeeping. If main_root has
    # a detached HEAD — e.g. from unrelated manual git activity — gh aborts with
    # "could not determine current branch", but only AFTER it has already
    # squash-merged the PR on GitHub, leaving a "merged-on-GitHub-but-reported-
    # failed" state. Re-attaching to the default branch here (mirroring the
    # post-merge `git pull` that already targets it) lets us fail fast if we
    # cannot proceed safely, before any GitHub-side merge happens.
    if not git_repo.is_head_attached(main_root):
        try:
            default_branch = git_repo.detect_main_branch(main_root)
            git_repo.checkout(main_root, default_branch)
        except GitError as e:
            logger.error("main_root.reattach_failed", error=str(e))
            console.error(
                f"Cannot merge: '{main_root.name}' has a detached HEAD and could "
                f"not be re-attached to a branch ({e})."
            )
            console.hint(
                f"Check out a branch in {main_root} "
                f"(e.g. `git -C {main_root} checkout main`), then retry the merge."
            )
            return MergeStatus.MERGE_FAILED
        console.step(f"Re-attached {main_root.name} to '{default_branch}' (was detached).")
        logger.info("main_root.reattached", branch=default_branch)

    # Detach HEAD in the worktree so git no longer considers the branch
    # "checked out", which unblocks `gh pr merge --delete-branch`.
    if worktree_path and worktree_path.is_dir():
        with contextlib.suppress(Exception):
            git_repo.checkout_detach(worktree_path)

    try:
        git_pr.merge_pr(repo_root=main_root, pr_number=pr_number, strategy="squash")
    except Exception as e:
        if worktree_path and worktree_path.is_dir():
            with contextlib.suppress(Exception):
                git_repo.checkout(worktree_path, branch)
        logger.error("pr_merge.failed", pr_number=pr_number, error=str(e))
        console.error(f"PR merge failed: {e}")
        console.hint(f"Branch '{branch}' preserved — retry or clean up manually.")
        return MergeStatus.MERGE_FAILED

    # Remove the worktree only after a successful merge.
    if worktree_path:
        _preserve_session_data(main_root, worktree_path)
        console.step(f"Removing worktree: {worktree_path.name}")
        try:
            git_worktree.remove_worktree(main_root, worktree_path)
        except Exception as e:
            # The PR is already merged — do not fail the lifecycle, but report
            # the leftover worktree accurately instead of claiming success.
            console.warn(f"Could not remove worktree {worktree_path.name}: {e}")
            console.hint(f"Remove it manually with: git worktree remove {worktree_path}")
        else:
            with contextlib.suppress(Exception):
                git_worktree.prune_worktrees(main_root)
            console.success(f"Removed {worktree_path.name}")

    _pull_main_after_merge(main_root)

    if issue_number:
        with contextlib.suppress(Exception):
            provider.close_task(str(issue_number))

    return MergeStatus.MERGED


SUMMARY_MARKER_START = "<!-- wade:summary:start -->"
SUMMARY_MARKER_END = "<!-- wade:summary:end -->"

# Review-status block (#367) — projects the done-time review outcome into the PR
# body so a human reviewer can tell a clean review from a skipped/never-run one.
REVIEW_STATUS_MARKER_START = "<!-- wade:review-status:start -->"
REVIEW_STATUS_MARKER_END = "<!-- wade:review-status:end -->"


class SessionType(StrEnum):
    """The two session kinds the completion gate (``done``) distinguishes."""

    IMPLEMENTATION = "implementation"
    REVIEW_PR_COMMENTS = "review-pr-comments"


class ReviewStatusKind(StrEnum):
    """Outcome of the done-time review-ran classification (#367).

    A single value the completion gate (:func:`done._gate_review_ran`) turns into
    pass/refuse and the PR-body renderer (:func:`_render_review_status`) turns
    into a human-legible line — one source of truth so the two cannot drift.
    """

    REVIEWED = "reviewed"  # current commit + active reviewer record is valid
    SKIPPED_FLAG = "skipped_flag"  # ``--skip-review`` passed
    REQUIRE_OFF = "require_off"  # ``done.require_review: false``
    DISABLED = "disabled"  # ``review_implementation.enabled: false``
    CAP_REACHED = "cap_reached"  # impl-only; pass cap hit with no fresh review
    BUNDLE_INVALID = "bundle_invalid"  # frozen workflow/skill content was altered
    REVIEWER_CHANGED = "reviewer_changed"  # same commit reviewed by another binding
    NOT_REVIEWED = "not_reviewed"  # gate would refuse (rendering fallback)


class ReviewStatus(BaseModel):
    """Immutable bundle describing whether review ran for the finalized commit.

    Flows gate → ``done()`` → ``_done_via_pr`` → renderer so the branching is
    decided once (in ``done._classify_review``) and merely rendered downstream —
    no separate ``session_type``/``passes`` args threaded across the boundary,
    they ride inside this object. Distinct from :class:`wade.models.review.
    PRReviewStatus`, which is about PR-level review threads/submissions; this is
    only about whether ``wade review implementation`` *ran* for the pushed commit.
    """

    model_config = {"frozen": True}

    kind: ReviewStatusKind
    passes: int  # distinct attempts for the active reviewer binding
    session_type: SessionType
    reviewed_sha: str  # the pre-sync HEAD the agent reviewed (for display)


def _review_pass_phrase(passes: int) -> str:
    """``review attempted on N distinct commit(s)`` — a count of unique commits a
    review delegation ran against, not a count of confirmed-successful reviews.
    Binding-aware records count completed reviews and real timeouts. The phrase
    must not claim every attempt produced a complete review. Records are
    per-commit and idempotent, so same-HEAD retries are never double-counted.
    """
    noun = "commit" if passes == 1 else "commits"
    return f"review attempted on {passes} distinct {noun}"


def _render_review_status(status: ReviewStatus) -> str:
    """Render the review-status block body — a ``## Review Status`` heading + one line.

    The line makes the review outcome legible to a human reading the PR:
    reviewed / skipped / gate-disabled / cap-reached / not-reviewed. For the
    non-reviewed outcomes it distinguishes "review attempted on N distinct
    commit(s), final commit not reviewed" (``passes > 0``) from "never tried"
    (``passes == 0``) — the core #367 legibility fix; see
    :func:`_review_pass_phrase` for why the count is phrased as attempted
    reviews, not confirmed-successful ones. The count is honest for both
    session types and rendered for the gate-disabled outcomes too
    (``REQUIRE_OFF``/``DISABLED``), not just the skipped/not-reviewed ones, so a
    disabled gate never masks an already-recorded review history — and those
    two branches always state the final commit was not reviewed, without
    claiming *when* relative to the gate the passes happened: marker files
    carry no timestamp, so that chronology can't be known (#367 follow-up).
    ``CAP_REACHED`` is only ever produced for implementation sessions (the
    classifier scopes the cap there), so its wording never leaks into a
    review-pr-comments PR.
    """
    kind = status.kind
    short_sha = status.reviewed_sha[:7] if status.reviewed_sha else "unknown"

    if kind is ReviewStatusKind.REVIEWED:
        line = f"✅ Reviewed at `{short_sha}` via `wade review implementation`."
    elif kind is ReviewStatusKind.SKIPPED_FLAG:
        if status.passes > 0:
            line = (
                f"⚠️ Review skipped (`--skip-review`); {_review_pass_phrase(status.passes)}, "
                "but the final commit was not reviewed."
            )
        else:
            line = "⚠️ Review skipped (`--skip-review`); no review was attempted (review never ran)."
    elif kind is ReviewStatusKind.CAP_REACHED:
        line = (
            f"⚠️ Completed with {_review_pass_phrase(status.passes)}; the final commit "
            "was not freshly reviewed (`done.max_review_passes` cap reached)."
        )
    elif kind is ReviewStatusKind.REVIEWER_CHANGED:
        line = (
            "⚠️ The final commit was reviewed with a different methodology binding; "
            "the active reviewer has not reviewed it."
        )
    elif kind is ReviewStatusKind.BUNDLE_INVALID:
        line = "⚠️ The frozen session bundle failed integrity validation."
    elif kind is ReviewStatusKind.REQUIRE_OFF:
        # The leading info emoji is intentional PR-body markdown, not an identifier.
        line = (
            "ℹ️ Review gate disabled (`done.require_review: false`); "  # noqa: RUF001
            "the final commit was not reviewed."
        )
        if status.passes > 0:
            line += f" {_review_pass_phrase(status.passes).capitalize()}."
    elif kind is ReviewStatusKind.DISABLED:
        line = (
            "ℹ️ Review gate disabled (`review_implementation.enabled: false`); "  # noqa: RUF001
            "the final commit was not reviewed."
        )
        if status.passes > 0:
            line += f" {_review_pass_phrase(status.passes).capitalize()}."
    else:  # NOT_REVIEWED — rendering fallback (the gate would normally have refused).
        if status.passes > 0:
            line = f"⚠️ {_review_pass_phrase(status.passes)}, but the final commit was not reviewed."
        else:
            line = "⚠️ The final commit was not reviewed (review never ran)."

    return f"## Review Status\n\n{line}"


def _strip_summary_section(body: str) -> str:
    """Remove an existing ``## Summary`` section from a PR body.

    Handles the *legacy* unmarked ``## Summary`` heading (written before #357
    wrapped the section in ``wade:summary`` markers). The marked block is
    removed separately via ``remove_marker_block``.

    The body may contain an implementation-usage block delimited by HTML
    comment markers.  We use that marker as a hard boundary so freeform
    summary content (which may itself contain ``## `` subheadings) is fully
    removed without eating into the impl-usage block.  The caller then
    re-inserts the new summary *before* any impl-usage block.
    """
    idx = body.find("\n## Summary\n")
    if idx == -1:
        # Also check at the very start of the string
        if body.startswith("## Summary\n"):
            idx = 0
        else:
            return body

    before = body[:idx]

    # Find the next structural boundary after the summary heading.
    # Prefer the impl-usage HTML marker; fall back to the next ## heading.
    marker_idx = body.find(IMPL_USAGE_MARKER_START, idx)
    if marker_idx != -1:
        after = body[marker_idx:]
    else:
        # No impl-usage marker — look for next ## heading after the summary title
        summary_title_end = idx + len("\n## Summary\n")
        if body.startswith("## Summary\n"):
            summary_title_end = len("## Summary\n")
        next_heading = re.search(r"(?:^|\n)## ", body[summary_title_end:])
        after = body[summary_title_end + next_heading.start() :] if next_heading else ""

    result = before.rstrip("\n")
    if after:
        result = result + "\n\n" + after
    return result if result else ""


def _apply_pr_refs(
    body: str,
    issue_number: str,
    close_issue: bool,
    parent_issue: str | None,
) -> str:
    """Add or update Closes/Part-of references in a PR body.

    Idempotent: repeated calls do not duplicate references.
    """
    updated = body

    # Add "Closes #N" if requested and not already present
    if close_issue:
        close_pattern = rf"^Closes\s+#{re.escape(issue_number)}\b"
        if not re.search(close_pattern, updated, flags=re.MULTILINE):
            # Strip existing "Implements #N" line when upgrading to "Closes #N"
            updated = re.sub(
                rf"^Implements\s+#{re.escape(issue_number)}\s*\n?",
                "",
                updated,
                flags=re.MULTILINE,
            )
            updated = f"Closes #{issue_number}\n\n" + updated.lstrip("\n")
    else:
        # --no-close: downgrade any existing "Closes #N" to "Implements #N" so
        # merging the PR does not auto-close the issue against the caller's intent.
        updated = re.sub(
            rf"^Closes\s+#{re.escape(issue_number)}\b",
            f"Implements #{issue_number}",
            updated,
            flags=re.MULTILINE,
        )

    # Add "Part of #parent" if detected and not already present
    if parent_issue:
        parent_pattern = rf"^Part of\s+#{re.escape(parent_issue)}\b"
        if not re.search(parent_pattern, updated, flags=re.MULTILINE):
            updated = f"Part of #{parent_issue}\n" + updated

    return updated


def _build_pr_body(
    task: Task,
    pr_summary_path: Path | None = None,
    close_issue: bool = True,
    parent_issue: str | None = None,
    *,
    review_status: ReviewStatus | None = None,
) -> str:
    """Compose the PR body (new-PR fallback path).

    Order:
    1. Part of #parent (if detected)
    2. Closes #N
    3. ## Summary (from PR-SUMMARY file)
    4. ## Review Status (from the done-time review classification, if provided)

    Plan summary stays on the issue only — not copied into the PR body.
    ``review_status`` is optional so the many callers that only compose
    refs/summary need not construct one; ``done()`` always supplies it, so a real
    PR always carries the review-status block.
    """
    from wade.utils.body_markers import build_marked_block

    parts: list[str] = []

    ref_lines: list[str] = []
    if parent_issue:
        ref_lines.append(f"Part of #{parent_issue}")
    if close_issue:
        ref_lines.append(f"Closes #{task.id}")
    if ref_lines:
        parts.append("\n".join(ref_lines))

    # PR summary from file — wrapped in wade:summary markers so a later `done`
    # rewrites only this block and preserves any concurrent edits (A4).
    if pr_summary_path and pr_summary_path.is_file():
        summary_content = pr_summary_path.read_text(encoding="utf-8").strip()
        if summary_content:
            parts.append(
                build_marked_block(
                    SUMMARY_MARKER_START,
                    SUMMARY_MARKER_END,
                    f"## Summary\n\n{summary_content}",
                )
            )

    # Review-status block — same marker-scoped treatment, placed after the summary.
    if review_status is not None:
        parts.append(
            build_marked_block(
                REVIEW_STATUS_MARKER_START,
                REVIEW_STATUS_MARKER_END,
                _render_review_status(review_status),
            )
        )

    return "\n\n".join(parts)
