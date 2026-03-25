"""Smart start service — detects PR state and routes to the right command.

When `wade <ISSUE_ID>` is invoked, this service checks if an open PR already
exists for the issue and presents a contextual menu:
- No PR → implement (normal flow)
- Open PR → "Continue working" / "Review PR comments" / "Merge PR"
"""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from wade.ai_tools.base import AbstractAITool
from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git.repo import GitError
from wade.models.ai import AIToolID
from wade.models.session import MergeStatus, SessionRecord
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services.implementation_service import _merge_pr, check_tracking_issue_and_batch
from wade.ui.console import console
from wade.utils.markdown import parse_sessions_from_body

logger = structlog.get_logger()


class SmartStartContext(BaseModel):
    """Bundles the repeated parameters threaded through smart_start call sites."""

    target: str
    ai_tool: str | None
    model: str | None
    project_root: Path | None
    detach: bool
    cd_only: bool
    ai_explicit: bool
    model_explicit: bool
    effort: str | None
    effort_explicit: bool
    yolo: bool | None

    def run_implement(
        self,
        *,
        resume_session_id: str | None = None,
        resume_ai_tool: str | None = None,
    ) -> bool:
        """Delegate to the implement service."""
        from wade.services.implementation_service import start as do_start

        result = do_start(
            target=self.target,
            ai_tool=self.ai_tool,
            model=self.model,
            project_root=self.project_root,
            detach=self.detach,
            cd_only=self.cd_only,
            ai_explicit=self.ai_explicit,
            model_explicit=self.model_explicit,
            effort=self.effort,
            effort_explicit=self.effort_explicit,
            resume_session_id=resume_session_id,
            resume_ai_tool=resume_ai_tool,
            yolo=self.yolo,
        )
        return result.success


def _open_pr_in_browser(pr_url: str) -> bool:
    """Open PR URL in the default system browser."""
    console.info("Opening PR in browser…")
    webbrowser.open(pr_url)
    return True


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
    ctx = SmartStartContext(
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

    config = load_config(project_root)
    provider = get_provider(config)

    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        # Not in a git repo — fall through to implement which will
        # give a proper error.
        return ctx.run_implement()

    # Read the issue
    issue_number = target.lstrip("#")
    try:
        task = provider.read_task(issue_number)
    except Exception:
        # Can't read issue — fall through to implement.
        return ctx.run_implement()

    # Tracking issue detection — redirect to batch implementation
    batch_result = check_tracking_issue_and_batch(
        task,
        ai_tool=ctx.ai_tool,
        model=ctx.model,
        project_root=ctx.project_root,
        ai_explicit=ctx.ai_explicit,
        model_explicit=ctx.model_explicit,
        effort=ctx.effort,
        effort_explicit=ctx.effort_explicit,
        yolo=ctx.yolo,
        cd_only=ctx.cd_only,
    )
    if batch_result is not None:
        return batch_result

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
        return ctx.run_implement()

    pr_number = pr_info.get("number") or pr_info.get("pr_number")
    if not pr_number:
        # Can't determine PR number — fall through to implement.
        return ctx.run_implement()
    pr_number_int = int(pr_number)
    pr_state = str(pr_info.get("state", "")).upper()
    pr_url_raw = pr_info.get("url")
    pr_url = pr_url_raw.strip() if isinstance(pr_url_raw, str) else ""

    if pr_state == "MERGED":
        console.info(f"PR #{pr_number_int} is already merged.")
        return True

    # cd_only: skip menu, just set up worktree and print path
    if cd_only:
        return ctx.run_implement()

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
        if has_worktree:
            menu_options.append(
                (
                    "Continue working",
                    partial(_run_continue_working, ctx, repo_root, pr_number_int),
                )
            )
        else:
            menu_options.append(
                (
                    "Start implementation",
                    ctx.run_implement,
                )
            )
    else:
        menu_options.append(
            (
                "Continue working",
                partial(_run_continue_working, ctx, repo_root, pr_number_int),
            )
        )
        review_worktree_path = next(
            (Path(wt["path"]) for wt in worktrees if wt.get("branch") == branch_name),
            None,
        )
        menu_options.append(
            (
                "Review PR comments",
                partial(
                    _run_review_pr_comments,
                    ctx,
                    repo_root=repo_root,
                    branch_name=branch_name,
                    pr_number=pr_number_int,
                    issue_number=str(task.id),
                    worktree_path=review_worktree_path,
                    provider=provider,
                ),
            )
        )
        menu_options.append(
            (
                "Merge PR",
                partial(
                    _run_merge_pr,
                    repo_root,
                    branch_name,
                    pr_number_int,
                    task.id,
                    provider,
                    worktrees,
                ),
            )
        )

    # Common option at the end of menu
    if pr_url:
        menu_options.append(
            (
                "Open PR in browser",
                partial(_open_pr_in_browser, pr_url),
            )
        )

    if not prompts.is_tty():
        default_label, default_action = menu_options[0]
        console.info(f"Non-interactive mode — defaulting to '{default_label}'.")
        return default_action()

    labels = [label for label, _ in menu_options]
    choice = prompts.select(
        f"PR #{pr_number_int} exists — what do you want to do?",
        labels,
    )

    # Dispatch the selected action
    return menu_options[choice][1]()


# ---------------------------------------------------------------------------
# Menu action helpers
# ---------------------------------------------------------------------------


def _run_review_pr_comments(
    ctx: SmartStartContext,
    *,
    repo_root: Path,
    branch_name: str,
    pr_number: int,
    issue_number: str,
    worktree_path: Path | None,
    provider: AbstractTaskProvider,
) -> bool:
    """Poll for PR review comments and start a review session when found."""
    from wade.models.review import PollOutcome
    from wade.services import review_service

    outcome = review_service.poll_for_reviews(provider, repo_root, pr_number, branch_name)

    if outcome == PollOutcome.COMMENTS_FOUND:
        return review_service.start(
            target=ctx.target,
            ai_tool=ctx.ai_tool,
            model=ctx.model,
            project_root=ctx.project_root,
            detach=ctx.detach,
            ai_explicit=ctx.ai_explicit,
            model_explicit=ctx.model_explicit,
            yolo=ctx.yolo,
        )
    elif outcome == PollOutcome.QUIET_TIMEOUT:
        review_service._quiet_next_steps_prompt(
            repo_root,
            branch_name,
            issue_number,
            worktree_path,
            pr_number,
            provider,
            ai_tool=ctx.ai_tool,
            model=ctx.model,
            detach=ctx.detach,
            ai_explicit=ctx.ai_explicit,
            model_explicit=ctx.model_explicit,
            yolo=ctx.yolo,
        )
        return True
    else:  # INTERRUPTED or PR_CLOSED
        return True


def _run_merge_pr(
    repo_root: Path,
    branch_name: str,
    pr_number: int,
    task_id: str,
    provider: AbstractTaskProvider,
    worktrees: list[Any],
) -> bool:
    """Execute the merge PR action from the menu."""
    worktree_path = next(
        (Path(wt["path"]) for wt in worktrees if wt.get("branch") == branch_name),
        None,
    )
    if worktree_path is None:
        console.warn(
            f"No local worktree found for branch '{branch_name}' — "
            "local cleanup will be skipped after merge."
        )
    result = _merge_pr(
        repo_root,
        branch_name,
        pr_number,
        task_id,
        worktree_path,
        provider,
    )
    return result not in (MergeStatus.MERGE_FAILED, MergeStatus.NOT_MERGED)


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
    ctx: SmartStartContext,
    repo_root: Path,
    pr_number: int,
) -> bool:
    """Show a resume sub-menu if a resumable session exists, else start new."""
    from wade.ui import prompts

    resumable = _get_latest_resumable_session(repo_root, pr_number)
    if not resumable:
        return ctx.run_implement()

    if not prompts.is_tty():
        console.info("Non-interactive mode — starting a new session instead of resuming.")
        return ctx.run_implement()

    session_id = resumable.session_id
    tool_name = resumable.ai_tool
    short_id = session_id[:16] + "…" if len(session_id) > 16 else session_id

    labels = [
        f"Resume last session  ({tool_name}: {short_id})",
        "Start new session",
    ]
    choice = prompts.select("How do you want to continue?", labels)

    if choice == 0:
        return ctx.run_implement(
            resume_session_id=session_id,
            resume_ai_tool=tool_name,
        )
    return ctx.run_implement()
