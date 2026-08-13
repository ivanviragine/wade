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

    "Real work" means commits a retarget cannot rewrite without discarding, so the old
    base's commits would leak into the new base's diff. This is the *guard* signal: an
    in-flight branch must not be retargeted silently. It is deliberately **not** about a
    checked-out worktree — ``wade implement --cd`` cuts a scaffold worktree with no
    commits, which is not divergent work.

    A bare commit count cannot tell WADE's *empty* scaffold commit apart from a real
    one-commit implementation, so being exactly one commit ahead is **not** proof of a
    scaffold: a user may have amended the scaffold with their work, squashed the branch
    to a single commit, or opened the PR outside WADE with one real commit. For the
    single-commit case we inspect the commit itself — only an *empty* tip (no tree change
    vs its parent, the signature of :func:`create_scaffold_commit`) is a rerootable
    scaffold; anything that touched the tree is real work a reroot would discard
    (#376 review). More than one commit ahead is always real work.

    The head may live only on origin — a fresh clone whose PR branch was never checked
    out locally. Resolve it to a local ref or ``origin/<branch>`` before measuring, so a
    scaffold-only *remote* PR is classified as scaffold work rather than misclassified as
    real work by a failed local ``rev-list`` (#376 review). A head that resolves nowhere,
    a count that cannot be computed, or an emptiness check that cannot be resolved all
    fail **closed** (return ``True``) so an indeterminate branch is never silently
    retargeted (and never hard-reset).
    """
    if not base:
        return False
    try:
        head_ref = git_branch.resolve_start_point(repo_root, branch_name)
        if head_ref is None:
            logger.debug("draft_pr.real_work_head_unresolved", branch=branch_name)
            return True
        start = git_branch.resolve_start_point(repo_root, base) or base
        ahead = git_branch.commits_ahead(repo_root, head_ref, start)
        if ahead == 0:
            return False
        if ahead > 1:
            return True
        # Exactly one commit ahead: rerootable only if that commit is WADE's empty
        # scaffold. A non-empty tip is real work; an indeterminate result fails closed.
        empty = git_branch.tip_commit_is_empty(repo_root, head_ref)
        if empty is None:
            logger.debug("draft_pr.real_work_tip_emptiness_unknown", branch=branch_name)
            return True
        return not empty
    except (GitError, OSError, ValueError):
        logger.debug("draft_pr.real_work_commits_check_failed", exc_info=True)
        return True


def _find_checked_out_worktree(repo_root: Path, branch_name: str) -> tuple[bool, Path | None]:
    """Locate the worktree, if any, that has *branch_name* checked out.

    A checked-out branch cannot be moved with ``git branch -f``; it must be re-rooted in
    place (a hard reset inside its worktree), so the reroot needs the worktree *path*, not
    just a yes/no.

    Returns a ``(checked_out, path)`` pair:

    - ``(True, <path>)`` — checked out; ``<path>`` is where to reset it.
    - ``(False, None)`` — definitively not checked out anywhere.
    - ``(True, None)`` — the worktree list could not be read. Fails **closed**: treated as
      checked-out-with-unknown-path so the caller refuses the retarget rather than
      attempting a ``git branch -f`` that would either fail or silently mis-handle a branch
      that really is checked out.
    """
    from wade.git import worktree as git_worktree

    try:
        for wt in git_worktree.list_worktrees(repo_root):
            if wt.get("branch") == branch_name:
                return True, Path(wt["path"])
        return False, None
    except Exception:
        logger.debug("draft_pr.worktree_check_failed", exc_info=True)
        return True, None


def _resolve_head_sha(repo_root: Path, ref: str) -> str | None:
    """Resolve *ref* to a commit SHA, or ``None`` if it cannot be resolved."""
    try:
        return git_repo.rev_parse(repo_root, ref)
    except GitError:
        return None


def _restore_scaffold_head(
    repo_root: Path, branch_name: str, target_sha: str, pr_number: int
) -> None:
    """Roll *branch_name* (local ref + remote) back to *target_sha* after a failed retarget.

    Called only when :func:`reroot_scaffold_branch_for_retarget` already force-pushed a
    rewritten head but the subsequent PR-base edit failed — which would otherwise leave
    the remote branch rerooted on the new base while the PR still targets the old one
    (divergent ancestry that can leak new-base commits into the old-base PR and makes a
    non-interactive retry misclassify the rerooted branch as real work). Restoring the
    pre-reroot head keeps the two consistent so a retry starts from a clean, correctly
    classified scaffold (#376 review). Best-effort: reports loudly if it cannot, since the
    divergence then needs manual cleanup.
    """
    checked_out, worktree_path = _find_checked_out_worktree(repo_root, branch_name)
    try:
        if checked_out and worktree_path is not None:
            git_branch.reset_worktree_hard(worktree_path, target_sha)
        else:
            git_branch.reset_branch(repo_root, branch_name, target_sha)
        git_repo.push_branch(repo_root, branch_name, force=True)
    except GitError as e:
        console.error(
            f"Could not restore branch '{branch_name}' after PR #{pr_number}'s base edit "
            f"failed: {e}. The remote branch may be rerooted while the PR still targets the "
            "old base — reset the branch manually before retrying."
        )
        return
    console.detail(f"Restored '{branch_name}' to its pre-retarget state after the failed PR edit")


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
    beyond *old_base* — rebuild it rooted on *new_base* and force-push; this is
    loss-free whether or not it is checked out (an in-place hard reset handles the
    checked-out case).

    A real-work branch is left untouched (rewriting in-flight history is
    destructive); the caller confirms/aborts that retarget separately (the plan flow
    via :func:`_base_retarget_is_safe`, ``start()`` via :func:`_branch_has_real_work`)
    so it is never applied silently. A scaffold-only branch is rebuilt on the new base
    and force-pushed — via ``git branch -f`` when it is not checked out, or a hard reset
    inside its worktree when it is (``git branch -f`` cannot move a checked-out branch).
    It **aborts** rather than retarget onto a stale branch when the reroot cannot be done
    loss-free: an unresolvable *old* base (cannot prove the branch is scaffold-only — a
    reset might discard real commits), a checked-out worktree carrying uncommitted tracked
    changes (a hard reset would discard them) or one whose path can't be resolved, or an
    unresolvable *new* base / failed git op.

    Returns:
        ``True`` — safe to proceed with the PR-base edit: the branch was recreated,
        or deliberately left as-is because it carries real work the caller guards.
        ``False`` — the branch could not be safely rerooted; the caller must abort
        rather than retarget onto a stale branch.
    """
    # Without a resolvable old base we can neither prove the branch is scaffold-only
    # (a reset could discard real commits) nor safely retarget it as-is (old-base
    # commits would leak into the new diff). Abort so the caller surfaces it (#376
    # review).
    if not old_base:
        console.error(
            f"Cannot retarget the PR for '{branch_name}': its current base is unknown, "
            "so wade cannot prove the branch is a rerootable scaffold. Retarget it "
            "manually once the base is resolvable."
        )
        return False

    # Real commits past the scaffold → leave the branch as-is; the caller confirms or
    # aborts this retarget separately, so a real-work retarget is never silent.
    if _branch_has_real_work(repo_root, branch_name, old_base):
        return True

    # Scaffold-only: rebuild it rooted on the new base and force-push — loss-free. A
    # checked-out branch (the `wade implement --cd` case) is re-rooted *in place* (a hard
    # reset inside its worktree) since `git branch -f` refuses a checked-out branch;
    # otherwise the retarget would leave old-base commits leaking into the new diff.
    checked_out, worktree_path = _find_checked_out_worktree(repo_root, branch_name)
    new_start = _resolve_base_start_point(repo_root, new_base)
    if new_start is None:
        return False  # resolver already reported why
    try:
        if checked_out:
            if worktree_path is None:
                # Checked out somewhere we can't resolve (worktree list unreadable) → we
                # can't reset the right worktree. Abort and require cleanup.
                console.error(
                    f"Branch '{branch_name}' appears checked out but its worktree could "
                    "not be resolved, so it cannot be re-rooted before retargeting. Remove "
                    "the worktree (`wade cleanup` or `git worktree remove`) and retry."
                )
                return False
            if git_repo.has_tracked_changes(worktree_path):
                # A hard reset would discard these edits — refuse rather than lose work.
                console.error(
                    f"Branch '{branch_name}' is checked out with uncommitted changes in "
                    f"{worktree_path}, so it cannot be re-rooted onto '{new_base}' before "
                    "retargeting. Commit or stash them (or remove the worktree) and retry."
                )
                return False
            git_branch.reset_worktree_hard(worktree_path, new_start)
        else:
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
            #
            # The reroot force-pushes the rewritten head *before* the base edit, so
            # capture the pre-reroot head first: if the edit then fails, the remote
            # branch (now on the new base) and the PR (still on the old base) would
            # diverge. Roll the head back to keep them consistent (#376 review).
            head_ref = git_branch.resolve_start_point(repo_root, branch_name)
            pre_reroot_sha = _resolve_head_sha(repo_root, head_ref) if head_ref else None
            if not reroot_scaffold_branch_for_retarget(
                repo_root, branch_name, existing.base_ref_name, base_branch, issue_number
            ):
                return None
            if not git_pr.update_pr_base(repo_root, existing.number, base_branch):
                console.error(f"Failed to retarget PR #{existing.number} to {base_branch}.")
                # Only roll back when the reroot actually rewrote the head (SHA changed).
                # A real-work branch is left untouched by the reroot, so a restore would
                # be a needless hard reset that could discard uncommitted work (#376).
                post_reroot_sha = _resolve_head_sha(repo_root, branch_name)
                if pre_reroot_sha and post_reroot_sha and pre_reroot_sha != post_reroot_sha:
                    _restore_scaffold_head(repo_root, branch_name, pre_reroot_sha, existing.number)
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
