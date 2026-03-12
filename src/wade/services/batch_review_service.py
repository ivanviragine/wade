"""Batch coherence review service — post-batch review pipeline.

Orchestrates: extract child issues from tracking issue → gather context →
create integration branch → open draft PR → run AI coherence review → post
findings as PR comment.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

import structlog

from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import sync as git_sync
from wade.git.repo import GitError
from wade.models.batch import BatchIssueContext, BatchReviewContext
from wade.models.delegation import DelegationMode, DelegationResult
from wade.providers.registry import get_provider
from wade.services.review_delegation_service import (
    _check_review_enabled,
    _load_review_config,
    _run_review_delegation,
)
from wade.skills.installer import load_prompt_template
from wade.ui.console import console

logger = structlog.get_logger()

_CHECKLIST_RE = re.compile(r"- \[[ xX]\] #(\d+)")
"""Matches ``- [ ] #42`` or ``- [x] #42`` in a tracking issue body."""


def extract_child_issues(tracking_body: str) -> list[str]:
    """Parse child issue numbers from a tracking issue checklist.

    Matches ``- [ ] #N`` and ``- [x] #N`` patterns.

    Returns:
        List of issue number strings (without ``#`` prefix).
    """
    return _CHECKLIST_RE.findall(tracking_body)


def gather_batch_context(
    tracking_issue_id: str,
    repo_root: Path | None = None,
) -> BatchReviewContext:
    """Read tracking issue and gather context for all child issues.

    For each child issue: reads the task, computes the expected branch name,
    checks for an existing PR, and collects diff stats.
    """
    config = load_config(repo_root)
    provider = get_provider(config)
    cwd = repo_root or Path.cwd()

    try:
        repo = git_repo.get_repo_root(cwd)
    except GitError:
        repo = cwd

    main_branch = config.project.main_branch or git_repo.detect_main_branch(repo)

    tracking_task = provider.read_task(tracking_issue_id)
    child_numbers = extract_child_issues(tracking_task.body)

    if not child_numbers:
        console.warn(f"No child issues found in tracking issue #{tracking_issue_id}")

    issues: list[BatchIssueContext] = []
    for num in child_numbers:
        try:
            task = provider.read_task(num)
        except Exception:
            logger.debug("batch_review.issue_read_failed", issue_num=num, exc_info=True)
            issues.append(BatchIssueContext(issue_number=num, issue_title=f"(unreadable #{num})"))
            continue

        branch_name = git_branch.make_branch_name(
            config.project.branch_prefix, int(num), task.title
        )

        # Check if branch exists locally (may need fetch first)
        if not git_branch.branch_exists(repo, branch_name):
            # Try fetching from remote
            try:
                git_repo.fetch_ref(repo, "origin", f"{branch_name}:{branch_name}")
            except GitError:
                logger.debug("batch_review.fetch_failed", branch=branch_name, exc_info=True)

        pr_info = git_pr.get_pr_for_branch(repo, branch_name)
        pr_number = int(pr_info["number"]) if pr_info and "number" in pr_info else None
        pr_url = str(pr_info["url"]) if pr_info and "url" in pr_info else None
        status = str(pr_info.get("state", "")) if pr_info else ""

        branch_available = git_branch.branch_exists(repo, branch_name)
        diff_stat = ""
        if branch_available:
            diff_stat = git_repo.diff_stat_between(repo, main_branch, branch_name)

        issues.append(
            BatchIssueContext(
                issue_number=num,
                issue_title=task.title,
                branch_name=branch_name if branch_available else None,
                pr_number=pr_number,
                pr_url=pr_url,
                diff_stat=diff_stat,
                status=status,
            )
        )

    return BatchReviewContext(
        issues=issues,
        main_branch=main_branch,
        tracking_issue=tracking_issue_id,
    )


def create_integration_branch(
    repo_root: Path,
    ctx: BatchReviewContext,
) -> BatchReviewContext:
    """Create an integration branch and merge all batch branches into it.

    On merge conflict: aborts the merge, marks the issue as conflicting,
    and continues with the remaining branches.

    Returns:
        Updated context with merge results and integration branch name set.
    """
    integration_branch = f"batch-review/{ctx.tracking_issue}"
    main_branch = ctx.main_branch

    # Delete existing integration branch if present
    if git_branch.branch_exists(repo_root, integration_branch):
        git_branch.delete_branch(repo_root, integration_branch, force=True)

    git_branch.create_branch(repo_root, integration_branch, main_branch)
    git_repo.checkout(repo_root, integration_branch)

    for issue in ctx.issues:
        if not issue.branch_name:
            continue

        try:
            git_repo.merge_no_edit(repo_root, issue.branch_name)
            issue.merged = True
            logger.info("batch_review.merged", issue=issue.issue_number)
        except GitError:
            # Conflict — abort and continue
            with contextlib.suppress(GitError):
                git_sync.abort_merge(repo_root)
            issue.conflict = True
            logger.info(
                "batch_review.conflict",
                issue=issue.issue_number,
                branch=issue.branch_name,
            )

    ctx.integration_branch = integration_branch
    return ctx


def create_review_pr(
    repo_root: Path,
    ctx: BatchReviewContext,
) -> BatchReviewContext:
    """Push integration branch and open a draft PR.

    Returns:
        Updated context with PR number and URL set.
    """
    if not ctx.integration_branch:
        console.error("No integration branch to push.")
        return ctx

    git_repo.push_branch(repo_root, ctx.integration_branch, set_upstream=True)

    merged_list = [f"- #{i.issue_number} {i.issue_title}" for i in ctx.issues if i.merged]
    conflict_list = [f"- #{i.issue_number} {i.issue_title}" for i in ctx.issues if i.conflict]
    skipped_list = [
        f"- #{i.issue_number} {i.issue_title}"
        for i in ctx.issues
        if not i.merged and not i.conflict
    ]

    body_parts = [f"Part of #{ctx.tracking_issue}\n"]
    body_parts.append("## Batch coherence review\n")

    if merged_list:
        body_parts.append("### Merged\n" + "\n".join(merged_list) + "\n")
    if conflict_list:
        body_parts.append("### Conflicts\n" + "\n".join(conflict_list) + "\n")
    if skipped_list:
        body_parts.append("### Skipped (no branch)\n" + "\n".join(skipped_list) + "\n")

    body = "\n".join(body_parts)

    pr_info = git_pr.create_pr(
        repo_root,
        title=f"Batch review: tracking #{ctx.tracking_issue}",
        body=body,
        base=ctx.main_branch,
        head=ctx.integration_branch,
        draft=True,
    )

    ctx.pr_number = int(pr_info["number"])
    ctx.pr_url = str(pr_info["url"])
    return ctx


def _format_batch_context(ctx: BatchReviewContext) -> str:
    """Format batch context as markdown for the AI prompt."""
    lines: list[str] = []
    lines.append(f"## Tracking issue: #{ctx.tracking_issue}")
    lines.append(f"**Main branch:** {ctx.main_branch}")
    if ctx.integration_branch:
        lines.append(f"**Integration branch:** {ctx.integration_branch}")
    lines.append("")

    for issue in ctx.issues:
        lines.append(f"### Issue #{issue.issue_number}: {issue.issue_title}")
        lines.append(f"- **Branch:** {issue.branch_name or '(no branch)'}")
        if issue.pr_url:
            lines.append(f"- **PR:** {issue.pr_url}")
        lines.append(f"- **Status:** {issue.status or 'unknown'}")
        if issue.merged:
            lines.append("- **Merge:** successfully merged into integration branch")
        elif issue.conflict:
            lines.append("- **Merge:** CONFLICT — could not merge into integration branch")
        else:
            lines.append("- **Merge:** skipped (no branch available)")

        if issue.diff_stat:
            lines.append(f"\n```\n{issue.diff_stat.strip()}\n```")
        lines.append("")

    return "\n".join(lines)


def run_coherence_review(
    ctx: BatchReviewContext,
    *,
    ai_tool: str | None = None,
    model: str | None = None,
    mode: str | None = None,
    effort: str | None = None,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    effort_explicit: bool = False,
) -> DelegationResult:
    """Run AI coherence review on the batch context.

    Posts findings as a comment on the review PR if one exists.
    """
    config, cmd_config = _load_review_config("review_batch")
    skip = _check_review_enabled("review_batch", cmd_config)
    if skip is not None:
        return skip

    template = load_prompt_template("review-batch.md")
    batch_context_md = _format_batch_context(ctx)
    prompt = template.replace("{batch_context}", batch_context_md)

    result = _run_review_delegation(
        prompt,
        "review_batch",
        config=config,
        cmd_config=cmd_config,
        ai_tool=ai_tool,
        model=model,
        mode=mode,
        effort=effort,
        ai_explicit=ai_explicit,
        model_explicit=model_explicit,
        effort_explicit=effort_explicit,
    )

    # Post findings to PR as a comment
    if result.success and not result.skipped and ctx.pr_number:
        try:
            repo_root = git_repo.get_repo_root(Path.cwd())
            git_pr.comment_on_pr(repo_root, ctx.pr_number, result.feedback)
            console.info(f"Review posted as comment on PR #{ctx.pr_number}")
        except Exception:
            logger.debug("batch_review.comment_failed", exc_info=True)
            console.warn("Could not post review as PR comment.")

    return result


def review_batch(
    tracking_issue_id: str,
    *,
    ai_tool: str | None = None,
    model: str | None = None,
    mode: str | None = None,
    effort: str | None = None,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    effort_explicit: bool = False,
    project_root: Path | None = None,
) -> DelegationResult:
    """Main entry point — run full batch coherence review pipeline.

    1. Gather context from tracking issue and child issues
    2. Create integration branch merging all batch branches
    3. Open draft PR for the combined diff
    4. Run AI coherence review and post findings
    """
    cwd = project_root or Path.cwd()

    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error("Not inside a git repository.")
        return DelegationResult(
            success=False,
            feedback="Not inside a git repository.",
            mode=DelegationMode.PROMPT,
        )

    console.rule(f"Batch coherence review — tracking #{tracking_issue_id}")

    # Step 1: Gather context
    console.step("Gathering batch context...")
    ctx = gather_batch_context(tracking_issue_id, repo_root=repo_root)

    if not ctx.issues:
        console.warn("No child issues found. Nothing to review.")
        return DelegationResult(
            success=True,
            feedback="No child issues found in tracking issue.",
            mode=DelegationMode.PROMPT,
            skipped=True,
        )

    branches_available = [i for i in ctx.issues if i.branch_name]
    if not branches_available:
        console.warn("No branches found for any child issue.")
        return DelegationResult(
            success=True,
            feedback="No branches available for batch review.",
            mode=DelegationMode.PROMPT,
            skipped=True,
        )

    # Step 2: Create integration branch
    console.step("Creating integration branch...")
    original_branch = git_repo.get_current_branch(repo_root)
    try:
        ctx = create_integration_branch(repo_root, ctx)

        merged_count = sum(1 for i in ctx.issues if i.merged)
        conflict_count = sum(1 for i in ctx.issues if i.conflict)
        console.info(f"Integration branch: {merged_count} merged, {conflict_count} conflicts")

        # Step 3: Create review PR
        console.step("Creating review PR...")
        ctx = create_review_pr(repo_root, ctx)
        if ctx.pr_url:
            console.info(f"Draft PR: {ctx.pr_url}")

        # Step 4: Run AI coherence review
        console.step("Running AI coherence review...")
        result = run_coherence_review(
            ctx,
            ai_tool=ai_tool,
            model=model,
            mode=mode,
            effort=effort,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort_explicit=effort_explicit,
        )

        return result

    finally:
        # Return to the original branch
        try:
            git_repo.checkout(repo_root, original_branch)
        except GitError:
            logger.debug("batch_review.checkout_restore_failed", exc_info=True)
