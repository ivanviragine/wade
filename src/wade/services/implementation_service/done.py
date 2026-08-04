"""Implementation session completion — create/finish PR or merge directly."""

from __future__ import annotations

import contextlib
from pathlib import Path

import structlog

from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git.repo import GitError
from wade.models.config import ProjectConfig
from wade.providers.registry import get_provider
from wade.services.implementation_service._shared import (
    extract_issue_from_branch,
    find_worktree_path,
)
from wade.services.implementation_service.bootstrap import (
    _check_tracked_managed_files,
    _format_uncommitted_summary,
    _get_dirty_file_paths,
    _identify_session_dirty_files,
    strip_worktree_gitignore,
)
from wade.services.implementation_service.core import _resolve_worktree_from_plan
from wade.services.implementation_service.lifecycle import (
    _apply_pr_refs,
    _build_pr_body,
    _strip_summary_section,
)
from wade.services.implementation_service.usage_tracking import IMPL_USAGE_MARKER_START
from wade.services.task_service import remove_in_progress_label
from wade.ui.console import console

logger = structlog.get_logger()

__all__ = [
    "_done_via_pr",
    "done",
]


def done(
    target: str | None = None,
    plan_file: Path | None = None,
    no_close: bool = False,
    draft: bool = False,
    project_root: Path | None = None,
) -> bool:
    """Complete implementation session — push the branch and finalize the PR.

    Detects the current branch, extracts the issue number, and delegates to
    ``_done_via_pr`` (PR is the only merge strategy since #357 retired
    ``direct``).

    Args:
        target: Optional issue number, worktree name, or plan file.
            If None, detects from current branch.
        no_close: Don't close the issue on merge.
        draft: Create PR as draft.
        project_root: Repository root.
    """
    config = load_config(project_root)
    provider = get_provider(config)
    cwd = project_root or Path.cwd()

    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error("Not inside a git repository")
        return False

    resolved_wt_path: Path | None = None
    if plan_file is not None:
        try:
            resolved_wt_path, resolved_branch, issue_num = _resolve_worktree_from_plan(
                plan_file, project_root=project_root
            )
            console.step("Resolved from plan:")
            console.detail(f"Worktree: {resolved_wt_path}")
            console.detail(f"Branch: {resolved_branch}")
            target = issue_num
        except ValueError as e:
            console.error(str(e))
            return False

    # If target is a plan file, create issue first (skip if target looks like a number)
    if target and not target.isdigit():
        target_path = Path(target).expanduser()
        if target_path.is_file():
            from wade.services.task_service import create_from_plan_file

            console.info(f"Creating issue from plan file: {target}")
            task = create_from_plan_file(target_path, config=config, provider=provider)
            if not task:
                return False
            target = task.id

    wt_path: Path | None = resolved_wt_path

    if wt_path is not None:
        cwd = wt_path

    # If target specifies a worktree, navigate to it and extract issue number
    if target and wt_path is None:
        wt_path = find_worktree_path(target, project_root=repo_root)
        if wt_path:
            cwd = wt_path
            # Replace non-numeric target with the issue number from the branch
            if not target.isdigit():
                try:
                    wt_branch = git_repo.get_current_branch(wt_path)
                    extracted = extract_issue_from_branch(wt_branch)
                    if extracted:
                        target = extracted
                except GitError:
                    pass

    # If running from inside a linked worktree with no explicit target,
    # use cwd as the worktree path so PR-SUMMARY.md lookup works.
    if wt_path is None and git_repo.is_worktree(cwd):
        wt_path = cwd

    # Detect branch and issue
    try:
        branch = git_repo.get_current_branch(cwd)
    except GitError:
        console.error_with_fix(
            "Cannot determine current branch",
            "Check that HEAD is not detached",
        )
        return False

    issue_number = target or extract_issue_from_branch(branch)
    if not issue_number:
        console.error(f"Cannot extract issue number from branch: {branch}")
        return False

    # Check clean — keep the worktree gitignore block in place so wade
    # artifacts (PLAN.md, .wade/, etc.) stay hidden from git status.
    # Strip it only after the gate passes.
    if not git_repo.is_clean(cwd):
        detail_str = _format_uncommitted_summary(cwd)
        dirty_paths = _get_dirty_file_paths(cwd)
        session_files = _identify_session_dirty_files(dirty_paths)
        console.error(f"Working tree is dirty ({detail_str})")
        if session_files:
            console.warn(
                "The following dirty files are wade session artifacts"
                " \N{EM DASH} do NOT commit them."
            )
            console.hint("Restore with: git checkout -- <file>")
            for sf in session_files:
                console.detail(sf)
            console.empty()
        console.hint(
            "Commit or stash your non-session changes first"
            if session_files
            else "Commit or stash your changes first"
        )
        return False

    # Clean gate passed — now strip the worktree gitignore block and
    # restore .gitignore visibility so downstream operations see the
    # true state.
    with contextlib.suppress(OSError):
        git_repo.unskip_worktree_file(cwd, ".gitignore")
    strip_worktree_gitignore(cwd)

    # Check for tracked wade-managed files that should never be committed
    tracked_managed = _check_tracked_managed_files(cwd)
    if tracked_managed:
        console.error("Wade-managed files are tracked in git — these must not be committed")
        for path in tracked_managed:
            console.detail(f"  {path}")
        console.info("Untrack them with:")
        for path in tracked_managed:
            console.detail(f"  git rm --cached {path}")
        console.info("Then commit the removal and re-run done.")
        return False

    main_branch = config.project.main_branch
    if not main_branch:
        try:
            main_branch = git_repo.detect_main_branch(repo_root)
        except GitError:
            console.error("Cannot detect main branch")
            return False

    # Check for stacked base branch metadata (written by start() for chain execution).
    # When present, target the parent branch instead of main — same as sync().
    if wt_path:
        base_branch_file = wt_path / ".wade" / "base_branch"
        if base_branch_file.is_file():
            stored_base = base_branch_file.read_text().strip()
            if stored_base and git_branch.branch_exists(repo_root, stored_base):
                main_branch = stored_base

    console.rule(f"done #{issue_number}")

    return _done_via_pr(
        repo_root=repo_root,
        branch=branch,
        issue_number=issue_number,
        main_branch=main_branch,
        close_issue=not no_close,
        draft=draft,
        config=config,
        worktree_path=wt_path,
    )


def _done_via_pr(
    repo_root: Path,
    branch: str,
    issue_number: str,
    main_branch: str,
    close_issue: bool,
    draft: bool,
    config: ProjectConfig,
    worktree_path: Path | None = None,
) -> bool:
    """Finalize implementation — update existing draft PR or create a new one.

    In the new workflow, a draft PR should already exist (created by plan
    or implement). This function:
    1. Pushes the branch
    2. Appends PR-SUMMARY content to the existing PR body
    3. Marks the draft PR as ready for review
    """
    provider = get_provider(config)
    pr_url = ""

    # Read issue for title and body
    try:
        task = provider.read_task(issue_number)
    except Exception as e:
        console.error(f"Cannot read issue #{issue_number}: {e}")
        return False

    # Push branch
    console.step("Pushing branch...")
    try:
        git_repo.push_branch(repo_root, branch, set_upstream=True)
        console.success("Branch pushed.")
    except GitError as e:
        console.error(f"Push failed: {e}")
        return False

    # Check for existing PR (expected from plan or implement bootstrap).
    # A lookup failure is transient — do NOT fall through to "create a new PR"
    # (that would duplicate the draft); report and let the user retry. A merged
    # or closed PR must not be body-updated / re-marked-ready as if it were open.
    lookup = git_pr.get_pr_for_branch(repo_root, branch)
    if lookup.lookup_failed:
        console.error(f"Could not look up the PR for branch '{branch}' — try again shortly.")
        return False
    if lookup.is_merged:
        console.error(f"PR #{lookup.number} is already merged — nothing to finalize.")
        return False
    if lookup.found and not lookup.is_open:
        console.error(
            f"PR #{lookup.number} is {lookup.state.lower()} — reopen it or start a new branch."
        )
        return False
    existing_pr = lookup.pr if lookup.is_open else None

    # Resolve PR-SUMMARY.md from worktree root
    pr_summary_path: Path | None = None
    if worktree_path and (worktree_path / "PR-SUMMARY.md").exists():
        pr_summary_path = worktree_path / "PR-SUMMARY.md"

    if pr_summary_path is None:
        console.warn("No PR-SUMMARY.md found — PR description will have no summary.")
        if worktree_path:
            console.detail(f"Expected: {worktree_path / 'PR-SUMMARY.md'}")

    if existing_pr is not None:
        # Update existing PR: append summary
        pr_number = existing_pr.number
        pr_url = existing_pr.url
        console.step(f"Updating existing PR #{pr_number}...")

        # Read current PR body and append summary
        current_body = git_pr.get_pr_body(repo_root, pr_number) or ""

        # Build summary section
        summary_section = ""
        if pr_summary_path and pr_summary_path.is_file():
            summary_content = pr_summary_path.read_text(encoding="utf-8").strip()
            if summary_content:
                summary_section = f"\n\n## Summary\n\n{summary_content}"

        # Detect parent tracking issue
        parent_issue: str | None = None
        try:
            parent_issue = provider.find_parent_issue(
                issue_number, label=config.project.issue_label
            )
            if parent_issue:
                console.detail(f"Detected parent tracking issue: #{parent_issue}")
        except Exception:
            logger.debug("implementation.parent_issue_detection_failed", exc_info=True)

        # Build updated body: keep existing content, add close/parent references + summary
        updated_body = _apply_pr_refs(current_body, issue_number, close_issue, parent_issue)
        # Strip any existing ## Summary section to avoid duplication on retry.
        # Use the impl-usage HTML marker as a hard boundary so that freeform
        # summary content (which may contain ## subheadings) is fully removed.
        updated_body = _strip_summary_section(updated_body)
        # Insert summary before any impl-usage block so ordering stays
        # consistent: content → summary → impl-usage.
        if summary_section:
            marker_pos = updated_body.find(IMPL_USAGE_MARKER_START)
            if marker_pos != -1:
                before = updated_body[:marker_pos].rstrip("\n")
                after = updated_body[marker_pos:]
                updated_body = before + summary_section + "\n\n" + after + "\n"
            else:
                updated_body = updated_body.rstrip("\n") + summary_section + "\n"
        else:
            updated_body = updated_body.rstrip("\n") + "\n"

        if not git_pr.update_pr_body(repo_root, pr_number, updated_body):
            console.error("Could not update the PR body.")
            return False
        console.success("PR body updated with summary.")

        # Mark draft as ready — but only if the caller did not request a draft.
        is_draft = existing_pr.is_draft
        if is_draft and not draft:
            if git_pr.mark_pr_ready(repo_root, pr_number):
                console.success("PR marked as ready for review.")
            else:
                console.warn("Could not mark PR as ready — do it manually.")
    else:
        # No existing PR — create one (fallback)
        console.warn("No existing draft PR found — creating new PR.")

        # Detect parent tracking issue
        parent_issue = None
        try:
            parent_issue = provider.find_parent_issue(
                issue_number, label=config.project.issue_label
            )
            if parent_issue:
                console.detail(f"Detected parent tracking issue: #{parent_issue}")
        except Exception:
            logger.debug("implementation.parent_issue_detection_failed", exc_info=True)

        body = _build_pr_body(
            task,
            pr_summary_path=pr_summary_path,
            close_issue=close_issue,
            parent_issue=parent_issue,
        )

        console.step("Creating pull request...")
        try:
            pr_info = git_pr.create_pr(
                repo_root=repo_root,
                title=task.title,
                body=body,
                base=main_branch,
                head=branch,
                draft=draft,
            )
            pr_url = str(pr_info.get("url", ""))
            console.success(f"PR created: {pr_url}")
        except Exception as e:
            console.error(f"PR creation failed: {e}")
            return False

    # Remove in-progress label
    with contextlib.suppress(Exception):
        remove_in_progress_label(provider, issue_number)

    lines = []
    lines.append(f"  PR      [url]{pr_url}[/]")
    lines.append(f"  Issue   {console.issue_ref(issue_number, task.title)}")
    console.panel("\n".join(lines), title="Implementation done")

    return True
