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
    "_branch_has_real_work",
    "_build_implementation_issue_context_header",
    "bootstrap_draft_pr",
    "build_implementation_prompt",
    "extract_plan_from_pr_body",
    "reroot_scaffold_branch_for_retarget",
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


def _resolve_base_start_point(repo_root: Path, base: str) -> str | None:
    """Resolve *base* to a local commit-ish usable as a branch start point.

    A plan-declared base may exist only as a remote tracking ref. If neither a
    local branch nor ``origin/<base>`` resolves, fetch the specific ref (an
    explicit refspec so it works even in a single-branch clone whose default fetch
    refspec would skip it) and re-resolve before giving up. Reports an actionable
    error and returns ``None`` when the base is genuinely absent or the fetch
    fails (#376).
    """
    start_point = git_branch.resolve_start_point(repo_root, base)
    if start_point is None and git_repo.has_remote(repo_root):
        # A local cache miss is NOT proof the branch is absent on origin — a teammate
        # may have just pushed it and our remote-tracking refs are stale.
        try:
            git_repo.fetch_ref(repo_root, "origin", f"{base}:refs/remotes/origin/{base}")
        except GitError as e:
            # The fetch itself failed. A missing ref on origin AND a network/auth
            # error both land here, so don't claim the branch is simply absent —
            # surface the underlying git error so the real cause (unreachable remote
            # vs. truly-missing ref) is visible (#376 review).
            console.error(
                f"Could not fetch base branch '{base}' from origin: {e}. "
                "Verify the branch exists on origin and the remote is reachable."
            )
            return None
        start_point = git_branch.resolve_start_point(repo_root, base)
    if start_point is None:
        console.error(
            f"Base branch '{base}' does not exist locally or on origin. "
            "Create and push it before implementation, or choose an existing base."
        )
        return None
    return start_point


def _branch_has_real_work(repo_root: Path, branch_name: str, base: str | None) -> bool:
    """Return ``True`` when *branch_name* carries real work beyond its scaffold commit.

    "Real work" means the branch is more than one commit ahead of *base* (i.e. past
    the bare scaffold commit) — commits a retarget cannot rewrite without discarding,
    so the old base's commits would leak into the new base's diff. This is the
    *guard* signal: an in-flight branch must not be retargeted silently. It is
    deliberately **not** about a checked-out worktree — ``wade implement --cd`` cuts
    a scaffold worktree with no commits, which is not divergent work. A count that
    cannot be computed fails **closed** (returns ``True``) so an indeterminate branch
    is never silently retargeted (#376).
    """
    if not base:
        return False
    try:
        start = git_branch.resolve_start_point(repo_root, base) or base
        return git_branch.commits_ahead(repo_root, branch_name, start) > 1
    except (GitError, OSError, ValueError):
        logger.debug("draft_pr.real_work_commits_check_failed", exc_info=True)
        return True


def _branch_is_checked_out(repo_root: Path, branch_name: str) -> bool:
    """Return ``True`` when *branch_name* is checked out in some worktree.

    A checked-out branch cannot be moved with ``git branch -f`` — the reroot below
    would fail — so this gates the *recreate*, independently of whether the branch
    holds real work. Fails **closed** (returns ``True`` → do not rewrite) when the
    worktree list cannot be read.
    """
    from wade.git import worktree as git_worktree

    try:
        return any(wt.get("branch") == branch_name for wt in git_worktree.list_worktrees(repo_root))
    except Exception:
        logger.debug("draft_pr.worktree_check_failed", exc_info=True)
        return True


def reroot_scaffold_branch_for_retarget(
    repo_root: Path,
    branch_name: str,
    old_base: str | None,
    new_base: str,
    issue_number: str,
) -> bool:
    """Re-root a scaffold-only branch on *new_base* before its PR is retargeted.

    Editing a PR's base does not rewrite its head branch's ancestry: a scaffold
    branch cut from *old_base* keeps that base's commits, which would then merge
    into *new_base* once the PR is retargeted (the later startup catchup merge
    cannot remove them). When the branch is scaffold-only — only its scaffold commit
    beyond *old_base* and not checked out in any worktree — rebuild it rooted on
    *new_base* and force-push; this is loss-free.

    A branch is left untouched when it carries real work (rewriting in-flight history
    is destructive) or is checked out in a worktree (``git branch -f`` would fail).
    The caller must guard/confirm the resulting retarget of a real-work branch
    separately (the plan flow via :func:`_base_retarget_is_safe`, ``start()`` via
    :func:`_branch_has_real_work`) so it is never applied silently.

    Returns:
        ``True`` — safe to proceed with the PR-base edit: the branch was recreated,
        or deliberately left as-is. ``False`` — a scaffold-only branch needed
        recreating but the new base could not be resolved or the git operation
        failed; the caller must abort rather than retarget onto a stale branch.
    """
    # Real commits past the scaffold, or a checked-out worktree we cannot ``git
    # branch -f`` over → leave the branch as-is; only a scaffold-only, un-checked-out
    # branch is safe to rebuild.
    if _branch_has_real_work(repo_root, branch_name, old_base) or _branch_is_checked_out(
        repo_root, branch_name
    ):
        return True

    # Scaffold-only: rebuild the branch rooted on the new base and force-push it.
    new_start = _resolve_base_start_point(repo_root, new_base)
    if new_start is None:
        return False  # resolver already reported why
    try:
        git_branch.reset_branch(repo_root, branch_name, new_start)
        git_branch.create_scaffold_commit(
            repo_root, branch_name, f"chore: scaffold branch for #{issue_number}"
        )
        git_repo.push_branch(repo_root, branch_name, force=True)
    except GitError as e:
        console.error(f"Could not re-root scaffold branch '{branch_name}' on '{new_base}': {e}.")
        return False
    console.detail(f"Re-rooted scaffold branch on '{new_base}' before retarget")
    return True


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

    # Reuse only an OPEN PR for this branch. A merged/closed PR must not be
    # reused (its branch work is done); fall through and create a fresh one.
    lookup = git_pr.get_pr_for_branch(repo_root, branch_name)
    if lookup.lookup_failed:
        # A failed lookup is NOT "no PR" — creating one now risks a duplicate PR
        # (or GitHub's "a pull request already exists" error) for a branch that
        # may already have an open PR.
        console.error(
            f"Could not look up the PR for branch {branch_name} — "
            "transient gh error; try again shortly."
        )
        return None
    if lookup.is_open and lookup.pr is not None:
        existing = lookup.pr
        # If a base was requested and differs from the PR's current base, retarget
        # it (stacked chain, or a plan-declared base). Skip the gh call when the
        # base already matches, and report success so the change is visible.
        if base_branch and base_branch != existing.base_ref_name:
            # Editing the PR base alone does NOT rewrite the head branch's ancestry:
            # a scaffold branch cut from the old base still carries that base's
            # commits, which would then merge into the new base (the later startup
            # catchup merge cannot remove them). Re-root a scaffold-only branch on
            # the new base first — loss-free — before retargeting (#376 review).
            if not reroot_scaffold_branch_for_retarget(
                repo_root, branch_name, existing.base_ref_name, base_branch, issue_number
            ):
                return None
            if not git_pr.update_pr_base(repo_root, existing.number, base_branch):
                console.error(f"Failed to retarget PR #{existing.number} to {base_branch}.")
                return None
            console.detail(f"Retargeted PR #{existing.number} base to {base_branch}")
        logger.info(
            "bootstrap_draft_pr.existing",
            branch=branch_name,
            pr=existing.number,
        )
        return {"number": existing.number, "url": existing.url}

    # Resolve the effective base for branch creation and PR target
    main_branch = config.project.main_branch or git_repo.detect_main_branch(repo_root)
    effective_base = base_branch or main_branch

    # Resolve a local commit-ish to cut from — a plan-declared base may exist only
    # as a remote tracking ref. If neither a local branch nor origin/<base> exists,
    # fail with an actionable message rather than a raw git error from create_branch.
    start_point = _resolve_base_start_point(repo_root, effective_base)
    if start_point is None:
        return None

    if not git_branch.branch_exists(repo_root, branch_name):
        git_branch.create_branch(repo_root, branch_name, start_point)
        logger.info("bootstrap_draft_pr.branch_created", branch=branch_name)

    # Scaffold commit so GitHub accepts the draft PR (needs ≥1 commit ahead of base)
    if git_branch.commits_ahead(repo_root, branch_name, start_point) == 0:
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
        if pr_info is None:
            console.error("Failed to create draft PR — could not determine the new PR number.")
            return None
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
    task: Task,
    ai_tool: str | None = None,
    has_plan: bool = False,
    stale_warning: str | None = None,
) -> str:
    """Build the initial prompt for an implementation session.

    When *has_plan* is False (no plan content in the draft PR), the issue
    description is prepended inline so the AI has it without relying on
    @PLAN.md.  When a plan already exists, PLAN.md carries the full context
    and the inline header is skipped to avoid duplication.

    When *stale_warning* is provided (startup catchup could not advance the branch
    onto its base — #407), it is prepended to the very top so the first turn sees the
    staleness banner before anything else.
    """
    from wade.skills.installer import get_templates_dir

    template_path = get_templates_dir() / "prompts" / "implement-context.md"
    if not template_path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")
    template = template_path.read_text(encoding="utf-8")
    prompt = template.format(issue_number=task.id, issue_title=task.title)
    if task.body and not has_plan:
        prompt = _build_implementation_issue_context_header(task) + prompt
    if stale_warning:
        prompt = stale_warning.rstrip() + "\n\n" + prompt
    return prompt
