"""Review service — address PR review comments in an existing worktree.

Orchestrates: fetch review threads, format comments, launch AI tool,
post-session token tracking, and label management.
"""

from __future__ import annotations

import contextlib
import tempfile
import time
from pathlib import Path

import structlog

from wade.ai_tools.base import AbstractAITool
from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.git.repo import GitError
from wade.models.ai import AIToolID
from wade.models.config import ProjectConfig
from wade.models.review import (
    PollOutcome,
    PRReviewStatus,
    ReviewBotStatus,
    detect_coderabbit_review_status,
    filter_actionable_threads,
    format_review_threads_markdown,
)
from wade.models.task import Task
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services.ai_resolution import (
    confirm_ai_selection,
    resolve_ai_tool,
    resolve_model,
    resolve_yolo,
)
from wade.services.implementation_service import (
    _detect_ai_cli_env,
    _merge_pr,
    _resolve_worktrees_dir,
    append_review_usage_entry,
    bootstrap_worktree,
    extract_issue_from_branch,
)
from wade.services.prompt_delivery import deliver_prompt_if_needed
from wade.services.task_service import add_review_addressed_by_labels
from wade.ui.console import console
from wade.utils.markdown import append_session_to_body
from wade.utils.terminal import (
    compose_review_title,
    launch_in_new_terminal,
    set_terminal_title,
    start_title_keeper,
    stop_title_keeper,
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Simple subcommands — used by AI agents during review sessions
# ---------------------------------------------------------------------------


def _resolve_task_branch(config: ProjectConfig, task: Task, repo_root: Path) -> str:
    """Resolve the branch name for a task.

    Prefers the currently checked-out branch when its issue number matches the
    task; falls back to make_branch_name for out-of-worktree or detached-HEAD
    callers.
    """
    try:
        current_branch = git_repo.get_current_branch(repo_root)
        if extract_issue_from_branch(current_branch) == str(int(task.id)):
            return current_branch
    except GitError:
        pass
    return git_branch.make_branch_name(
        config.project.branch_prefix,
        int(task.id),
        task.title,
    )


def fetch_reviews(
    target: str,
    project_root: Path | None = None,
) -> bool:
    """Fetch unresolved PR review comments and print formatted markdown to stdout.

    This is a tool for AI agents — it outputs structured markdown that the agent
    can read and act on.

    Returns:
        True on success, False on failure.
    """
    config = load_config(project_root)
    provider = get_provider(config)

    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return False

    # Read the issue
    issue_number = target.lstrip("#")
    try:
        task = provider.read_task(issue_number)
    except Exception as e:
        console.error(f"Could not read issue #{issue_number}: {e}")
        return False

    # Find branch and PR — prefer actual checked-out branch (authoritative);
    # fall back to reconstructed name for out-of-worktree or detached-HEAD callers.
    branch_name = _resolve_task_branch(config, task, repo_root)

    pr_info = git_pr.get_pr_for_branch(repo_root, branch_name)
    if not pr_info:
        console.error(f"No open PR found for branch {branch_name}")
        return False

    pr_number = int(pr_info["number"])

    # Fetch comprehensive review status
    status = get_comprehensive_review_status(provider, repo_root, pr_number)
    if status.fetch_failed:
        print("Review status fetch failed — status may be incomplete. Try again shortly.")
        return False
    actionable = status.actionable_threads

    if not actionable:
        if status.bot_status == ReviewBotStatus.PAUSED:
            print("No unresolved review comments found, but CodeRabbit review is paused.")
            print("Comments may arrive when the review is resumed.")
        elif status.bot_status == ReviewBotStatus.IN_PROGRESS:
            print("No unresolved review comments found, but CodeRabbit is still reviewing.")
            print("Try fetching again shortly.")
        else:
            print("No unresolved review comments found.")

        # Show PR-level review info even when no threads
        if status.changes_requested_by:
            names = ", ".join(f"@{a}" for a in status.changes_requested_by)
            print(f"\nNote: Changes requested by {names} (PR-level review).")
        if status.pending_reviewers:
            names = ", ".join(
                f"@{r.name}" + (" (team)" if r.is_team else "") for r in status.pending_reviewers
            )
            print(f"\nAwaiting review from {names}.")
        return True

    # Output formatted markdown to stdout (for AI consumption)
    print(format_review_threads_markdown(actionable))
    return True


def resolve_thread(
    thread_id: str,
    project_root: Path | None = None,
) -> bool:
    """Mark a PR review thread as resolved on GitHub.

    Returns:
        True on success, False on failure.
    """
    config = load_config(project_root)
    provider = get_provider(config)

    try:
        success = provider.resolve_review_thread(thread_id)
    except NotImplementedError:
        console.error("Resolving review threads is not supported by this provider.")
        return False
    except Exception as e:
        console.error(f"Failed to resolve thread: {e}")
        return False

    if success:
        console.success(f"Thread {thread_id} resolved.")
    else:
        console.error(f"Failed to resolve thread {thread_id}.")
    return success


def count_unresolved_threads(
    project_root: Path | None = None,
) -> int | None:
    """Count unresolved, actionable review threads for the current branch's PR.

    Returns:
        Number of unresolved threads, or None if the check could not be performed
        (no git repo, no branch, no PR, provider error).
    """
    status = get_review_status(project_root)
    if status is None or status.fetch_failed:
        return None
    return len(status.actionable_threads)


def get_review_status(
    project_root: Path | None = None,
) -> PRReviewStatus | None:
    """Fetch comprehensive PR review status for the current branch's PR.

    Returns:
        A :class:`PRReviewStatus` with all review data, or ``None`` if the
        check could not be performed (no git repo, no branch, no PR, provider
        error, or provider doesn't support comprehensive status).
    """
    config = load_config(project_root)
    provider = get_provider(config)

    try:
        cwd = project_root or Path.cwd()
        repo_root = git_repo.get_repo_root(cwd)
        branch = git_repo.get_current_branch(repo_root)
    except (FileNotFoundError, GitError):
        return None

    issue_number = extract_issue_from_branch(branch)
    if not issue_number:
        return None

    pr_info = git_pr.get_pr_for_branch(repo_root, branch)
    if not pr_info:
        return None

    pr_number = int(pr_info["number"])

    try:
        return provider.get_pr_review_status(repo_root, pr_number)
    except NotImplementedError:
        # Fallback: use legacy thread-only approach
        return _fallback_review_status(provider, repo_root, pr_number)
    except Exception:
        return None


def get_comprehensive_review_status(
    provider: AbstractTaskProvider,
    repo_root: Path,
    pr_number: int,
) -> PRReviewStatus:
    """Fetch comprehensive PR review status using provider with fallback.

    Unlike :func:`get_review_status`, this accepts explicit parameters instead
    of resolving from the current branch. Used by ``start()`` and
    ``fetch_reviews()`` where the PR is already known.
    """
    try:
        return provider.get_pr_review_status(repo_root, pr_number)
    except NotImplementedError:
        return _fallback_review_status(provider, repo_root, pr_number)
    except Exception:
        logger.debug("review.comprehensive_status_failed", exc_info=True)
        return PRReviewStatus(fetch_failed=True)


def _fallback_review_status(
    provider: AbstractTaskProvider,
    repo_root: Path,
    pr_number: int,
) -> PRReviewStatus:
    """Build a PRReviewStatus from legacy thread-only + bot-status APIs.

    Used when the provider doesn't support ``get_pr_review_status()``.
    """
    try:
        all_threads = provider.get_pr_review_threads(repo_root, pr_number)
    except Exception:
        return PRReviewStatus(fetch_failed=True)

    actionable = filter_actionable_threads(all_threads)
    bot_status = _check_review_bot_status(provider, pr_number)

    return PRReviewStatus(
        actionable_threads=actionable,
        bot_status=bot_status,
    )


def poll_for_reviews(
    provider: AbstractTaskProvider,
    repo_root: Path,
    pr_number: int,
    branch: str,
    *,
    poll_interval: int = 60,
    bot_settle: int = 60,
    human_settle: int = 120,
    quiet_timeout: int = 600,
) -> PollOutcome:
    """Poll for new PR review comments, blocking until a terminal condition is reached.

    Checks every ``poll_interval`` seconds.  Returns a :class:`PollOutcome`:

    * ``COMMENTS_FOUND`` — actionable threads appeared; a settle period has elapsed.
    * ``QUIET_TIMEOUT`` — the PR has been quiet for ``quiet_timeout`` seconds after
      the latest commit aged past the grace period.
    * ``PR_CLOSED`` — the PR was merged or closed externally.
    * ``INTERRUPTED`` — the user pressed Ctrl+C.
    """
    console.info("Waiting for review comments... (Ctrl+C to stop)")

    quiet_start: float | None = None

    try:
        while True:
            pr_info = git_pr.get_pr_for_branch(repo_root, branch)
            if not pr_info:
                console.info("PR is no longer open. Stopping poll.")
                return PollOutcome.PR_CLOSED
            pr_state = str(pr_info.get("state", "")).upper()
            if pr_state in ("MERGED", "CLOSED"):
                console.info(f"PR #{pr_number} was {pr_state.lower()} externally. Stopping poll.")
                return PollOutcome.PR_CLOSED

            status = get_comprehensive_review_status(provider, repo_root, pr_number)

            if status.fetch_failed:
                quiet_start = None  # reset on transient failure
                console.detail("Fetch failed — retrying shortly...")
                time.sleep(poll_interval)
                continue

            if status.bot_status == ReviewBotStatus.IN_PROGRESS:
                quiet_start = None  # bot is active; reset quiet timer
                console.detail("Bot review in progress — checking again shortly...")
                time.sleep(poll_interval)
                continue

            if status.actionable_threads:
                count = len(status.actionable_threads)
                is_bot = status.bot_status is not None
                settle = bot_settle if is_bot else human_settle
                reviewer_type = "bot" if is_bot else "reviewer"
                console.info(
                    f"Found {count} new review comment(s). "
                    f"Waiting {settle}s for {reviewer_type} to finish..."
                )
                time.sleep(settle)
                return PollOutcome.COMMENTS_FOUND

            # No actionable threads, no bot blocking — apply quiet-timeout logic.
            if status.is_commit_fresh():
                # Commit too recent; reset quiet timer and keep polling.
                quiet_start = None
                console.detail(
                    f"Commit is too recent for review — next check in {poll_interval}s..."
                )
            else:
                # Commit is old enough; start or advance the quiet timer.
                now = time.time()
                if quiet_start is None:
                    quiet_start = now
                elapsed = now - quiet_start
                if elapsed >= quiet_timeout:
                    console.info(
                        f"PR has been quiet for {int(elapsed)}s "
                        "with no new comments. Stopping poll."
                    )
                    return PollOutcome.QUIET_TIMEOUT
                console.detail(f"No new comments yet — next check in {poll_interval}s...")

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        console.info("Polling stopped.")
        return PollOutcome.INTERRUPTED


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def start(
    target: str,
    ai_tool: str | None = None,
    model: str | None = None,
    project_root: Path | None = None,
    detach: bool = False,
    *,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    yolo: bool | None = None,
) -> bool:
    """Start a review-addressing session on an issue.

    Steps:
    1. Read the issue from the provider
    2. Find existing worktree (or recover from remote branch)
    3. Find PR for the branch (error if missing or merged)
    4. Quick-check for unresolved review threads
    5. Install review-pr-comments-session skill, build prompt, launch AI
    6. Post-session: capture token usage, update PR, add labels
    7. Post-review lifecycle: "Merge PR" / "Wait for new reviews"

    Returns:
        True on success, False on failure.
    """
    config = load_config(project_root)
    provider = get_provider(config)

    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return False

    # 1. Read the issue
    issue_number = target.lstrip("#")
    try:
        task = provider.read_task(issue_number)
    except Exception as e:
        console.error(f"Could not read issue #{issue_number}: {e}")
        return False

    console.rule(f"review pr-comments #{task.id}")
    console.kv("Issue", console.issue_ref(task.id, task.title))

    # 2. Find existing worktree for the issue (or recover from remote branch)
    branch_name = _resolve_task_branch(config, task, repo_root)

    existing_wt = next(
        (
            Path(wt["path"])
            for wt in git_worktree.list_worktrees(repo_root)
            if wt.get("branch") == branch_name
        ),
        None,
    )

    if existing_wt:
        worktree_path = existing_wt
    else:
        # Try to recover: create worktree from the remote branch
        recovered = _recover_worktree(repo_root, branch_name, config)
        if not recovered:
            console.error_with_fix(
                f"No worktree or remote branch found for issue #{task.id}",
                f"Run `wade implement {task.id}` first to create a worktree",
            )
            return False
        worktree_path = recovered

    console.kv("Worktree", str(worktree_path))

    # 3. Find PR for the branch
    pr_info = git_pr.get_pr_for_branch(repo_root, branch_name)
    if not pr_info:
        console.error_with_fix(
            f"No open PR found for branch {branch_name}",
            "Run `wade implementation-session done` from the worktree to create a PR first",
        )
        return False

    pr_number = int(pr_info["number"])
    pr_state = str(pr_info.get("state", "")).upper()

    if pr_state == "MERGED":
        console.error(f"PR #{pr_number} is already merged — nothing to address.")
        return False

    console.kv("PR", f"#{pr_number}")

    # 4. Quick-check for unresolved review threads via comprehensive status
    console.step("Checking for review comments...")
    status = get_comprehensive_review_status(provider, repo_root, pr_number)
    if status.fetch_failed:
        console.warn("Review status fetch failed — status may be incomplete. Try again shortly.")
        return False
    comment_count = len(status.actionable_threads)
    file_paths = {
        t.first_comment.path
        for t in status.actionable_threads
        if t.first_comment and t.first_comment.path
    }
    file_count = len(file_paths) + (
        1
        if any(t.first_comment and not t.first_comment.path for t in status.actionable_threads)
        else 0
    )

    if comment_count == 0:
        if status.bot_status == ReviewBotStatus.PAUSED:
            console.warn(
                "CodeRabbit review is paused — comments may arrive when resumed.\n"
                "    Run '@coderabbitai resume' on the PR to trigger a new review."
            )
            return True
        if status.bot_status == ReviewBotStatus.IN_PROGRESS:
            console.warn("CodeRabbit is still reviewing — try again shortly.")
            return True
        if status.has_changes_requested:
            names = ", ".join(f"@{a}" for a in status.changes_requested_by)
            console.warn(
                f"No inline comments, but changes requested by {names} "
                "(PR-level review). Check the PR for details."
            )
            return True

        # No blocking conditions — message depends on commit freshness.
        if status.is_commit_fresh():
            console.info(
                "No review comments found yet — the latest commit is less"
                " than 2 minutes old. Review may still arrive."
            )
        elif not status.pending_reviewers:
            console.success("All review comments resolved — nothing to address! 🎉")

        if status.pending_reviewers:
            names = ", ".join(
                f"@{r.name}" + (" (team)" if r.is_team else "") for r in status.pending_reviewers
            )
            console.info(f"Awaiting review from {names}.")
            return True

        # Offer the shared quiet-exit menu: keep polling / merge / exit.
        _quiet_next_steps_prompt(
            repo_root,
            branch_name,
            task.id,
            worktree_path,
            pr_number,
            provider,
            ai_tool=ai_tool,
            model=model,
            detach=detach,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            yolo=yolo,
        )
        return True

    console.info(f"Found {comment_count} unresolved comment(s) across {file_count} location(s)")

    # 5. Re-bootstrap skills (ensures review-pr-comments-session skill is installed)
    from wade.skills.installer import REVIEW_SKILLS

    bootstrap_worktree(worktree_path, config, repo_root, skills=REVIEW_SKILLS)

    # 6. Resolve AI tool and model
    resolved_tool = resolve_ai_tool(ai_tool, config, "implement")
    resolved_model = resolve_model(
        model,
        config,
        "implement",
        tool=resolved_tool,
        complexity=task.complexity.value if task.complexity else None,
    )
    resolved_yolo = resolve_yolo(yolo, config, "implement", tool=resolved_tool)

    if not detach:
        resolved_tool, resolved_model, _effort, resolved_yolo = confirm_ai_selection(
            resolved_tool,
            resolved_model,
            tool_explicit=ai_explicit,
            model_explicit=model_explicit,
            resolved_yolo=resolved_yolo,
            yolo_explicit=yolo is not None,
        )

    # 7. Build review prompt
    prompt = build_review_prompt(
        task=task,
        pr_number=pr_number,
        comment_count=comment_count,
        file_count=file_count,
    )

    snippet = "\n".join(prompt.splitlines()[:5]) + "\n…"
    console.panel(snippet, title="Review Prompt (preview)")

    # AI-initiated start guard
    detected_env = _detect_ai_cli_env()
    if detected_env:
        logger.info(
            "review.ai_launch_skipped",
            reason="inside_ai_cli",
            env_var=detected_env,
        )
        console.info(
            f"Skipping AI launch: already inside AI session (detected via {detected_env})."
        )
        console.detail(f"Worktree ready at: {worktree_path}")
        print(str(worktree_path))
        return True

    # Set terminal title
    review_title = compose_review_title(task.id, task.title)
    set_terminal_title(review_title)
    start_title_keeper(review_title)

    # Transcript capture
    transcript_path: Path | None = None
    try:
        transcript_dir = tempfile.mkdtemp(prefix="wade-review-")
        transcript_path = Path(transcript_dir) / f"transcript-review-{task.id}.log"
        console.hint(f"Transcript: {transcript_path}")
    except OSError:
        logger.warning("review.transcript_dir_failed")

    # 8. Detach mode
    if detach and resolved_tool:
        try:
            detach_adapter = AbstractAITool.get(AIToolID(resolved_tool))
            deliver_prompt_if_needed(detach_adapter, prompt)
            cmd = detach_adapter.build_launch_command(
                model=resolved_model,
                trusted_dirs=[str(worktree_path), tempfile.gettempdir()],
                initial_message=prompt,
                yolo=resolved_yolo,
            )
        except (ValueError, KeyError):
            cmd = [resolved_tool]

        console.step(f"Launching {resolved_tool} in new terminal...")
        if launch_in_new_terminal(cmd, cwd=str(worktree_path), title=review_title):
            console.success(f"Detached review session for #{task.id}")
            stop_title_keeper()
            return True
        console.warn("Could not launch in new terminal — falling back to inline")

    # 9. Launch AI tool inline
    if resolved_tool:
        console.step(f"Launching {resolved_tool}...")

        adapter: AbstractAITool | None = None
        launch_completed = False
        detected_model: str | None = None
        try:
            adapter = AbstractAITool.get(AIToolID(resolved_tool))

            deliver_prompt_if_needed(adapter, prompt)
            exit_code = adapter.launch(
                worktree_path=worktree_path,
                model=resolved_model,
                prompt=prompt,
                transcript_path=transcript_path,
                trusted_dirs=[str(worktree_path), tempfile.gettempdir()],
                yolo=resolved_yolo,
            )
            launch_completed = True
            logger.info("review.ai_exited", exit_code=exit_code, tool=resolved_tool)

            if not adapter.capabilities().blocks_until_exit:
                from wade.ui import prompts as ui_prompts

                console.empty()
                if not ui_prompts.confirm("Have you finished the review session?", default=True):
                    console.info(
                        "Worktree preserved — run"
                        " 'wade review-pr-comments-session done'"
                        " when ready."
                    )
                    launch_completed = False
        except (ValueError, KeyError):
            console.warn(f"Unknown AI tool: {resolved_tool}")
        except Exception as e:
            console.warn(f"AI tool launch failed: {e}")
        finally:
            stop_title_keeper()

            if (
                adapter is not None
                and launch_completed
                and adapter.capabilities().blocks_until_exit
            ):
                detected_model = _capture_review_session_usage(
                    transcript_path=transcript_path,
                    adapter=adapter,
                    repo_root=repo_root,
                    branch=branch_name,
                    ai_tool=resolved_tool,
                    model=resolved_model,
                    issue_number=task.id,
                    provider=provider,
                )

        if launch_completed:
            effective_model = resolved_model or detected_model
            try:
                add_review_addressed_by_labels(provider, task.id, resolved_tool, effective_model)
            except Exception as e:
                console.warn(f"Could not apply review-addressed-by labels: {e}")
                logger.warning("review.review_addressed_by_labels_failed", error=str(e))

            # 10. Post-review lifecycle: "Merge PR" / "Wait for new reviews"
            _post_review_lifecycle(
                repo_root,
                branch_name,
                task.id,
                worktree_path,
                pr_number,
                provider,
                ai_tool=resolved_tool,
                model=effective_model,
                detach=detach,
                ai_explicit=ai_explicit,
                model_explicit=model_explicit,
                yolo=resolved_yolo,
            )
    else:
        console.info(
            "No AI tool configured — use `wade review-pr-comments-session fetch` to view comments."
        )
        console.detail(f"cd {worktree_path}")
        stop_title_keeper()

        # 10. Post-review lifecycle (no AI tool — user addressed manually)
        _post_review_lifecycle(
            repo_root,
            branch_name,
            task.id,
            worktree_path,
            pr_number,
            provider,
            ai_tool=ai_tool,
            model=model,
            detach=detach,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            yolo=yolo,
        )

    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _recover_worktree(
    repo_root: Path,
    branch_name: str,
    config: object,
) -> Path | None:
    """Try to recover a worktree from an existing remote branch.

    If the branch exists on the remote (e.g. from a PR), fetch it and
    create a new worktree pointing at it.

    Returns the worktree path on success, or None if the branch doesn't exist.
    """
    from wade.models.config import ProjectConfig

    assert isinstance(config, ProjectConfig)

    # Fetch the branch from remote
    try:
        git_repo.fetch_ref(repo_root, "origin", f"{branch_name}:{branch_name}")
    except GitError:
        logger.debug("review.fetch_branch_failed", branch=branch_name)
        return None

    # Verify the branch exists locally after fetch
    try:
        git_repo.rev_parse(repo_root, branch_name)
    except GitError:
        return None

    # Build worktree path
    worktrees_dir = _resolve_worktrees_dir(config, repo_root)
    repo_name = repo_root.name
    worktree_path = worktrees_dir / repo_name / branch_name.replace("/", "-")

    if worktree_path.exists():
        logger.debug("review.worktree_dir_exists", path=str(worktree_path))
        return None

    console.step(f"Recovering worktree from remote branch {branch_name}...")
    try:
        result = git_worktree.checkout_existing_branch_worktree(
            repo_root, branch_name, worktree_path
        )
        console.success(f"Recovered worktree at {result}")
        return result
    except Exception as e:
        logger.warning("review.worktree_recovery_failed", error=str(e))
        return None


def _quiet_next_steps_prompt(
    repo_root: Path,
    branch: str,
    issue_number: str | int | None,
    worktree_path: Path | None,
    pr_number: int,
    provider: AbstractTaskProvider,
    *,
    ai_tool: str | None = None,
    model: str | None = None,
    detach: bool = False,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    yolo: bool | None = None,
) -> None:
    """Shared next-steps menu for quiet PRs: keep polling, merge, or exit.

    Used both when ``wade review pr-comments <issue>`` finds nothing to address
    and when the polling loop hits the quiet timeout.
    """
    from wade.ui import prompts

    if not prompts.is_tty():
        return

    while True:
        allow_merge = True
        status = get_comprehensive_review_status(provider, repo_root, pr_number)
        if status.pending_reviewers:
            names = ", ".join(
                f"@{r.name}" + (" (team)" if r.is_team else "") for r in status.pending_reviewers
            )
            console.info(
                f"Awaiting review from {names}. Merge is unavailable while review is pending."
            )
            allow_merge = False

        console.empty()
        options = (
            ["Keep polling", "Merge PR", "Exit without merging"]
            if allow_merge
            else ["Keep polling", "Exit without merging"]
        )
        choice = prompts.select(f"PR #{pr_number} — what next?", options)

        if choice == 0:  # Keep polling
            outcome = poll_for_reviews(provider, repo_root, pr_number, branch)
            if outcome == PollOutcome.COMMENTS_FOUND:
                if issue_number:
                    _ = start(
                        str(issue_number),
                        ai_tool=ai_tool,
                        model=model,
                        project_root=repo_root,
                        detach=detach,
                        ai_explicit=ai_explicit,
                        model_explicit=model_explicit,
                        yolo=yolo,
                    )
                return
            elif outcome == PollOutcome.QUIET_TIMEOUT:
                continue  # Show menu again
            else:  # INTERRUPTED or PR_CLOSED
                return
        elif allow_merge and choice == 1:  # Merge PR
            _merge_pr(repo_root, branch, pr_number, issue_number, worktree_path, provider)
            return
        else:  # Exit without merging
            return


def _post_review_lifecycle(
    repo_root: Path,
    branch: str,
    issue_number: str | int | None,
    worktree_path: Path | None,
    pr_number: int,
    provider: AbstractTaskProvider,
    *,
    ai_tool: str | None = None,
    model: str | None = None,
    detach: bool = False,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    yolo: bool | None = None,
) -> None:
    """Post-review lifecycle menu: Merge PR or wait for new reviews."""
    from wade.ui import prompts

    if not prompts.is_tty():
        return

    console.empty()
    choice = prompts.select(
        f"PR #{pr_number} — what next?",
        ["Merge PR", "Wait for new reviews"],
    )

    if choice == 1:  # Wait for new reviews
        outcome = poll_for_reviews(provider, repo_root, pr_number, branch)
        if outcome == PollOutcome.COMMENTS_FOUND:
            if issue_number:
                _ = start(
                    str(issue_number),
                    ai_tool=ai_tool,
                    model=model,
                    project_root=repo_root,
                    detach=detach,
                    ai_explicit=ai_explicit,
                    model_explicit=model_explicit,
                    yolo=yolo,
                )
        elif outcome == PollOutcome.QUIET_TIMEOUT:
            _quiet_next_steps_prompt(
                repo_root,
                branch,
                issue_number,
                worktree_path,
                pr_number,
                provider,
                ai_tool=ai_tool,
                model=model,
                detach=detach,
                ai_explicit=ai_explicit,
                model_explicit=model_explicit,
                yolo=yolo,
            )
        return

    # Merge flow — reuse the same merge logic as post-implementation lifecycle
    _merge_pr(repo_root, branch, pr_number, issue_number, worktree_path, provider)


def _check_review_bot_status(
    provider: AbstractTaskProvider,
    pr_number: int,
) -> ReviewBotStatus | None:
    """Check if a review bot (e.g. CodeRabbit) has a pending review on the PR."""
    try:
        comments = provider.get_pr_issue_comments(pr_number)
    except Exception:
        logger.debug("review.bot_status_check_failed", exc_info=True)
        return None
    return detect_coderabbit_review_status(comments)


def build_review_prompt(
    task: Task,
    pr_number: int,
    comment_count: int,
    file_count: int,
) -> str:
    """Build the initial prompt for a review session."""
    from wade.skills.installer import get_templates_dir

    template_path = get_templates_dir() / "prompts" / "review-pr-comments.md"
    if not template_path.is_file():
        raise FileNotFoundError(f"Review prompt template not found: {template_path}")
    template = template_path.read_text(encoding="utf-8")
    return template.format(
        issue_number=task.id,
        issue_title=task.title,
        pr_number=pr_number,
        comment_count=comment_count,
        file_count=file_count,
    )


def _capture_review_session_usage(
    transcript_path: Path | None,
    adapter: AbstractAITool,
    repo_root: Path,
    branch: str,
    ai_tool: str,
    model: str | None,
    issue_number: str | None = None,
    provider: AbstractTaskProvider | None = None,
) -> str | None:
    """Post-AI-exit processing: parse transcript, update PR and issue with review usage.

    Returns the primary model detected from the transcript.
    """
    if not transcript_path or not transcript_path.is_file():
        return None

    try:
        usage = adapter.parse_transcript(transcript_path)
    except Exception as e:
        logger.warning("review.transcript_parse_failed", error=str(e))
        return None

    has_tokens = usage and (usage.total_tokens or usage.input_tokens)
    has_session = usage and usage.session_id
    if not has_tokens and not has_session:
        logger.warning("review.no_token_usage", transcript=str(transcript_path))
        console.warn(f"No token usage found in transcript: {transcript_path}")
        return None

    effective_model = model or (
        usage.model_breakdown[0].model if usage and usage.model_breakdown else None
    )

    # Update PR body with review usage stats
    pr_info = git_pr.get_pr_for_branch(repo_root, branch)
    if pr_info:
        pr_number = int(pr_info["number"])
        try:
            current_body = git_pr.get_pr_body(repo_root, pr_number)
            if current_body is not None:
                new_body = current_body
                assert usage is not None
                new_body = append_review_usage_entry(
                    new_body,
                    ai_tool=ai_tool,
                    model=effective_model,
                    token_usage=usage,
                )
                if has_session:
                    assert usage is not None and usage.session_id is not None
                    new_body = append_session_to_body(
                        new_body, phase="Review", ai_tool=ai_tool, session_id=usage.session_id
                    )
                if git_pr.update_pr_body(repo_root, pr_number, new_body):
                    console.success("Updated PR with review usage stats.")
                    logger.info(
                        "review.usage_updated",
                        pr=pr_number,
                        total_tokens=usage.total_tokens if usage else None,
                    )
        except Exception:
            logger.debug("review.pr_body_read_failed", exc_info=True)
    else:
        logger.debug("review.no_pr_for_branch", branch=branch)

    # Update issue body with review usage stats
    if issue_number and provider:
        with contextlib.suppress(Exception):
            task = provider.read_task(str(issue_number))
            new_body = task.body
            assert usage is not None
            new_body = append_review_usage_entry(
                new_body,
                ai_tool=ai_tool,
                model=effective_model,
                token_usage=usage,
            )
            if has_session:
                assert usage is not None and usage.session_id is not None
                new_body = append_session_to_body(
                    new_body, phase="Review", ai_tool=ai_tool, session_id=usage.session_id
                )
            provider.update_task(str(issue_number), body=new_body)
            console.success("Updated issue with review usage stats.")
            logger.info("review.usage_issue_updated", issue=issue_number)

    return effective_model
