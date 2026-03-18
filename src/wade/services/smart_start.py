"""Smart start service — detects PR state and routes to the right command.

When `wade <ISSUE_ID>` is invoked, this service checks if an open PR already
exists for the issue and presents a contextual menu:
- No PR → implement (normal flow)
- Open PR → "Continue working" / "Review PR comments" / "Merge PR"
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from wade.ai_tools.base import AbstractAITool
from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git.repo import GitError
from wade.models.ai import AIToolID
from wade.models.session import SessionRecord
from wade.models.task import (
    has_checklist_items,
    is_tracking_issue,
    parse_all_issue_refs,
    parse_tracking_child_ids,
)
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services.implementation_service import _merge_pr
from wade.ui.console import console
from wade.utils.markdown import parse_sessions_from_body

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
    effort: str | None = None,
    effort_explicit: bool = False,
    yolo: bool | None = None,
) -> bool:
    """Detect PR state for an issue and route to the right command.

    If no open PR exists, falls through to implement.
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
        # Not in a git repo — fall through to implement which will
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
            effort=effort,
            effort_explicit=effort_explicit,
            yolo=yolo,
        )

    # Read the issue
    issue_number = target.lstrip("#")
    try:
        task = provider.read_task(issue_number)
    except Exception:
        # Can't read issue — fall through to implement.
        return _run_implement_task(
            target,
            ai_tool,
            model,
            project_root,
            detach,
            cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort=effort,
            effort_explicit=effort_explicit,
            yolo=yolo,
        )

    # Tracking issue detection — redirect to batch implementation
    if is_tracking_issue(task.title):
        from wade.ui import prompts

        # If the body uses checklist format, honour checked/unchecked semantics
        # (only unchecked = still to-do). Otherwise fall back to all plain #N refs
        # so tracking issues authored without a checklist still trigger batch mode.
        child_ids = (
            parse_tracking_child_ids(task.body)
            if has_checklist_items(task.body)
            else parse_all_issue_refs(task.body)
        )
        if child_ids:
            refs = ", ".join(f"#{cid}" for cid in child_ids)
            console.info(f"#{task.id} is a tracking issue for: {refs}")
            if prompts.confirm("Start batch implementation?", default=True):
                from wade.services.implementation_service import batch

                return batch(
                    issue_numbers=child_ids,
                    ai_tool=ai_tool,
                    model=model,
                    project_root=project_root,
                    ai_explicit=ai_explicit,
                    model_explicit=model_explicit,
                    effort=effort,
                    effort_explicit=effort_explicit,
                    yolo=yolo,
                )
            return False

    # Build the expected branch name
    branch_name = git_branch.make_branch_name(
        config.project.branch_prefix,
        int(task.id),
        task.title,
    )

    # Check for existing PR
    pr_info = git_pr.get_pr_for_branch(repo_root, branch_name)
    if not pr_info:
        # No PR → normal implement flow
        return _run_implement_task(
            target,
            ai_tool,
            model,
            project_root,
            detach,
            cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort=effort,
            effort_explicit=effort_explicit,
            yolo=yolo,
        )

    pr_number = pr_info.get("number") or pr_info.get("pr_number")
    if not pr_number:
        # Can't determine PR number — fall through to implement.
        return _run_implement_task(
            target,
            ai_tool,
            model,
            project_root,
            detach,
            cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort=effort,
            effort_explicit=effort_explicit,
            yolo=yolo,
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
            effort=effort,
            effort_explicit=effort_explicit,
            yolo=yolo,
        )

    # Open PR exists — present contextual menu
    from wade.git import worktree as git_worktree
    from wade.ui import prompts

    console.kv("Issue", console.issue_ref(task.id, task.title))
    console.kv("PR", f"#{pr_number_int} ({pr_state.lower()})")

    # Extract draft state and worktree presence
    is_draft = pr_info.get("isDraft", False)
    worktrees = git_worktree.list_worktrees(repo_root)
    has_worktree = any(wt.get("branch") == branch_name for wt in worktrees)

    # Build dynamic menu based on PR state and worktree
    menu_options: list[tuple[str, Callable[[], bool]]] = []

    if is_draft:
        # For draft PRs: show either "Start implementation" or "Continue working"
        if has_worktree:
            menu_options.append(
                (
                    "Continue working",
                    _run_continue_working_wrapper(
                        target,
                        ai_tool,
                        model,
                        project_root,
                        detach,
                        cd_only,
                        ai_explicit,
                        model_explicit,
                        repo_root,
                        pr_number_int,
                        effort=effort,
                        effort_explicit=effort_explicit,
                        yolo=yolo,
                    ),
                )
            )
        else:
            menu_options.append(
                (
                    "Start implementation",
                    _run_implement_task_wrapper(
                        target,
                        ai_tool,
                        model,
                        project_root,
                        detach,
                        cd_only,
                        ai_explicit,
                        model_explicit,
                        effort=effort,
                        effort_explicit=effort_explicit,
                        yolo=yolo,
                    ),
                )
            )
    else:
        # For ready PRs: show all three options
        menu_options.append(
            (
                "Continue working",
                _run_continue_working_wrapper(
                    target,
                    ai_tool,
                    model,
                    project_root,
                    detach,
                    cd_only,
                    ai_explicit,
                    model_explicit,
                    repo_root,
                    pr_number_int,
                    effort=effort,
                    effort_explicit=effort_explicit,
                    yolo=yolo,
                ),
            )
        )
        menu_options.append(
            (
                "Review PR comments",
                _run_review_pr_comments_wrapper(
                    target, ai_tool, model, project_root, detach, ai_explicit, model_explicit, yolo
                ),
            )
        )
        menu_options.append(
            (
                "Merge PR",
                _run_merge_pr_wrapper(
                    repo_root, branch_name, pr_number_int, task.id, provider, worktrees
                ),
            )
        )

    labels = [label for label, _ in menu_options]
    choice = prompts.select(
        f"PR #{pr_number_int} exists — what do you want to do?",
        labels,
    )

    # Dispatch the selected action
    return menu_options[choice][1]()


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
    effort: str | None = None,
    effort_explicit: bool = False,
    resume_session_id: str | None = None,
    resume_ai_tool: str | None = None,
    yolo: bool | None = None,
) -> bool:
    """Delegate to the implement service."""
    from wade.services.implementation_service import start as do_start

    result = do_start(
        target=target,
        ai_tool=ai_tool,
        model=model,
        project_root=project_root,
        detach=detach,
        cd_only=cd_only,
        ai_explicit=ai_explicit,
        model_explicit=model_explicit,
        effort=effort,
        effort_explicit=effort_explicit,
        resume_session_id=resume_session_id,
        resume_ai_tool=resume_ai_tool,
        yolo=yolo,
    )
    return result.success


def _run_review_pr_comments(
    target: str,
    ai_tool: str | None,
    model: str | None,
    project_root: Path | None,
    detach: bool,
    *,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    yolo: bool | None = None,
) -> bool:
    """Delegate to the review pr-comments service."""
    from wade.services.review_service import start as do_start

    return do_start(
        target=target,
        ai_tool=ai_tool,
        model=model,
        project_root=project_root,
        detach=detach,
        ai_explicit=ai_explicit,
        model_explicit=model_explicit,
        yolo=yolo,
    )


def _run_implement_task_wrapper(
    target: str,
    ai_tool: str | None,
    model: str | None,
    project_root: Path | None,
    detach: bool,
    cd_only: bool,
    ai_explicit: bool,
    model_explicit: bool,
    effort: str | None = None,
    effort_explicit: bool = False,
    yolo: bool | None = None,
) -> Callable[[], bool]:
    """Return a callable that runs _run_implement_task with captured arguments."""

    def _impl() -> bool:
        return _run_implement_task(
            target=target,
            ai_tool=ai_tool,
            model=model,
            project_root=project_root,
            detach=detach,
            cd_only=cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort=effort,
            effort_explicit=effort_explicit,
            yolo=yolo,
        )

    return _impl


def _run_review_pr_comments_wrapper(
    target: str,
    ai_tool: str | None,
    model: str | None,
    project_root: Path | None,
    detach: bool,
    ai_explicit: bool,
    model_explicit: bool,
    yolo: bool | None = None,
) -> Callable[[], bool]:
    """Return a callable that runs _run_review_pr_comments with captured arguments."""

    def _impl() -> bool:
        return _run_review_pr_comments(
            target=target,
            ai_tool=ai_tool,
            model=model,
            project_root=project_root,
            detach=detach,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            yolo=yolo,
        )

    return _impl


def _run_merge_pr_wrapper(
    repo_root: Path,
    branch_name: str,
    pr_number: int,
    task_id: str,
    provider: AbstractTaskProvider,
    worktrees: list[Any],
) -> Callable[[], bool]:
    """Return a callable that runs the merge PR logic with captured arguments."""

    def _impl() -> bool:
        worktree_path = next(
            (Path(wt["path"]) for wt in worktrees if wt.get("branch") == branch_name),
            None,
        )
        if worktree_path is None:
            console.warn(
                f"No local worktree found for branch '{branch_name}' — "
                "local cleanup will be skipped after merge."
            )
        _merge_pr(
            repo_root,
            branch_name,
            pr_number,
            task_id,
            worktree_path,
            provider,
        )
        return True

    return _impl


# ---------------------------------------------------------------------------
# Resume session helpers
# ---------------------------------------------------------------------------


def _get_latest_resumable_session(repo_root: Path, pr_number: int) -> SessionRecord | None:
    """Find the most recent session from the PR body whose tool supports resume.

    Returns a ``SessionRecord`` or ``None``.
    """
    pr_body = git_pr.get_pr_body(repo_root, pr_number)
    if not pr_body:
        return None

    sessions = parse_sessions_from_body(pr_body)
    if not sessions:
        return None

    # Iterate in reverse (latest first) — find first tool with resume support
    for session in reversed(sessions):
        tool_id_str = session["ai_tool"]
        try:
            adapter = AbstractAITool.get(AIToolID(tool_id_str))
            if adapter.capabilities().supports_resume:
                return SessionRecord(**session)
        except (ValueError, KeyError):
            continue

    return None


def _run_continue_working(
    target: str,
    ai_tool: str | None,
    model: str | None,
    project_root: Path | None,
    detach: bool,
    cd_only: bool,
    ai_explicit: bool,
    model_explicit: bool,
    repo_root: Path,
    pr_number: int,
    effort: str | None = None,
    effort_explicit: bool = False,
    yolo: bool | None = None,
) -> bool:
    """Show a resume sub-menu if a resumable session exists, else start new."""
    from wade.ui import prompts

    resumable = _get_latest_resumable_session(repo_root, pr_number)
    if not resumable:
        return _run_implement_task(
            target=target,
            ai_tool=ai_tool,
            model=model,
            project_root=project_root,
            detach=detach,
            cd_only=cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort=effort,
            effort_explicit=effort_explicit,
            yolo=yolo,
        )

    session_id = resumable.session_id
    tool_name = resumable.ai_tool
    short_id = session_id[:16] + "…" if len(session_id) > 16 else session_id

    labels = [
        f"Resume last session  ({tool_name}: {short_id})",
        "Start new session",
    ]
    choice = prompts.select("How do you want to continue?", labels)

    if choice == 0:
        # Resume
        return _run_implement_task(
            target=target,
            ai_tool=ai_tool,
            model=model,
            project_root=project_root,
            detach=detach,
            cd_only=cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort=effort,
            effort_explicit=effort_explicit,
            resume_session_id=session_id,
            resume_ai_tool=tool_name,
            yolo=yolo,
        )
    else:
        # Start new session
        return _run_implement_task(
            target=target,
            ai_tool=ai_tool,
            model=model,
            project_root=project_root,
            detach=detach,
            cd_only=cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort=effort,
            effort_explicit=effort_explicit,
            yolo=yolo,
        )


def _run_continue_working_wrapper(
    target: str,
    ai_tool: str | None,
    model: str | None,
    project_root: Path | None,
    detach: bool,
    cd_only: bool,
    ai_explicit: bool,
    model_explicit: bool,
    repo_root: Path,
    pr_number: int,
    effort: str | None = None,
    effort_explicit: bool = False,
    yolo: bool | None = None,
) -> Callable[[], bool]:
    """Return a callable that runs _run_continue_working with captured arguments."""

    def _impl() -> bool:
        return _run_continue_working(
            target=target,
            ai_tool=ai_tool,
            model=model,
            project_root=project_root,
            detach=detach,
            cd_only=cd_only,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            repo_root=repo_root,
            pr_number=pr_number,
            effort=effort,
            effort_explicit=effort_explicit,
            yolo=yolo,
        )

    return _impl
