"""Post-implementation lifecycle and PR-body composition.

Runs after the AI session exits: merge (PR or direct), worktree cleanup, main
pull, and issue close. Also owns the PR-body helpers shared with ``done``.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import webbrowser
from pathlib import Path

import structlog

from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.git.repo import GitError
from wade.models.config import ProjectConfig
from wade.models.session import MergeStatus, MergeStrategy
from wade.models.task import Task
from wade.providers.base import AbstractTaskProvider
from wade.services.implementation_service.cleanup import (
    _cleanup_worktree,
    _preserve_session_data,
)
from wade.services.implementation_service.usage_tracking import IMPL_USAGE_MARKER_START
from wade.ui import prompts
from wade.ui.console import console

logger = structlog.get_logger()

__all__ = [
    "_apply_pr_refs",
    "_build_pr_body",
    "_merge_pr",
    "_parse_overwrite_paths",
    "_post_implementation_lifecycle",
    "_post_implementation_lifecycle_direct",
    "_post_implementation_lifecycle_pr",
    "_pull_main_after_merge",
    "_strip_summary_section",
    "_warn_pull_sync_failed",
]


def _post_implementation_lifecycle(
    repo_root: Path,
    branch: str,
    issue_number: str | int | None,
    worktree_path: Path | None,
    config: ProjectConfig,
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
    """Run post-implementation lifecycle and return the merge status."""
    if config.project.merge_strategy == MergeStrategy.PR:
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
    return _post_implementation_lifecycle_direct(
        repo_root, branch, issue_number, worktree_path, config, provider
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
    """
    result = git_repo.pull_ff_only(repo_root)
    if result.returncode == 0:
        return
    if "untracked working tree files would be overwritten by merge" in result.stderr:
        # NEVER delete the colliding files — git reports every untracked
        # collision here, not just wade-managed ones, so unlinking could destroy
        # user data. Move each aside into a backup dir before retrying the pull.
        backup_root = repo_root / ".wade" / "pull-backups"
        backed_up: list[Path] = []
        for rel_path in _parse_overwrite_paths(result.stderr):
            target = repo_root / rel_path
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
        retry = git_repo.pull_ff_only(repo_root)
        if backed_up:
            console.warn("Backed up untracked files that collided with the merge:")
            for dest in backed_up:
                console.detail(str(dest))
        if retry.returncode != 0:
            _warn_pull_sync_failed()
    elif "Your local changes to the following files would be overwritten" in result.stderr:
        # Stash local changes, pull, then restore
        stash_result = git_repo.stash(repo_root)
        if stash_result.returncode != 0:
            _warn_pull_sync_failed()
            return
        retry = git_repo.pull_ff_only(repo_root)
        pop_result = git_repo.stash_pop(repo_root)
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
    pr_info = git_pr.get_pr_for_branch(repo_root, branch)
    if not pr_info:
        console.warn(f"No open PR found for branch '{branch}'. Skipping lifecycle.")
        return MergeStatus.NOT_MERGED

    pr_number = pr_info.get("number") or pr_info.get("pr_number")
    if not pr_number:
        console.warn(f"Could not determine PR number for branch '{branch}'.")
        return MergeStatus.NOT_MERGED

    pr_url = str(pr_info.get("url", ""))
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
    # Warn if the worktree has uncommitted changes before proceeding.
    if worktree_path and worktree_path.is_dir() and not git_repo.is_clean(worktree_path):
        console.warn("Worktree has uncommitted changes.")
        if not prompts.confirm("Proceed anyway? Uncommitted work will be lost.", default=False):
            return MergeStatus.NOT_MERGED

    # Detach HEAD in the worktree so git no longer considers the branch
    # "checked out", which unblocks `gh pr merge --delete-branch`.
    if worktree_path and worktree_path.is_dir():
        with contextlib.suppress(Exception):
            git_repo.checkout_detach(worktree_path)

    try:
        git_pr.merge_pr(repo_root=repo_root, pr_number=pr_number, strategy="squash")
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
        _preserve_session_data(repo_root, worktree_path)
        console.step(f"Removing worktree: {worktree_path.name}")
        try:
            git_worktree.remove_worktree(repo_root, worktree_path)
        except Exception as e:
            # The PR is already merged — do not fail the lifecycle, but report
            # the leftover worktree accurately instead of claiming success.
            console.warn(f"Could not remove worktree {worktree_path.name}: {e}")
            console.hint(f"Remove it manually with: git worktree remove {worktree_path}")
        else:
            with contextlib.suppress(Exception):
                git_worktree.prune_worktrees(repo_root)
            console.success(f"Removed {worktree_path.name}")

    _pull_main_after_merge(repo_root)

    if issue_number:
        with contextlib.suppress(Exception):
            provider.close_task(str(issue_number))

    return MergeStatus.MERGED


def _post_implementation_lifecycle_direct(
    repo_root: Path,
    branch: str,
    issue_number: str | int | None,
    worktree_path: Path | None,
    config: ProjectConfig,
    provider: AbstractTaskProvider,
) -> MergeStatus:
    """Run the direct-merge post-implementation lifecycle."""
    main_branch = config.project.main_branch
    if not main_branch:
        # Detect the repo's real default branch (master/trunk/...) instead of
        # assuming "main", matching sync(), done(), and cleanup.remove().
        try:
            main_branch = git_repo.detect_main_branch(repo_root)
        except GitError:
            console.warn("Could not detect the main branch.")
            return MergeStatus.MERGE_FAILED
    try:
        ahead = git_branch.commits_ahead(repo_root, branch, main_branch)
    except GitError:
        console.warn("Could not determine commit count; skipping post-implementation lifecycle.")
        return MergeStatus.MERGE_FAILED

    if ahead == 0:
        if not prompts.confirm("Branch has no new commits. Delete empty worktree?", default=False):
            return MergeStatus.NOT_MERGED
        if worktree_path:
            _cleanup_worktree(repo_root, worktree_path, main_branch)
        return MergeStatus.NOT_MERGED

    choices = ["Merge into main", "Merge + close task", "Skip"]
    idx = prompts.select(f"Branch '{branch}' has {ahead} commit(s). What next?", choices)
    choice = choices[idx]
    if choice == "Skip":
        return MergeStatus.NOT_MERGED

    try:
        git_repo.merge_squash(repo_root, branch)
        git_repo.commit_no_edit(repo_root)
        git_repo.push(repo_root)
    except (GitError, Exception) as e:
        logger.error("direct_merge.failed", branch=branch, error=str(e))
        return MergeStatus.MERGE_FAILED

    if worktree_path:
        _cleanup_worktree(repo_root, worktree_path, main_branch)

    if choice == "Merge + close task" and issue_number:
        with contextlib.suppress(Exception):
            provider.close_task(str(issue_number))

    return MergeStatus.MERGED


def _strip_summary_section(body: str) -> str:
    """Remove an existing ``## Summary`` section from a PR body.

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
) -> str:
    """Compose the PR body.

    Order:
    1. Part of #parent (if detected)
    2. Closes #N
    3. ## Summary (from PR-SUMMARY file)

    Plan summary stays on the issue only — not copied into the PR body.
    """
    lines: list[str] = []

    if parent_issue:
        lines.append(f"Part of #{parent_issue}")
    if close_issue:
        lines.append(f"Closes #{task.id}")

    if lines:
        lines.append("")

    # PR summary from file
    if pr_summary_path and pr_summary_path.is_file():
        summary_content = pr_summary_path.read_text(encoding="utf-8").strip()
        if summary_content:
            lines.append("## Summary")
            lines.append("")
            lines.append(summary_content)

    return "\n".join(lines)
