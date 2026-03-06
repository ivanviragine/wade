"""Smart start service — detects PR state and routes to the right command.

When `wade <ISSUE_ID>` is invoked, this service checks if an open PR already
exists for the issue and presents a contextual menu:
- No PR → implement-task (normal flow)
- Open PR → "Continue working" / "Address reviews" / "Merge PR"
"""

from __future__ import annotations

from pathlib import Path

import structlog

from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git.repo import GitError
from wade.providers.registry import get_provider
from wade.services.work_service import _merge_pr
from wade.ui.console import console

logger = structlog.get_logger()


def smart_start(
    target: str,
    ai_tool: str | None = None,
    model: str | None = None,
    project_root: Path | None = None,
    detach: bool = False,
    cd_only: bool = False,
    *,
    ai_explicit: bool = False,
    model_explicit: bool = False,
) -> bool:
    """Detect PR state for an issue and route to the right command.

    If no open PR exists, falls through to implement-task.
    If an open PR exists, offers a contextual menu.

    Returns:
        True on success, False on failure.
    """
    config = load_config(project_root)
    provider = get_provider(config)

    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        # Not in a git repo — fall through to implement-task which will
        # give a proper error.
        return _run_implement_task(
            target,
            ai_tool,
            model,
            project_root,
            detach,
            cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
        )

    # Read the issue
    issue_number = target.lstrip("#")
    try:
        task = provider.read_task(issue_number)
    except Exception:
        # Can't read issue — fall through to implement-task.
        return _run_implement_task(
            target,
            ai_tool,
            model,
            project_root,
            detach,
            cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
        )

    # Build the expected branch name
    branch_name = git_branch.make_branch_name(
        config.project.branch_prefix,
        int(task.id),
        task.title,
    )

    # Check for existing PR
    pr_info = git_pr.get_pr_for_branch(repo_root, branch_name)
    if not pr_info:
        # No PR → normal implement-task flow
        return _run_implement_task(
            target,
            ai_tool,
            model,
            project_root,
            detach,
            cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
        )

    pr_number = pr_info.get("number") or pr_info.get("pr_number")
    if not pr_number:
        # Can't determine PR number — fall through to implement-task.
        return _run_implement_task(
            target,
            ai_tool,
            model,
            project_root,
            detach,
            cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
        )
    pr_number_int = int(pr_number)
    pr_state = str(pr_info.get("state", "")).upper()

    if pr_state == "MERGED":
        console.info(f"PR #{pr_number_int} is already merged.")
        return True

    # cd_only: skip menu, just set up worktree and print path
    if cd_only:
        return _run_implement_task(
            target,
            ai_tool,
            model,
            project_root,
            detach,
            cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
        )

    # Open PR exists — present contextual menu
    from wade.git import worktree as git_worktree
    from wade.ui import prompts

    console.kv("Issue", console.issue_ref(task.id, task.title))
    console.kv("PR", f"#{pr_number_int} ({pr_state.lower()})")

    choice = prompts.select(
        f"PR #{pr_number_int} exists — what do you want to do?",
        ["Continue working", "Address reviews", "Merge PR"],
    )

    if choice == 0:  # Continue working
        return _run_implement_task(
            target,
            ai_tool,
            model,
            project_root,
            detach,
            cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
        )

    if choice == 1:  # Address reviews
        return _run_address_reviews(
            target,
            ai_tool,
            model,
            project_root,
            detach,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
        )

    # choice == 2: Merge PR
    worktree_path = next(
        (
            Path(wt["path"])
            for wt in git_worktree.list_worktrees(repo_root)
            if wt.get("branch") == branch_name
        ),
        None,
    )
    _merge_pr(
        repo_root,
        branch_name,
        pr_number_int,
        task.id,
        worktree_path,
        provider,
    )
    return True


def _run_implement_task(
    target: str,
    ai_tool: str | None,
    model: str | None,
    project_root: Path | None,
    detach: bool,
    cd_only: bool,
    *,
    ai_explicit: bool = False,
    model_explicit: bool = False,
) -> bool:
    """Delegate to the implement-task service."""
    from wade.services.work_service import start as do_start

    return do_start(
        target=target,
        ai_tool=ai_tool,
        model=model,
        project_root=project_root,
        detach=detach,
        cd_only=cd_only,
        ai_explicit=ai_explicit,
        model_explicit=model_explicit,
    )


def _run_address_reviews(
    target: str,
    ai_tool: str | None,
    model: str | None,
    project_root: Path | None,
    detach: bool,
    *,
    ai_explicit: bool = False,
    model_explicit: bool = False,
) -> bool:
    """Delegate to the address-reviews service."""
    from wade.services.review_service import start as do_start

    return do_start(
        target=target,
        ai_tool=ai_tool,
        model=model,
        project_root=project_root,
        detach=detach,
        ai_explicit=ai_explicit,
        model_explicit=model_explicit,
    )
