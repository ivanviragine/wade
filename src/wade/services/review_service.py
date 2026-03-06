"""Review service — address PR review comments in an existing worktree.

Orchestrates: fetch review threads, format comments, launch AI tool,
post-session token tracking, and label management.
"""

from __future__ import annotations

import contextlib
import tempfile
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
from wade.models.review import (
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
)
from wade.services.prompt_delivery import deliver_prompt_if_needed
from wade.services.task_service import add_reviewed_by_labels
from wade.services.work_service import (
    _strip_review_usage_block,
    bootstrap_worktree,
    build_review_usage_block,
)
from wade.ui.console import console
from wade.utils.markdown import append_session_to_body
from wade.utils.terminal import (
    compose_work_title,
    launch_in_new_terminal,
    set_terminal_title,
    start_title_keeper,
    stop_title_keeper,
)

logger = structlog.get_logger()


def start(
    target: str,
    ai_tool: str | None = None,
    model: str | None = None,
    project_root: Path | None = None,
    detach: bool = False,
    *,
    ai_explicit: bool = False,
    model_explicit: bool = False,
) -> bool:
    """Start a review-addressing session on an issue.

    Steps:
    1. Read the issue from the provider
    2. Find existing worktree (error if missing)
    3. Find PR for the branch (error if missing or merged)
    4. Fetch and filter review threads
    5. If no actionable threads: display success, return
    6. Write REVIEW-COMMENTS.md to worktree
    7. Install review-session skill, build prompt, launch AI
    8. Post-session: capture token usage, update PR, add labels

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

    console.rule(f"address-reviews #{task.id}")
    console.kv("Issue", console.issue_ref(task.id, task.title))

    # 2. Find existing worktree for the issue
    branch_name = git_branch.make_branch_name(
        config.project.branch_prefix,
        int(task.id),
        task.title,
    )

    existing_wt = next(
        (
            Path(wt["path"])
            for wt in git_worktree.list_worktrees(repo_root)
            if wt.get("branch") == branch_name
        ),
        None,
    )

    if not existing_wt:
        console.error_with_fix(
            f"No worktree found for issue #{task.id}",
            f"Run `wade implement-task {task.id}` first to create a worktree",
        )
        return False

    worktree_path = existing_wt
    console.kv("Worktree", str(worktree_path))

    # 3. Find PR for the branch
    pr_info = git_pr.get_pr_for_branch(repo_root, branch_name)
    if not pr_info:
        console.error_with_fix(
            f"No open PR found for branch {branch_name}",
            "Run `wade work done` from the worktree to create a PR first",
        )
        return False

    pr_number = int(pr_info["number"])
    pr_state = str(pr_info.get("state", "")).upper()

    if pr_state == "MERGED":
        console.error(f"PR #{pr_number} is already merged — nothing to address.")
        return False

    console.kv("PR", f"#{pr_number}")

    # 4. Fetch review threads
    console.step("Fetching review comments...")
    try:
        all_threads = provider.get_pr_review_threads(repo_root, pr_number)
    except NotImplementedError:
        console.error("Review thread fetching is not supported by this provider.")
        return False
    except Exception as e:
        console.error(f"Failed to fetch review threads: {e}")
        return False

    # 5. Filter to actionable threads
    actionable = filter_actionable_threads(all_threads)

    if not actionable:
        console.success("All review comments resolved — nothing to address! 🎉")
        return True

    # Count files
    file_paths = {
        t.first_comment.path for t in actionable if t.first_comment and t.first_comment.path
    }
    comment_count = len(actionable)
    file_count = len(file_paths) + (
        1 if any(t.first_comment and not t.first_comment.path for t in actionable) else 0
    )

    console.info(f"Found {comment_count} unresolved comment(s) across {file_count} file(s)")

    # 6. Format and write REVIEW-COMMENTS.md
    review_md = format_review_threads_markdown(actionable)
    review_file = worktree_path / "REVIEW-COMMENTS.md"
    review_file.write_text(review_md, encoding="utf-8")
    console.detail(f"Wrote {review_file}")

    # 7. Re-bootstrap skills (ensures review-session skill is installed)
    bootstrap_worktree(worktree_path, config, repo_root)

    # 8. Resolve AI tool and model
    resolved_tool = resolve_ai_tool(ai_tool, config, "work")
    resolved_model = resolve_model(
        model,
        config,
        "work",
        tool=resolved_tool,
        complexity=task.complexity.value if task.complexity else None,
    )

    if not detach:
        resolved_tool, resolved_model = confirm_ai_selection(
            resolved_tool,
            resolved_model,
            tool_explicit=ai_explicit,
            model_explicit=model_explicit,
        )

    # 9. Build review prompt
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
    work_title = compose_work_title(task.id, task.title)
    set_terminal_title(f"review {work_title}")
    start_title_keeper(f"review {work_title}")

    # Transcript capture
    transcript_path: Path | None = None
    try:
        transcript_dir = tempfile.mkdtemp(prefix="wade-review-")
        transcript_path = Path(transcript_dir) / f"transcript-review-{task.id}.log"
        console.hint(f"Transcript: {transcript_path}")
    except OSError:
        logger.warning("review.transcript_dir_failed")

    # 10. Detach mode
    if detach and resolved_tool:
        try:
            detach_adapter = AbstractAITool.get(AIToolID(resolved_tool))
            deliver_prompt_if_needed(detach_adapter, prompt)
            cmd = detach_adapter.build_launch_command(
                model=resolved_model,
                trusted_dirs=[str(worktree_path), tempfile.gettempdir()],
                initial_message=prompt,
            )
        except (ValueError, KeyError):
            cmd = [resolved_tool]

        console.step(f"Launching {resolved_tool} in new terminal...")
        if launch_in_new_terminal(cmd, cwd=str(worktree_path), title=f"review {work_title}"):
            console.success(f"Detached review session for #{task.id}")
            stop_title_keeper()
            return True
        console.warn("Could not launch in new terminal — falling back to inline")

    # 11. Launch AI tool inline
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
            )
            launch_completed = True
            logger.info("review.ai_exited", exit_code=exit_code, tool=resolved_tool)

            if not adapter.capabilities().blocks_until_exit:
                from wade.ui import prompts

                console.empty()
                if not prompts.confirm("Have you finished the review session?", default=True):
                    console.info("Worktree preserved — run 'wade work done' when ready.")
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

        effective_model = resolved_model or detected_model
        try:
            add_reviewed_by_labels(provider, task.id, resolved_tool, effective_model)
        except Exception as e:
            console.warn(f"Could not apply reviewed-by labels: {e}")
            logger.warning("review.reviewed_by_labels_failed", error=str(e))
    else:
        console.info("No AI tool configured. Review comments in REVIEW-COMMENTS.md.")
        console.detail(f"cd {worktree_path}")
        stop_title_keeper()

    # Summary panel (no merge prompt — review cycle is iterative)
    lines = []
    lines.append(f"  Worktree   {console.git_ref(branch_name)}")
    lines.append(f"  Issue      {console.issue_ref(task.id, task.title)}")
    lines.append(f"  PR         #{pr_number}")
    console.panel("\n".join(lines), title="Review session complete")

    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_review_prompt(
    task: Task,
    pr_number: int,
    comment_count: int,
    file_count: int,
) -> str:
    """Build the initial prompt for a review session."""
    from wade.skills.installer import get_templates_dir

    template_path = get_templates_dir() / "prompts" / "review-context.md"
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


def _detect_ai_cli_env() -> str | None:
    """Detect if running inside an AI CLI tool (to avoid nesting)."""
    import os

    for var in ("CLAUDE_CODE", "COPILOT_CLI", "CURSOR_SESSION", "AIDER_SESSION"):
        if os.environ.get(var):
            return var
    return None


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
                if has_tokens:
                    assert usage is not None
                    usage_block = build_review_usage_block(
                        ai_tool=ai_tool,
                        model=effective_model,
                        token_usage=usage,
                    )
                    stripped = _strip_review_usage_block(new_body).rstrip("\n")
                    new_body = stripped + "\n\n" + usage_block + "\n"
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
            if has_tokens:
                assert usage is not None
                usage_block = build_review_usage_block(
                    ai_tool=ai_tool,
                    model=effective_model,
                    token_usage=usage,
                )
                stripped = _strip_review_usage_block(new_body).rstrip("\n")
                new_body = stripped + "\n\n" + usage_block + "\n"
            if has_session:
                assert usage is not None and usage.session_id is not None
                new_body = append_session_to_body(
                    new_body, phase="Review", ai_tool=ai_tool, session_id=usage.session_id
                )
            provider.update_task(str(issue_number), body=new_body)
            console.success("Updated issue with review usage stats.")
            logger.info("review.usage_issue_updated", issue=issue_number)

    return effective_model
