"""Draft-PR bootstrap and implementation-prompt building.

Shared by the plan and implementation flows. Reads prompt templates directly
from ``templates/prompts/`` (not symlinked) — see knowledge entry b61e247e.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git.repo import GitError
from wade.models.config import ProjectConfig
from wade.models.task import Task
from wade.ui.console import console

logger = structlog.get_logger()

__all__ = [
    "PLAN_MARKER_END",
    "PLAN_MARKER_START",
    "_build_implementation_issue_context_header",
    "bootstrap_draft_pr",
    "build_implementation_prompt",
    "extract_plan_from_pr_body",
]

PLAN_MARKER_START = "<!-- wade:plan:start -->"
PLAN_MARKER_END = "<!-- wade:plan:end -->"


def _build_draft_pr_body(plan_body: str, issue_number: str) -> str:
    """Format draft PR body with plan content in markers."""
    lines = [
        f"Implements #{issue_number}",
        "",
        PLAN_MARKER_START,
        "",
        plan_body,
        "",
        PLAN_MARKER_END,
    ]
    return "\n".join(lines)


def extract_plan_from_pr_body(pr_body: str) -> str | None:
    """Extract plan content from between plan markers in a PR body.

    Returns the content between markers, or None if markers are not found.
    """
    start_idx = pr_body.find(PLAN_MARKER_START)
    end_idx = pr_body.find(PLAN_MARKER_END)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return None
    content = pr_body[start_idx + len(PLAN_MARKER_START) : end_idx]
    return content.strip()


def bootstrap_draft_pr(
    issue_number: str,
    issue_title: str,
    plan_body: str,
    config: ProjectConfig,
    repo_root: Path,
    base_branch: str | None = None,
) -> dict[str, str | int] | None:
    """Create branch + push + draft PR for an issue.

    Reusable by both plan and implementation flows. Idempotent — if the branch and
    PR already exist, returns the existing PR info.

    Args:
        issue_number: GitHub issue number.
        issue_title: Issue title (used for branch name and PR title).
        plan_body: Plan content to embed in the draft PR body.
        config: Project configuration.
        repo_root: Repository root directory.
        base_branch: When set, branch from this instead of main and target
            the PR at it (stacked PR for chain execution).

    Returns:
        Dict with "number" (int) and "url" (str) keys, or None on failure.
    """
    # Generate deterministic branch name
    branch_name = git_branch.make_branch_name(
        config.project.branch_prefix,
        int(issue_number),
        issue_title,
    )

    # Check if PR already exists for this branch
    existing_pr = git_pr.get_pr_for_branch(repo_root, branch_name)
    if existing_pr:
        # If a stacked base was requested but the existing PR targets main,
        # re-target it to the parent branch.
        if base_branch:
            pr_number = int(existing_pr["number"])
            if not git_pr.update_pr_base(repo_root, pr_number, base_branch):
                console.error(f"Failed to retarget existing PR #{pr_number} to {base_branch}.")
                return None
        logger.info(
            "bootstrap_draft_pr.existing",
            branch=branch_name,
            pr=existing_pr["number"],
        )
        return existing_pr

    # Resolve the effective base for branch creation and PR target
    main_branch = config.project.main_branch or git_repo.detect_main_branch(repo_root)
    effective_base = base_branch or main_branch

    if not git_branch.branch_exists(repo_root, branch_name):
        git_branch.create_branch(repo_root, branch_name, effective_base)
        logger.info("bootstrap_draft_pr.branch_created", branch=branch_name)

    # Scaffold commit so GitHub accepts the draft PR (needs ≥1 commit ahead of base)
    if git_branch.commits_ahead(repo_root, branch_name, effective_base) == 0:
        git_branch.create_scaffold_commit(
            repo_root,
            branch_name,
            f"chore: scaffold branch for #{issue_number}",
        )

    # Push branch to origin
    try:
        git_repo.push_branch(repo_root, branch_name, set_upstream=True)
    except GitError as e:
        console.error(f"Failed to push branch: {e}")
        return None

    # Build draft PR body with plan markers
    body = _build_draft_pr_body(plan_body, issue_number)

    # Create draft PR targeting the effective base (parent branch for stacked PRs)
    try:
        pr_info = git_pr.create_pr(
            repo_root=repo_root,
            title=issue_title,
            body=body,
            base=effective_base,
            head=branch_name,
            draft=True,
        )
        logger.info(
            "bootstrap_draft_pr.created",
            branch=branch_name,
            pr=pr_info.get("number"),
        )
        return pr_info
    except Exception as e:
        console.error(f"Failed to create draft PR: {e}")
        return None


def _build_implementation_issue_context_header(task: Task) -> str:
    """Build an issue description block to prepend to the implementation prompt."""
    lines = [
        "## Issue Description",
        "",
        (task.body or "").strip(),
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def build_implementation_prompt(
    task: Task, ai_tool: str | None = None, has_plan: bool = False
) -> str:
    """Build the initial prompt for an implementation session.

    When *has_plan* is False (no plan content in the draft PR), the issue
    description is prepended inline so the AI has it without relying on
    @PLAN.md.  When a plan already exists, PLAN.md carries the full context
    and the inline header is skipped to avoid duplication.
    """
    from wade.skills.installer import get_templates_dir

    template_path = get_templates_dir() / "prompts" / "implement-context.md"
    if not template_path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")
    template = template_path.read_text(encoding="utf-8")
    prompt = template.format(issue_number=task.id, issue_title=task.title)
    if task.body and not has_plan:
        prompt = _build_implementation_issue_context_header(task) + prompt
    return prompt
