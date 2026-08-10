"""Post-implementation lifecycle and PR-body composition.

Runs after the AI session exits: merge (PR or direct), worktree cleanup, main
pull, and issue close. Also owns the PR-body helpers shared with ``done``.
"""

from __future__ import annotations

import contextlib
import re
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
from wade.models.session import MergeStatus
from wade.models.task import Task
from wade.providers.base import AbstractTaskProvider
from wade.services.implementation_service.cleanup import _preserve_session_data
from wade.services.implementation_service.usage_tracking import IMPL_USAGE_MARKER_START
from wade.ui import prompts
from wade.ui.console import console

logger = structlog.get_logger()

__all__ = [
    "ReviewStatus",
    "ReviewStatusKind",
    "SessionType",
    "_apply_pr_refs",
    "_build_pr_body",
    "_merge_pr",
    "_parse_overwrite_paths",
    "_post_implementation_lifecycle",
    "_post_implementation_lifecycle_pr",
    "_pull_main_after_merge",
    "_render_review_status",
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
) -> MergeStatus:
    """Run post-implementation lifecycle and return the merge status.

    PR is the only supported merge strategy (the ``direct`` strategy was
    retired in #357), so this delegates straight to the PR lifecycle.
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
    )


def _parse_overwrite_paths(stderr: str) -> list[str]:
    """Extract conflicting file paths from a git 'would be overwritten' error."""
    paths: list[str] = []
    in_block = False
    for line in stderr.splitlines():
        if "would be overwritten by merge" in line:
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


def _pull_main_after_merge(repo_root: Path) -> None:
    """Pull the latest main branch after a successful PR merge.

    Handles the common case where wade-managed files (skills, settings) were
    installed by ``wade init`` as untracked files in the repo root. When the PR
    being merged introduced those same files as tracked, a plain ``git pull``
    aborts with "untracked files would be overwritten". This helper detects that
    condition, backs up the conflicting untracked files into ``.wade/pull-backups``
    (never deleting them, since git reports arbitrary untracked collisions here —
    not only wade-managed files), then retries the pull so the tracked versions
    take their place.

    Also handles local modifications to tracked files (e.g. ``wade init``
    modifying ``.gitignore``) by stashing, pulling, and popping the stash.

    Pulling main only makes sense in the *main checkout*: a ``git pull`` run from
    a linked worktree (which is on a feature branch) would target the wrong
    branch, so we resolve the main checkout root first.
    """
    main_root = git_repo.main_checkout_root(repo_root)
    result = git_repo.pull_ff_only(main_root)
    if result.returncode == 0:
        return
    if "untracked working tree files would be overwritten by merge" in result.stderr:
        # NEVER delete the colliding files — git reports every untracked
        # collision here, not just wade-managed ones, so unlinking could destroy
        # user data. Move each aside into a backup dir before retrying the pull.
        backup_root = main_root / ".wade" / "pull-backups"
        backed_up: list[Path] = []
        for rel_path in _parse_overwrite_paths(result.stderr):
            target = main_root / rel_path
            if not target.exists():
                continue
            dest = backup_root / rel_path
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(dest))
                backed_up.append(dest)
            except OSError:
                logger.warning("pull.backup_untracked_failed", path=str(target), exc_info=True)
            with contextlib.suppress(OSError):
                target.parent.rmdir()
        retry = git_repo.pull_ff_only(main_root)
        if backed_up:
            console.warn("Backed up untracked files that collided with the merge:")
            for dest in backed_up:
                console.detail(str(dest))
        if retry.returncode != 0:
            _warn_pull_sync_failed()
    elif "Your local changes to the following files would be overwritten" in result.stderr:
        # Stash local changes, pull, then restore
        stash_result = git_repo.stash(main_root)
        if stash_result.returncode != 0:
            _warn_pull_sync_failed()
            return
        retry = git_repo.pull_ff_only(main_root)
        pop_result = git_repo.stash_pop(main_root)
        if pop_result.returncode != 0:
            console.warn("Could not restore stashed local changes.")
            console.hint("Resolve conflicts, then inspect `git stash list`.")
        if retry.returncode != 0:
            _warn_pull_sync_failed()
    else:
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

    choice = prompts.select(
        f"PR #{pr_number} — what next?",
        ["Merge PR", "Wait for reviews"],
    )

    if choice == 1:  # Wait for reviews
        from wade.models.review import PollOutcome
        from wade.services import review_service

        outcome = review_service.poll_for_reviews(provider, repo_root, int(pr_number), branch)
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

    # Warn if the worktree has uncommitted changes before proceeding.
    if worktree_path and worktree_path.is_dir() and not git_repo.is_clean(worktree_path):
        console.warn("Worktree has uncommitted changes.")
        if not prompts.confirm("Proceed anyway? Uncommitted work will be lost.", default=False):
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

    REVIEWED = "reviewed"  # exact-sha ``reviewed@<head>`` marker present
    SKIPPED_FLAG = "skipped_flag"  # ``--skip-review`` passed
    REQUIRE_OFF = "require_off"  # ``done.require_review: false``
    DISABLED = "disabled"  # ``review_implementation.enabled: false``
    CAP_REACHED = "cap_reached"  # impl-only; pass cap hit with no fresh review
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
    passes: int  # distinct review-pass markers (``count_review_passes()``)
    session_type: SessionType
    reviewed_sha: str  # the pre-sync HEAD the agent reviewed (for display)


def _review_pass_phrase(passes: int) -> str:
    """``review attempted on N distinct commit(s)`` — a count of unique commits a
    review delegation ran against, not a count of confirmed-successful reviews.
    ``review_delegation_service._record_review_pass`` writes the
    ``review-pass@<sha>`` marker *before* checking ``result.success``, so a
    headless timeout or other delegation failure still advances this count
    (#384) — the phrase must not claim those commits were actually reviewed,
    only that a review was attempted. ``markers.record_review_pass`` is per-sha
    and idempotent, so retrying against the same HEAD is never double-counted.
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
