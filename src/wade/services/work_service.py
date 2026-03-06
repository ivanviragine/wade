"""Work service — session lifecycle: start, done, sync, list, batch, remove.

Orchestrates: worktree creation, bootstrap, AI tool launch, PR/merge,
sync, list, batch topology.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import Any

import structlog

from wade.ai_tools.base import AbstractAITool
from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import sync as git_sync
from wade.git import worktree as git_worktree
from wade.git.repo import GitError
from wade.models.ai import AIToolID, TokenUsage
from wade.models.config import ProjectConfig
from wade.models.deps import DependencyGraph
from wade.models.task import Task
from wade.models.work import (
    MergeStrategy,
    SyncEvent,
    SyncEventType,
    SyncResult,
    WorktreeState,
)
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services.ai_resolution import confirm_ai_selection, resolve_ai_tool, resolve_model
from wade.services.prompt_delivery import deliver_prompt_if_needed
from wade.services.task_service import (
    add_in_progress_label,
    add_worked_by_labels,
    remove_in_progress_label,
)
from wade.ui import prompts
from wade.ui.console import console
from wade.utils.markdown import append_session_to_body, remove_marker_block
from wade.utils.terminal import (
    compose_work_title,
    launch_in_new_terminal,
    set_terminal_title,
    start_title_keeper,
    stop_title_keeper,
)

logger = structlog.get_logger()

# --- Implementation usage block markers ---
IMPL_USAGE_MARKER_START = "<!-- wade:impl-usage:start -->"
IMPL_USAGE_MARKER_END = "<!-- wade:impl-usage:end -->"

# --- Review usage block markers ---
REVIEW_USAGE_MARKER_START = "<!-- wade:review-usage:start -->"
REVIEW_USAGE_MARKER_END = "<!-- wade:review-usage:end -->"

# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------


def _resolve_worktrees_dir(config: ProjectConfig, repo_root: Path) -> Path:
    """Resolve the worktrees directory from config."""
    wt_dir = config.project.worktrees_dir
    if Path(wt_dir).is_absolute():
        return Path(wt_dir)
    return (repo_root / wt_dir).resolve()


def _format_uncommitted_summary(cwd: Path) -> str:
    """Build a human-readable summary of dirty working tree status."""
    dirty = git_repo.get_dirty_status(cwd)
    parts: list[str] = []
    if dirty["staged"]:
        parts.append(f"{dirty['staged']} staged")
    if dirty["unstaged"]:
        parts.append(f"{dirty['unstaged']} unstaged")
    if dirty["untracked"]:
        parts.append(f"{dirty['untracked']} untracked")
    return ", ".join(parts) if parts else "dirty"


def write_plan_md(
    worktree_path: Path,
    task: Task,
    plan_content: str | None = None,
) -> Path:
    """Write PLAN.md to the worktree.

    Args:
        worktree_path: Worktree directory.
        task: Task with metadata (id, title, url).
        plan_content: Optional plan content to use instead of task.body.
            When provided (e.g. extracted from a draft PR), this takes priority.
    """
    plan_path = worktree_path / "PLAN.md"
    lines = [
        f"# Issue #{task.id}: {task.title}",
        "",
    ]
    body = plan_content if plan_content is not None else task.body
    if body:
        lines.append(body)
    if task.url:
        lines.append("")
        lines.append(f"URL: {task.url}")

    plan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("work.plan_md_written", path=str(plan_path))
    return plan_path


def bootstrap_worktree(
    worktree_path: Path,
    config: ProjectConfig,
    repo_root: Path,
) -> None:
    """Run post-creation bootstrap: copy files, install skills, run hooks."""
    # Copy configured files
    for filename in config.hooks.copy_to_worktree:
        src = repo_root / filename
        dest = worktree_path / filename
        if src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            logger.debug("work.bootstrap_copy", file=filename)

    # Install skill files — not tracked by git so worktrees don't inherit them
    from wade.skills.installer import get_wade_repo_root, install_skills

    is_self = repo_root.resolve() == get_wade_repo_root().resolve()
    if is_self:
        # Worktree has its own templates/ checkout — symlink to those
        wt_templates = worktree_path / "templates" / "skills"
        install_skills(worktree_path, is_self_init=True, force=True, templates_dir=wt_templates)
    else:
        install_skills(worktree_path, is_self_init=False, force=True)
    logger.debug("work.bootstrap_skills", path=str(worktree_path))

    # Propagate allowlist from project root to worktree if already configured
    from wade.config.claude_allowlist import configure_allowlist, is_allowlist_configured

    if is_allowlist_configured(repo_root):
        configure_allowlist(worktree_path, extra_patterns=config.permissions.allowed_commands)

    # Propagate Cursor allowlist to worktree's per-project .cursor/cli.json
    from wade.config.cursor_allowlist import configure_allowlist as configure_cursor_allowlist
    from wade.config.cursor_allowlist import is_allowlist_configured as is_cursor_configured

    if is_cursor_configured() or is_cursor_configured(repo_root):
        configure_cursor_allowlist(
            worktree_path, extra_patterns=config.permissions.allowed_commands
        )

    # Run post-create hook
    if config.hooks.post_worktree_create:
        hook_path = repo_root / config.hooks.post_worktree_create
        if hook_path.is_file():
            try:
                subprocess.run(
                    [str(hook_path)],
                    cwd=str(worktree_path),
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
                logger.info("work.hook_ran", hook=config.hooks.post_worktree_create)
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(f"Bootstrap hook timed out after 60 seconds: {hook_path}") from e
            except subprocess.CalledProcessError as e:
                hook_path_str = str(hook_path)
                logger.warning(
                    "work.hook_failed",
                    hook=config.hooks.post_worktree_create,
                    hook_path=hook_path_str,
                    error=e.stderr.decode("utf-8", errors="replace") if e.stderr else "",
                    msg=f"Hook script failed: {hook_path_str}. Check logs for details.",
                )


def _detect_ai_cli_env() -> str | None:
    """Detect which AI CLI session we are running inside, if any.

    Returns the env-var name that triggered detection, or ``None``.

    When an AI agent calls ``wade implement-task`` from within its own
    session, we must not launch another AI instance (infinite nesting).
    Instead, create the worktree and print the path.
    """
    # Claude Code sets CLAUDE_CODE=1 or CLAUDE_CODE_ENTRYPOINT
    if os.environ.get("CLAUDE_CODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "CLAUDE_CODE"
    # Copilot CLI
    if os.environ.get("COPILOT_CLI"):
        return "COPILOT_CLI"
    # Gemini CLI
    if os.environ.get("GEMINI_CLI"):
        return "GEMINI_CLI"
    # Codex CLI
    if os.environ.get("CODEX_CLI"):
        return "CODEX_CLI"
    # Cursor CLI
    if os.environ.get("CURSOR_CLI"):
        return "CURSOR_CLI"
    return None


# ---------------------------------------------------------------------------
# Draft PR bootstrap (shared by plan and work flows)
# ---------------------------------------------------------------------------

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
) -> dict[str, str | int] | None:
    """Create branch + push + draft PR for an issue.

    Reusable by both plan and work flows. Idempotent — if the branch and
    PR already exist, returns the existing PR info.

    Args:
        issue_number: GitHub issue number.
        issue_title: Issue title (used for branch name and PR title).
        plan_body: Plan content to embed in the draft PR body.
        config: Project configuration.
        repo_root: Repository root directory.

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
        logger.info(
            "bootstrap_draft_pr.existing",
            branch=branch_name,
            pr=existing_pr["number"],
        )
        return existing_pr

    # Create branch from main (if not exists)
    main_branch = config.project.main_branch or git_repo.detect_main_branch(repo_root)
    if not git_branch.branch_exists(repo_root, branch_name):
        git_branch.create_branch(repo_root, branch_name, main_branch)
        logger.info("bootstrap_draft_pr.branch_created", branch=branch_name)

    # Scaffold commit so GitHub accepts the draft PR (needs ≥1 commit ahead of base)
    if git_branch.commits_ahead(repo_root, branch_name, main_branch) == 0:
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

    # Create draft PR
    try:
        pr_info = git_pr.create_pr(
            repo_root=repo_root,
            title=issue_title,
            body=body,
            base=main_branch,
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


def _capture_post_session_usage(
    transcript_path: Path | None,
    adapter: AbstractAITool,
    repo_root: Path,
    branch: str,
    ai_tool: str,
    model: str | None,
    issue_number: str | None = None,
    provider: AbstractTaskProvider | None = None,
) -> str | None:
    """Post-AI-exit processing: parse transcript, update PR and issue with token usage.

    Returns the primary model detected from the transcript (for worked-model label),
    or the explicitly passed model if no breakdown is available.
    """
    if not transcript_path or not transcript_path.is_file():
        return None

    # Parse transcript for token usage
    try:
        usage = adapter.parse_transcript(transcript_path)
    except Exception as e:
        logger.warning("work.transcript_parse_failed", error=str(e))
        return None

    has_tokens = usage and (usage.total_tokens or usage.input_tokens)
    has_session = usage and usage.session_id
    if not has_tokens and not has_session:
        logger.warning("work.no_token_usage", transcript=str(transcript_path))
        console.warn(f"No token usage found in transcript: {transcript_path}")
        return None

    # Use transcript model_breakdown as source of truth when model wasn't set explicitly
    effective_model = model or (
        usage.model_breakdown[0].model if usage and usage.model_breakdown else None
    )

    # Update PR body with usage stats and session ID
    pr_info = git_pr.get_pr_for_branch(repo_root, branch)
    if pr_info:
        pr_number = int(pr_info["number"])
        try:
            current_body = git_pr.get_pr_body(repo_root, pr_number)
            if current_body is not None:
                new_body = current_body
                if has_tokens:
                    assert usage is not None
                    new_body = append_impl_usage_entry(
                        new_body,
                        ai_tool=ai_tool,
                        model=effective_model,
                        token_usage=usage,
                    )
                if has_session:
                    assert usage is not None and usage.session_id is not None
                    new_body = append_session_to_body(
                        new_body, phase="Implement", ai_tool=ai_tool, session_id=usage.session_id
                    )
                if git_pr.update_pr_body(repo_root, pr_number, new_body):
                    console.success("Updated PR with implementation usage stats.")
                    logger.info(
                        "work.impl_usage_updated",
                        pr=pr_number,
                        total_tokens=usage.total_tokens if usage else None,
                    )
        except Exception:
            logger.debug("work.pr_body_read_failed", exc_info=True)
    else:
        logger.debug("work.no_pr_for_branch", branch=branch)

    # Embed usage stats and session ID in the issue body
    if issue_number and provider:
        with contextlib.suppress(Exception):
            task = provider.read_task(str(issue_number))
            new_body = task.body
            if has_tokens:
                assert usage is not None
                new_body = append_impl_usage_entry(
                    new_body,
                    ai_tool=ai_tool,
                    model=effective_model,
                    token_usage=usage,
                )
            if has_session:
                assert usage is not None and usage.session_id is not None
                new_body = append_session_to_body(
                    new_body, phase="Implement", ai_tool=ai_tool, session_id=usage.session_id
                )
            provider.update_task(str(issue_number), body=new_body)
            console.success("Updated issue with implementation usage stats.")
            logger.info("work.impl_usage_issue_updated", issue=issue_number)

    return effective_model


def _build_work_issue_context_header(task: Task) -> str:
    """Build an issue description block to prepend to the work prompt."""
    lines = [
        "## Issue Description",
        "",
        (task.body or "").strip(),
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def build_work_prompt(task: Task, ai_tool: str | None = None, has_plan: bool = False) -> str:
    """Build the initial prompt for a work session.

    When *has_plan* is False (no plan content in the draft PR), the issue
    description is prepended inline so the AI has it without relying on
    @PLAN.md.  When a plan already exists, PLAN.md carries the full context
    and the inline header is skipped to avoid duplication.
    """
    from wade.skills.installer import get_templates_dir

    template_path = get_templates_dir() / "prompts" / "work-context.md"
    if not template_path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")
    template = template_path.read_text(encoding="utf-8")
    prompt = template.format(issue_number=task.id, issue_title=task.title)
    if task.body and not has_plan:
        prompt = _build_work_issue_context_header(task) + prompt
    return prompt


def _post_work_lifecycle(
    repo_root: Path,
    branch: str,
    issue_number: str | int | None,
    worktree_path: Path | None,
    config: ProjectConfig,
    provider: AbstractTaskProvider,
) -> None:
    if config.project.merge_strategy == MergeStrategy.PR:
        _post_work_lifecycle_pr(repo_root, branch, issue_number, worktree_path, provider)
    else:
        _post_work_lifecycle_direct(
            repo_root, branch, issue_number, worktree_path, config, provider
        )


def _parse_overwrite_paths(stderr: str) -> list[str]:
    """Extract conflicting file paths from a git 'would be overwritten' error."""
    paths: list[str] = []
    in_block = False
    for line in stderr.splitlines():
        if "would be overwritten by merge" in line:
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if not stripped or stripped.startswith("Please"):
                break
            paths.append(stripped)
    return paths


def _warn_pull_sync_failed() -> None:
    console.warn("Could not sync local main branch after merge.")
    console.hint("Run 'git pull' manually to update your local branch.")


def _pull_main_after_merge(repo_root: Path) -> None:
    """Pull the latest main branch after a successful PR merge.

    Handles the common case where wade-managed files (skills, settings) were
    installed by ``wade init`` as untracked files in the repo root. When the PR
    being merged introduced those same files as tracked, a plain ``git pull``
    aborts with "untracked files would be overwritten". This helper detects that
    condition, removes the conflicting untracked files (they will be replaced by
    the tracked versions from the merge), and retries the pull.

    Also handles local modifications to tracked files (e.g. ``wade init``
    modifying ``.gitignore``) by stashing, pulling, and popping the stash.
    """
    result = git_repo.pull_ff_only(repo_root)
    if result.returncode == 0:
        return
    if "untracked working tree files would be overwritten by merge" in result.stderr:
        for rel_path in _parse_overwrite_paths(result.stderr):
            target = repo_root / rel_path
            target.unlink(missing_ok=True)
            with contextlib.suppress(Exception):
                target.parent.rmdir()
        retry = git_repo.pull_ff_only(repo_root)
        if retry.returncode != 0:
            _warn_pull_sync_failed()
    elif "Your local changes to the following files would be overwritten" in result.stderr:
        # Stash local changes, pull, then restore
        stash_result = git_repo.stash(repo_root)
        if stash_result.returncode != 0:
            _warn_pull_sync_failed()
            return
        retry = git_repo.pull_ff_only(repo_root)
        git_repo.stash_pop(repo_root)  # best-effort restore; failure leaves stash intact
        if retry.returncode != 0:
            _warn_pull_sync_failed()
    else:
        _warn_pull_sync_failed()


def _post_work_lifecycle_pr(
    repo_root: Path,
    branch: str,
    issue_number: str | int | None,
    worktree_path: Path | None,
    provider: AbstractTaskProvider,
) -> None:
    pr_info = git_pr.get_pr_for_branch(repo_root, branch)
    if not pr_info:
        console.warn(f"No open PR found for branch '{branch}'. Skipping lifecycle.")
        return

    pr_number = pr_info.get("number") or pr_info.get("pr_number")
    if not pr_number:
        console.warn(f"Could not determine PR number for branch '{branch}'.")
        return

    pr_url = str(pr_info.get("url", ""))
    if pr_url and prompts.confirm("Open PR in browser?", default=True):
        webbrowser.open(pr_url)

    choice = prompts.select(
        f"PR #{pr_number} — what next?",
        ["Merge PR", "Wait for reviews"],
    )

    if choice == 1:  # Wait for reviews
        issue_hint = f" {issue_number}" if issue_number else ""
        console.hint(f"Run `wade address-reviews{issue_hint}` when reviews come in.")
        return

    # Merge flow
    _merge_pr(repo_root, branch, int(pr_number), issue_number, worktree_path, provider)


def _merge_pr(
    repo_root: Path,
    branch: str,
    pr_number: int,
    issue_number: str | int | None,
    worktree_path: Path | None,
    provider: AbstractTaskProvider,
) -> None:
    """Merge a PR via squash, clean up worktree, pull main, close issue."""
    # Warn if the worktree has uncommitted changes before proceeding.
    if worktree_path and worktree_path.is_dir() and not git_repo.is_clean(worktree_path):
        console.warn("Worktree has uncommitted changes.")
        if not prompts.confirm("Proceed anyway? Uncommitted work will be lost.", default=False):
            return

    # Detach HEAD in the worktree so git no longer considers the branch
    # "checked out", which unblocks `gh pr merge --delete-branch`.
    if worktree_path and worktree_path.is_dir():
        with contextlib.suppress(Exception):
            git_repo.checkout_detach(worktree_path)

    try:
        git_pr.merge_pr(repo_root=repo_root, pr_number=pr_number, strategy="squash")
    except Exception as e:
        if worktree_path and worktree_path.is_dir():
            with contextlib.suppress(Exception):
                git_repo.checkout(worktree_path, branch)
        logger.error("pr_merge.failed", pr_number=pr_number, error=str(e))
        console.error(f"PR merge failed: {e}")
        console.hint(f"Branch '{branch}' preserved — retry or clean up manually.")
        return

    # Remove the worktree only after a successful merge.
    if worktree_path:
        console.step(f"Removing worktree: {worktree_path.name}")
        with contextlib.suppress(Exception):
            git_worktree.remove_worktree(repo_root, worktree_path)
        with contextlib.suppress(Exception):
            git_worktree.prune_worktrees(repo_root)
        console.success(f"Removed {worktree_path.name}")

    _pull_main_after_merge(repo_root)

    if issue_number:
        with contextlib.suppress(Exception):
            provider.close_task(str(issue_number))


def _post_work_lifecycle_direct(
    repo_root: Path,
    branch: str,
    issue_number: str | int | None,
    worktree_path: Path | None,
    config: ProjectConfig,
    provider: AbstractTaskProvider,
) -> None:
    main_branch = config.project.main_branch or "main"
    try:
        ahead = git_branch.commits_ahead(repo_root, branch, main_branch)
    except GitError:
        console.warn("Could not determine commit count; skipping post-work lifecycle.")
        return

    if ahead == 0:
        if not prompts.confirm("Branch has no new commits. Delete empty worktree?", default=False):
            return
        if worktree_path:
            _cleanup_worktree(repo_root, worktree_path, main_branch)
        return

    choices = ["Merge into main", "Merge + close task", "Skip"]
    idx = prompts.select(f"Branch '{branch}' has {ahead} commit(s). What next?", choices)
    choice = choices[idx]
    if choice == "Skip":
        return

    try:
        git_repo.merge_squash(repo_root, branch)
        git_repo.commit_no_edit(repo_root)
        git_repo.push(repo_root)
    except (GitError, Exception) as e:
        logger.error("direct_merge.failed", branch=branch, error=str(e))
        return

    if worktree_path:
        _cleanup_worktree(repo_root, worktree_path, main_branch)

    if choice == "Merge + close task" and issue_number:
        with contextlib.suppress(Exception):
            provider.close_task(str(issue_number))


# ---------------------------------------------------------------------------
# Work start
# ---------------------------------------------------------------------------


def start(
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
    """Start a work session on an issue.

    Steps:
    1. Read the issue from the provider
    2. Create worktree and branch
    3. Bootstrap worktree (copy files, hooks, issue context)
    4. Resolve model from complexity
    5. Build work prompt and pass it as initial message to the AI tool
    6. Launch AI tool (or print path if cd_only / detach)
    7. Post-exit processing

    Args:
        target: Issue number or plan file path.
        ai_tool: AI tool to use (overrides config).
        model: Model to use (overrides config + complexity mapping).
        project_root: Repository root (defaults to CWD).
        detach: If True, launch AI in a new terminal tab.
        cd_only: If True, create worktree and print path only (no AI launch).

    Returns:
        True on success, False on failure.
    """
    config = load_config(project_root)
    provider = get_provider(config)

    # Resolve repo root
    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return False

    # When cd_only, redirect all status output to stderr so stdout stays
    # clean for the machine-readable worktree path.
    _original_out = console.out
    if cd_only:
        console.out = console.err
    try:
        # Read the issue
        task = _resolve_task_target(target, provider, config)
        if not task:
            return False

        console.rule(f"implement-task #{task.id}")
        console.kv("Issue", console.issue_ref(task.id, task.title))

        # Resolve AI tool and model
        resolved_tool = resolve_ai_tool(ai_tool, config, "work")
        resolved_model = resolve_model(
            model,
            config,
            "work",
            tool=resolved_tool,
            complexity=task.complexity.value if task.complexity else None,
        )

        if task.complexity:
            console.kv("Complexity", task.complexity.value)

        # Offer interactive confirmation (skipped when cd_only or both flags explicit).
        if not cd_only:
            resolved_tool, resolved_model = confirm_ai_selection(
                resolved_tool,
                resolved_model,
                tool_explicit=ai_explicit,
                model_explicit=model_explicit,
            )

        # Resolve main branch
        main_branch = config.project.main_branch or git_repo.detect_main_branch(repo_root)

        # Generate deterministic branch name
        branch_name = git_branch.make_branch_name(
            config.project.branch_prefix,
            int(task.id),
            task.title,
        )

        worktrees_dir = _resolve_worktrees_dir(config, repo_root)
        repo_name = repo_root.name
        worktree_path = worktrees_dir / repo_name / branch_name.replace("/", "-")

        # Check for existing draft PR (from plan-task flow)
        existing_pr = git_pr.get_pr_for_branch(repo_root, branch_name)
        plan_content: str | None = None

        if existing_pr:
            console.info(f"Found existing PR #{existing_pr['number']} for this task")
            # Extract plan content from PR body
            pr_body = git_pr.get_pr_body(repo_root, int(existing_pr["number"]))
            if pr_body:
                plan_content = extract_plan_from_pr_body(pr_body)
                if plan_content:
                    console.detail("Plan content extracted from draft PR")
        else:
            # No draft PR — warn and prompt
            console.warn("This task has no plan attached.")
            if prompts.is_tty():
                choices = ["Plan first (recommended)", "Proceed without plan"]
                idx = prompts.select("How would you like to proceed?", choices)
                if idx == 0:
                    from wade.services.plan_service import plan as do_plan

                    return do_plan(issue_id=task.id, project_root=project_root)
            # Proceed: bootstrap a draft PR with the issue body
            console.step("Bootstrapping draft PR...")
            pr_info = bootstrap_draft_pr(
                issue_number=task.id,
                issue_title=task.title,
                plan_body=task.body or f"Implements #{task.id}: {task.title}",
                config=config,
                repo_root=repo_root,
            )
            if pr_info:
                console.success(f"Draft PR #{pr_info.get('number')}: {pr_info.get('url')}")
            else:
                console.warn("Could not create draft PR — proceeding anyway")

        # Reuse the worktree if the branch already exists (idempotent re-run)
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
            console.info(f"Reusing existing worktree: {worktree_path}")
        elif existing_pr:
            # Draft PR exists → branch already exists remotely, check it out
            try:
                # Ensure local branch tracks remote
                if not git_branch.branch_exists(repo_root, branch_name):
                    git_repo.fetch_ref(repo_root, "origin", f"{branch_name}:{branch_name}")
                with console.status("Creating worktree..."):
                    git_worktree.checkout_existing_branch_worktree(
                        repo_root=repo_root,
                        branch_name=branch_name,
                        worktree_dir=worktree_path,
                    )
                console.kv("Worktree", str(branch_name))
                console.kv("Path", str(worktree_path))
            except GitError as e:
                console.error(f"Failed to create worktree: {e}")
                return False
        else:
            try:
                with console.status("Creating worktree..."):
                    if git_branch.branch_exists(repo_root, branch_name):
                        # Branch exists locally but no worktree — reuse it
                        git_worktree.checkout_existing_branch_worktree(
                            repo_root=repo_root,
                            branch_name=branch_name,
                            worktree_dir=worktree_path,
                        )
                    else:
                        git_worktree.create_worktree(
                            repo_root=repo_root,
                            branch_name=branch_name,
                            worktree_dir=worktree_path,
                            base_branch=main_branch,
                        )
                console.kv("Worktree", str(branch_name))
                console.kv("Path", str(worktree_path))
            except GitError as e:
                console.error(f"Failed to create worktree: {e}")
                return False

        console.empty()

        # Bootstrap
        write_plan_md(worktree_path, task, plan_content=plan_content)
        bootstrap_worktree(worktree_path, config, repo_root)

        # Add in-progress label and move to in-progress on project board (both non-critical)
        with contextlib.suppress(Exception):
            add_in_progress_label(provider, task.id)
        with contextlib.suppress(Exception):
            provider.move_to_in_progress(task.id)

        # Build work prompt
        prompt = build_work_prompt(task, resolved_tool, has_plan=bool(plan_content))
        snippet = "\n".join(prompt.splitlines()[:5]) + "\n…"
        console.panel(snippet, title="Work Prompt (preview)")

        # cd_only mode: just print the worktree path and return (no title, no AI)
        if cd_only:
            print(str(worktree_path))
            return True

        # AI-initiated start guard: if we're inside an AI CLI session,
        # don't launch another AI tool — just print the worktree path.
        detected_env = _detect_ai_cli_env()
        if detected_env:
            logger.info(
                "work.ai_launch_skipped",
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
        set_terminal_title(work_title)
        start_title_keeper(work_title)

        # Set up transcript capture
        transcript_path: Path | None = None
        try:
            transcript_dir = tempfile.mkdtemp(prefix="wade-work-")
            transcript_path = Path(transcript_dir) / f"transcript-{task.id}.log"
            console.hint(f"Transcript: {transcript_path}")
        except OSError:
            logger.warning("work.transcript_dir_failed")

        # Detach mode: launch AI tool in a new terminal, don't block
        if detach and resolved_tool:
            try:
                detach_adapter = AbstractAITool.get(AIToolID(resolved_tool))
                deliver_prompt_if_needed(detach_adapter, prompt)
                cmd = detach_adapter.build_launch_command(
                    model=resolved_model,
                    trusted_dirs=[str(worktree_path), tempfile.gettempdir()],
                    initial_message=prompt,
                    allowed_commands=config.permissions.allowed_commands,
                )
            except (ValueError, KeyError):
                cmd = [resolved_tool]

            console.step(f"Launching {resolved_tool} in new terminal...")
            if launch_in_new_terminal(cmd, cwd=str(worktree_path), title=work_title):
                console.success(f"Detached AI session for #{task.id}")
                stop_title_keeper()
                return True
            console.warn("Could not launch in new terminal — falling back to inline")
            # Fall through to inline launch below

        # Launch AI tool (inline)
        if not detach and resolved_tool:
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
                    allowed_commands=config.permissions.allowed_commands,
                )
                launch_completed = True
                logger.info("work.ai_exited", exit_code=exit_code, tool=resolved_tool)

                # Non-blocking tools (VS Code, Antigravity) return immediately.
                # Wait for the user to confirm they're done before post-session steps.
                if not adapter.capabilities().blocks_until_exit:
                    console.empty()
                    if not prompts.confirm("Have you finished the session?", default=True):
                        console.info("Worktree preserved — run 'wade work done' when ready.")
                        launch_completed = False
            except (ValueError, KeyError):
                console.warn(f"Unknown AI tool: {resolved_tool}")
            except Exception as e:
                console.warn(f"AI tool launch failed: {e}")
            finally:
                stop_title_keeper()

                # Capture token usage BEFORE lifecycle (merge/cleanup) to ensure
                # the PR is still open and the branch still exists.
                # Skip for non-blocking tools — they don't produce transcripts.
                if (
                    adapter is not None
                    and launch_completed
                    and adapter.capabilities().blocks_until_exit
                ):
                    detected_model = _capture_post_session_usage(
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
                    try:
                        _post_work_lifecycle(
                            repo_root=repo_root,
                            branch=branch_name,
                            issue_number=task.id,
                            worktree_path=worktree_path,
                            config=config,
                            provider=provider,
                        )
                    except Exception:
                        logger.exception("post_work_lifecycle.failed")

            # Use CLI-resolved model, falling back to transcript-detected model.
            effective_model = resolved_model or detected_model
            try:
                add_worked_by_labels(provider, task.id, resolved_tool, effective_model)
            except Exception as e:
                console.warn(f"Could not apply worked-by labels: {e}")
                logger.warning("work.worked_by_labels_failed", error=str(e))
        elif not resolved_tool:
            console.info("No AI tool configured. Worktree ready for manual work.")
            console.detail(f"cd {worktree_path}")
            stop_title_keeper()

        lines = []
        lines.append(f"  Worktree   {console.git_ref(branch_name)}")
        lines.append(f"  Issue      {console.issue_ref(task.id, task.title)}")
        console.panel("\n".join(lines), title="Work session complete")

        return True
    finally:
        console.out = _original_out


def _resolve_task_target(
    target: str,
    provider: AbstractTaskProvider,
    config: ProjectConfig,
) -> Task | None:
    """Resolve a target (issue number or plan file) to a Task.

    If the target is a path to a plan file, create the issue first.
    """
    # Check if target is a file path
    target_path = Path(target).expanduser()
    if target_path.is_file():
        from wade.services.task_service import create_from_plan_file

        console.info(f"Creating issue from plan file: {target}")
        task = create_from_plan_file(target_path, config=config, provider=provider)
        return task

    # Treat as issue number
    try:
        task = provider.read_task(target)
        return task
    except Exception as e:
        console.error(f"Could not read issue #{target}: {e}")
        return None


# ---------------------------------------------------------------------------
# Work batch
# ---------------------------------------------------------------------------


_BATCH_STAGGER_SECS = 1.0
"""Delay between terminal spawns to avoid git index.lock contention."""


def batch(
    issue_numbers: list[str],
    ai_tool: str | None = None,
    model: str | None = None,
    project_root: Path | None = None,
    *,
    ai_explicit: bool = False,
    model_explicit: bool = False,
) -> bool:
    """Start parallel work sessions for multiple issues.

    Independent issues launch in parallel terminals.
    Dependent chains: only the first issue in each chain is launched; the
    remaining chain members are printed in order for manual sequential
    execution (one cannot work on a dependent issue before its blocker is done).
    """
    # Deduplicate while preserving order
    issue_numbers = list(dict.fromkeys(issue_numbers))

    config = load_config(project_root)
    cwd = project_root or Path.cwd()

    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error("Not inside a git repository")
        return False

    console.rule(f"work batch ({len(issue_numbers)} issues)")

    # Resolve AI tool and model, then offer interactive confirmation.
    resolved_tool = resolve_ai_tool(ai_tool, config, "work")
    resolved_model = resolve_model(model, config, "work", tool=resolved_tool)
    resolved_tool, resolved_model = confirm_ai_selection(
        resolved_tool,
        resolved_model,
        tool_explicit=ai_explicit,
        model_explicit=model_explicit,
    )

    # Check for dependency ordering
    # Try to load deps from issue bodies (look for "Depends on" references)
    graph = _build_graph_from_issues(issue_numbers, config)

    if graph and graph.edges:
        independent, chains = graph.partition(issue_numbers)
        console.info(f"Dependency analysis: {len(independent)} independent, {len(chains)} chain(s)")
    else:
        independent = issue_numbers
        chains = []

    launched = 0

    def _launch(issue_id: str, label: str) -> bool:
        """Build command and launch a single issue in a new terminal."""
        nonlocal launched
        cmd = ["wade", "implement-task", issue_id]
        if resolved_tool:
            cmd.extend(["--ai", resolved_tool])
        if resolved_model:
            cmd.extend(["--model", resolved_model])

        console.step(f"Launching #{issue_id} ({label}) in new terminal")
        if launch_in_new_terminal(cmd, cwd=str(repo_root), title=f"wade #{issue_id}"):
            launched += 1
            return True
        console.warn(f"Could not launch terminal for #{issue_id}")
        return False

    # Launch independent issues in staggered terminals
    for i, issue_id in enumerate(independent):
        if i > 0:
            time.sleep(_BATCH_STAGGER_SECS)
        _launch(issue_id, "independent")

    # Launch chains: start only the first item, list the rest in order
    for chain in chains:
        console.info(f"Dependency chain: {' → '.join(f'#{n}' for n in chain)}")
        if launched > 0:
            time.sleep(_BATCH_STAGGER_SECS)
        _launch(chain[0], "first in chain")

        if len(chain) > 1:
            remaining = ", ".join(f"#{n}" for n in chain[1:])
            console.info(f"After completing #{chain[0]}, work on these in order: {remaining}")

    console.panel(f"  Launched {launched} work session(s)", title="Batch started")
    return launched > 0


def _build_graph_from_issues(
    issue_numbers: list[str],
    config: ProjectConfig,
) -> DependencyGraph | None:
    """Try to build a dependency graph from issue body cross-references."""
    from wade.models.deps import DependencyEdge, DependencyGraph
    from wade.models.task import parse_dependency_refs

    provider = get_provider(config)
    edges: list[DependencyEdge] = []
    valid_set = set(issue_numbers)

    for num in issue_numbers:
        try:
            task = provider.read_task(num)
        except Exception:
            logger.debug("batch.issue_read_failed", issue_num=num, exc_info=True)
            continue

        refs = parse_dependency_refs(task.body)
        for dep_id in refs["depends_on"]:
            if dep_id in valid_set:
                edges.append(DependencyEdge(from_task=dep_id, to_task=num))

    if edges:
        return DependencyGraph(edges=edges)
    return None


# ---------------------------------------------------------------------------
# Work cd
# ---------------------------------------------------------------------------


def find_worktree_path(
    target: str,
    project_root: Path | None = None,
) -> Path | None:
    """Find the worktree path for a given issue number or branch name."""
    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        return None

    worktrees = git_worktree.list_worktrees(repo_root)

    for wt in worktrees:
        wt_branch = wt.get("branch", "")
        wt_path = wt.get("path", "")

        # Match by issue number in branch name
        if f"/{target}-" in wt_branch or wt_branch.endswith(f"/{target}"):
            return Path(wt_path)

        # Match by worktree directory name (boundary-aware to avoid
        # target="1" matching "feat-10-something")
        if re.search(rf"(?:^|-){re.escape(target)}(?:-|$)", Path(wt_path).name):
            return Path(wt_path)

    return None


def _resolve_worktree_from_plan(
    plan_file: Path,
    project_root: Path | None = None,
) -> tuple[Path, str, str | None]:
    if not plan_file.is_file():
        raise ValueError(f"Plan file '{plan_file}' not found.")

    first_line = plan_file.read_text(encoding="utf-8").split("\n", 1)[0].strip()
    match = re.match(r"^#\s+(.+)", first_line)
    if not match:
        raise ValueError(
            "Plan file must start with a '# Title' heading to derive the worktree name."
        )
    title = match.group(1).strip()

    from wade.utils.slug import slugify

    slug = slugify(title, max_length=50)

    wt_path = find_worktree_path(slug, project_root=project_root)
    if not wt_path:
        raise ValueError(
            f"No worktree found matching plan title '{title}' (slug: '{slug}'). "
            "Check active worktrees with: wade work list"
        )

    branch = git_repo.get_current_branch(wt_path)
    issue_number = extract_issue_from_branch(branch)

    return wt_path, branch, issue_number


# ---------------------------------------------------------------------------
# Branch / issue helpers
# ---------------------------------------------------------------------------


def extract_issue_from_branch(branch: str) -> str | None:
    """Extract the issue number from a branch name like ``feat/42-slug``."""
    m = re.search(r"/(\d+)", branch)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Staleness classification
# ---------------------------------------------------------------------------


def classify_staleness(
    repo_root: Path,
    branch: str,
    main_branch: str,
    issue_number: str | None = None,
    provider: AbstractTaskProvider | None = None,
) -> WorktreeState:
    """Classify a worktree's staleness.

    Returns one of:
    - ACTIVE — issue is open or could not determine
    - STALE_EMPTY — no commits ahead of main
    - STALE_MERGED — branch merged into main
    - STALE_REMOTE_GONE — remote tracking branch deleted
    """
    # 1. If issue number, check issue state
    if issue_number and provider:
        try:
            task = provider.read_task(issue_number)
            from wade.models.task import TaskState

            if task.state == TaskState.OPEN:
                return WorktreeState.ACTIVE
        except Exception:
            logger.debug("staleness.issue_read_failed", issue=issue_number, exc_info=True)
            # Can't read issue — treat as active (fail-safe)
            return WorktreeState.ACTIVE

    # 2. Count commits ahead of main
    try:
        ahead = git_branch.commits_ahead(repo_root, branch, main_branch)
    except GitError:
        return WorktreeState.ACTIVE

    if ahead == 0:
        return WorktreeState.STALE_EMPTY

    # 3. Check if merged (merge-base equals branch tip)
    try:
        mb = git_repo.merge_base(repo_root, branch, main_branch)
        tip = git_repo.rev_parse(repo_root, branch)
        if mb == tip:
            return WorktreeState.STALE_MERGED
    except GitError:
        logger.debug("staleness.merge_base_check_failed", exc_info=True)

    # 4. Check if remote tracking branch gone
    try:
        tracking = git_repo.upstream_tracking_status(repo_root, branch)
        if tracking == "gone":
            return WorktreeState.STALE_REMOTE_GONE
    except GitError:
        logger.debug("staleness.remote_tracking_check_failed", exc_info=True)

    return WorktreeState.ACTIVE


# ---------------------------------------------------------------------------
# Implementation usage block (for PR bodies)
# ---------------------------------------------------------------------------


def _build_session_usage_table(
    ai_tool: str | None = None,
    model: str | None = None,
    token_usage: TokenUsage | None = None,
) -> str:
    """Build a single-session markdown usage table (no markers or headings).

    Generates the table rows for one session, used by both impl and review
    usage block builders.
    """
    from wade.ai_tools.transcript import format_count

    breakdown = token_usage.model_breakdown if token_usage else []
    multi = len(breakdown) > 1

    lines: list[str] = []

    if multi:
        names = [row.model for row in breakdown]
        n = len(names)
        header = "| Metric | Total | " + " | ".join(f"`{m}`" for m in names) + " |"
        sep = "| " + " | ".join(["---"] * (2 + n)) + " |"
        empty = " |" * n

        lines.extend([header, sep])

        if ai_tool:
            lines.append(f"| Tool | `{ai_tool}` |{empty}")

        has_tokens = token_usage and token_usage.total_tokens and token_usage.total_tokens > 0
        if has_tokens:
            assert token_usage is not None  # for type narrowing

            def per(attr: str) -> str:
                return " | ".join(f"**{format_count(getattr(r, attr))}**" for r in breakdown)

            per_total = " | ".join(
                f"**{format_count((r.input_tokens or 0) + (r.output_tokens or 0) + (r.cached_tokens or 0))}**"  # noqa: E501
                for r in breakdown
            )
            lines.append(
                f"| Total tokens | **{format_count(token_usage.total_tokens)}** | {per_total} |"
            )
            if token_usage.input_tokens:
                inp_total = format_count(token_usage.input_tokens)
                lines.append(f"| Input tokens | **{inp_total}** | {per('input_tokens')} |")
            if token_usage.output_tokens:
                out_total = format_count(token_usage.output_tokens)
                lines.append(f"| Output tokens | **{out_total}** | {per('output_tokens')} |")
            if token_usage.cached_tokens:
                cac_total = format_count(token_usage.cached_tokens)
                lines.append(f"| Cached tokens | **{cac_total}** | {per('cached_tokens')} |")
        else:
            lines.append(f"| Total tokens | *unavailable* |{empty}")

        if token_usage and token_usage.premium_requests and token_usage.premium_requests > 0:
            per_prem = " | ".join(
                f"**{r.premium_requests}**" if r.premium_requests else "" for r in breakdown
            )
            lines.append(
                f"| Premium requests (est.) | **{token_usage.premium_requests}** | {per_prem} |"
            )

    else:
        lines.extend(["| Metric | Value |", "| --- | --- |"])

        if ai_tool:
            lines.append(f"| Tool | `{ai_tool}` |")
        if model:
            lines.append(f"| Model | `{model}` |")

        has_tokens = token_usage and token_usage.total_tokens and token_usage.total_tokens > 0
        if has_tokens:
            assert token_usage is not None  # for type narrowing
            lines.append(f"| Total tokens | **{format_count(token_usage.total_tokens)}** |")
            if token_usage.input_tokens:
                lines.append(f"| Input tokens | **{format_count(token_usage.input_tokens)}** |")
            if token_usage.output_tokens:
                lines.append(f"| Output tokens | **{format_count(token_usage.output_tokens)}** |")
            if token_usage.cached_tokens:
                lines.append(f"| Cached tokens | **{format_count(token_usage.cached_tokens)}** |")
        else:
            lines.append("| Total tokens | *unavailable* |")

        if token_usage and token_usage.premium_requests and token_usage.premium_requests > 0:
            lines.append(f"| Premium requests (est.) | **{token_usage.premium_requests}** |")

    return "\n".join(lines)


def _count_sessions(block_content: str) -> int:
    """Count ``### Session N`` occurrences in a marker block's inner content."""
    return len(re.findall(r"^### Session \d+", block_content, re.MULTILINE))


def _append_usage_entry(
    body: str,
    ai_tool: str | None,
    model: str | None,
    token_usage: TokenUsage | None,
    start_marker: str,
    end_marker: str,
    heading: str,
) -> str:
    """Append a new session entry to a usage marker block.

    If the block doesn't exist, creates a fresh block with ``### Session 1``.
    If the block exists with N sessions, appends ``### Session N+1``.
    """
    from wade.utils.markdown import extract_marker_block

    existing = extract_marker_block(body, start_marker, end_marker)
    table = _build_session_usage_table(ai_tool=ai_tool, model=model, token_usage=token_usage)

    if existing is None:
        # Fresh block
        lines = [
            start_marker,
            "",
            f"## {heading}",
            "",
            "### Session 1",
            "",
            table,
            "",
            end_marker,
        ]
        block = "\n".join(lines)
        stripped = body.rstrip("\n")
        return stripped + "\n\n" + block + "\n" if stripped else block + "\n"

    # Existing block — count sessions and append
    n = _count_sessions(existing)

    if n == 0 and existing.strip():
        # Old format (no ### Session headings) — wrap old content as Session 1
        new_inner = f"### Session 1\n\n{existing.strip()}\n\n### Session 2\n\n{table}"
    else:
        new_session = f"### Session {n + 1}\n\n{table}"
        new_inner = existing.rstrip("\n") + "\n\n" + new_session

    # Rebuild: remove old block, construct new one with appended session
    cleaned = remove_marker_block(body, start_marker, end_marker)
    new_block = f"{start_marker}\n\n{new_inner}\n\n{end_marker}"
    stripped = cleaned.rstrip("\n")
    return stripped + "\n\n" + new_block + "\n" if stripped else new_block + "\n"


def append_impl_usage_entry(
    body: str,
    ai_tool: str | None = None,
    model: str | None = None,
    token_usage: TokenUsage | None = None,
) -> str:
    """Append an implementation usage session entry to the body."""
    return _append_usage_entry(
        body,
        ai_tool=ai_tool,
        model=model,
        token_usage=token_usage,
        start_marker=IMPL_USAGE_MARKER_START,
        end_marker=IMPL_USAGE_MARKER_END,
        heading="Token Usage (Implementation)",
    )


def append_review_usage_entry(
    body: str,
    ai_tool: str | None = None,
    model: str | None = None,
    token_usage: TokenUsage | None = None,
) -> str:
    """Append a review usage session entry to the body."""
    return _append_usage_entry(
        body,
        ai_tool=ai_tool,
        model=model,
        token_usage=token_usage,
        start_marker=REVIEW_USAGE_MARKER_START,
        end_marker=REVIEW_USAGE_MARKER_END,
        heading="Token Usage (Review)",
    )


def build_impl_usage_block(
    ai_tool: str | None = None,
    model: str | None = None,
    token_usage: TokenUsage | None = None,
) -> str:
    """Build the ## Token Usage (Implementation) section for PR body.

    Wraps ``_build_session_usage_table`` with markers and a ``### Session 1``
    header.
    """
    table = _build_session_usage_table(ai_tool=ai_tool, model=model, token_usage=token_usage)
    lines = [
        IMPL_USAGE_MARKER_START,
        "",
        "## Token Usage (Implementation)",
        "",
        "### Session 1",
        "",
        table,
        "",
        IMPL_USAGE_MARKER_END,
    ]
    return "\n".join(lines)


def _strip_impl_usage_block(body: str) -> str:
    """Remove existing implementation usage block from body (idempotent)."""
    return remove_marker_block(body, IMPL_USAGE_MARKER_START, IMPL_USAGE_MARKER_END)


def build_review_usage_block(
    ai_tool: str | None = None,
    model: str | None = None,
    token_usage: TokenUsage | None = None,
) -> str:
    """Build the ## Token Usage (Review) section for PR/issue body.

    Wraps ``_build_session_usage_table`` with review markers and a
    ``### Session 1`` header.
    """
    table = _build_session_usage_table(ai_tool=ai_tool, model=model, token_usage=token_usage)
    lines = [
        REVIEW_USAGE_MARKER_START,
        "",
        "## Token Usage (Review)",
        "",
        "### Session 1",
        "",
        table,
        "",
        REVIEW_USAGE_MARKER_END,
    ]
    return "\n".join(lines)


def _strip_review_usage_block(body: str) -> str:
    """Remove existing review usage block from body (idempotent)."""
    return remove_marker_block(body, REVIEW_USAGE_MARKER_START, REVIEW_USAGE_MARKER_END)


def _strip_summary_section(body: str) -> str:
    """Remove an existing ``## Summary`` section from a PR body.

    The body may contain an implementation-usage block delimited by HTML
    comment markers.  We use that marker as a hard boundary so freeform
    summary content (which may itself contain ``## `` subheadings) is fully
    removed without eating into the impl-usage block.  The caller then
    re-inserts the new summary *before* any impl-usage block.
    """
    idx = body.find("\n## Summary\n")
    if idx == -1:
        # Also check at the very start of the string
        if body.startswith("## Summary\n"):
            idx = 0
        else:
            return body

    before = body[:idx]

    # Find the next structural boundary after the summary heading.
    # Prefer the impl-usage HTML marker; fall back to the next ## heading.
    marker_idx = body.find(IMPL_USAGE_MARKER_START, idx)
    if marker_idx != -1:
        after = body[marker_idx:]
    else:
        # No impl-usage marker — look for next ## heading after the summary title
        summary_title_end = idx + len("\n## Summary\n")
        if body.startswith("## Summary\n"):
            summary_title_end = len("## Summary\n")
        next_heading = re.search(r"(?:^|\n)## ", body[summary_title_end:])
        after = body[summary_title_end + next_heading.start() :] if next_heading else ""

    result = before.rstrip("\n")
    if after:
        result = result + "\n\n" + after
    return result if result else ""


def _apply_pr_refs(
    body: str,
    issue_number: str,
    close_issue: bool,
    parent_issue: str | None,
) -> str:
    """Add or update Closes/Part-of references in a PR body.

    Idempotent: repeated calls do not duplicate references.
    """
    updated = body

    # Add "Closes #N" if requested and not already present
    if close_issue:
        close_pattern = rf"^Closes\s+#{re.escape(issue_number)}\b"
        if not re.search(close_pattern, updated, flags=re.MULTILINE):
            # Strip existing "Implements #N" line when upgrading to "Closes #N"
            updated = re.sub(
                rf"^Implements\s+#{re.escape(issue_number)}\s*\n?",
                "",
                updated,
                flags=re.MULTILINE,
            )
            updated = f"Closes #{issue_number}\n\n" + updated.lstrip("\n")

    # Add "Part of #parent" if detected and not already present
    if parent_issue:
        parent_pattern = rf"^Part of\s+#{re.escape(parent_issue)}\b"
        if not re.search(parent_pattern, updated, flags=re.MULTILINE):
            updated = f"Part of #{parent_issue}\n" + updated

    return updated


# ---------------------------------------------------------------------------
# PR body composition
# ---------------------------------------------------------------------------


def _build_pr_body(
    task: Task,
    pr_summary_path: Path | None = None,
    close_issue: bool = True,
    parent_issue: str | None = None,
) -> str:
    """Compose the PR body.

    Order:
    1. Part of #parent (if detected)
    2. Closes #N
    3. ## Summary (from PR-SUMMARY file)

    Plan summary stays on the issue only — not copied into the PR body.
    """
    lines: list[str] = []

    if parent_issue:
        lines.append(f"Part of #{parent_issue}")
    if close_issue:
        lines.append(f"Closes #{task.id}")

    if lines:
        lines.append("")

    # PR summary from file
    if pr_summary_path and pr_summary_path.is_file():
        summary_content = pr_summary_path.read_text(encoding="utf-8").strip()
        if summary_content:
            lines.append("## Summary")
            lines.append("")
            lines.append(summary_content)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Work sync
# ---------------------------------------------------------------------------


def sync(
    dry_run: bool = False,
    main_branch: str | None = None,
    json_output: bool = False,
    project_root: Path | None = None,
) -> SyncResult:
    """Sync current branch with main.

    Flow:
    1. Pre-flight checks (in git repo, not on main, clean worktree)
    2. Fetch origin
    3. Count commits behind
    4. Merge (unless dry-run or up-to-date)
    5. Emit structured events
    """
    config = load_config(project_root)
    cwd = project_root or Path.cwd()
    events: list[SyncEvent] = []

    def emit(event: SyncEventType, **data: str | int) -> None:
        ev = SyncEvent(event=event, data=data)
        events.append(ev)
        if json_output:
            console.raw(json.dumps({"event": event, **data}))

    # Pre-flight checks
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        emit(SyncEventType.ERROR, reason="not_git_repo")
        return SyncResult(
            success=False,
            current_branch="",
            main_branch=main_branch or "",
            events=events,
        )

    try:
        current = git_repo.get_current_branch(cwd)
    except GitError:
        emit(SyncEventType.ERROR, reason="detached_head")
        return SyncResult(
            success=False,
            current_branch="",
            main_branch=main_branch or "",
            events=events,
        )

    resolved_main = main_branch or config.project.main_branch
    if not resolved_main:
        try:
            resolved_main = git_repo.detect_main_branch(repo_root)
        except GitError:
            emit(SyncEventType.ERROR, reason="no_main_branch")
            return SyncResult(
                success=False,
                current_branch=current,
                main_branch="",
                events=events,
            )

    if current == resolved_main:
        emit(SyncEventType.ERROR, reason="on_main_branch")
        return SyncResult(
            success=False,
            current_branch=current,
            main_branch=resolved_main,
            events=events,
        )

    # Check clean — with detailed diagnostics
    if not git_repo.is_clean(cwd):
        detail_str = _format_uncommitted_summary(cwd)
        emit(SyncEventType.ERROR, reason="dirty_worktree", details=detail_str)
        if not json_output:
            console.error_with_fix(
                f"Working tree is dirty ({detail_str})",
                "Commit or stash your changes first",
                "git stash",
            )
        return SyncResult(
            success=False,
            current_branch=current,
            main_branch=resolved_main,
            events=events,
        )

    emit(SyncEventType.PREFLIGHT_OK, current_branch=current, main_branch=resolved_main)
    if not json_output:
        console.step(f"Syncing {current} with {resolved_main}")

    # Fetch
    merge_ref = resolved_main
    try:
        has_remote = git_repo.has_remote(repo_root)
    except GitError:
        has_remote = False

    if has_remote:
        try:
            git_sync.fetch_origin(repo_root)
            merge_ref = f"origin/{resolved_main}"
            if not json_output:
                console.detail(f"Fetched latest from origin/{resolved_main}")
        except GitError:
            if not json_output:
                console.warn("Fetch failed; using local main")

    # Count commits behind
    try:
        behind = git_branch.commits_ahead(repo_root, merge_ref, current)
    except GitError:
        behind = 0

    if behind == 0:
        emit(SyncEventType.UP_TO_DATE, branch=current, main=resolved_main)
        if not json_output:
            console.success("Already up to date.")
        emit(SyncEventType.DONE, branch=current, main=resolved_main)
        return SyncResult(
            success=True,
            current_branch=current,
            main_branch=resolved_main,
            events=events,
        )

    if dry_run:
        emit(SyncEventType.DRY_RUN, action="merge_main_into_feature", behind=behind)
        if not json_output:
            console.info(f"Dry run: {behind} commit(s) would be merged.")
        emit(SyncEventType.DONE, branch=current, main=resolved_main)
        return SyncResult(
            success=True,
            current_branch=current,
            main_branch=resolved_main,
            events=events,
        )

    # Merge
    if not json_output:
        console.step(f"Merging {merge_ref} ({behind} commit(s) behind)")

    merge_result = git_sync.merge_branch(repo_root, merge_ref)

    if merge_result.success:
        emit(SyncEventType.MERGED, commits_merged=behind)
        if not json_output:
            console.success(f"Merged {behind} commit(s).")
        emit(SyncEventType.DONE, branch=current, main=resolved_main)
        return SyncResult(
            success=True,
            current_branch=current,
            main_branch=resolved_main,
            commits_merged=behind,
            events=events,
        )

    # Conflicts
    conflicts = merge_result.conflicts
    emit(SyncEventType.CONFLICT, source=resolved_main, target=current, files="\n".join(conflicts))
    if not json_output:
        console.error(f"Merge conflict in {len(conflicts)} file(s):")
        for f in conflicts:
            console.detail(f)
        console.empty()
        console.hint("Resolve conflicts, then run:")
        console.out.print("      [prompt.dimmed]$ wade work sync[/]")

    # Get conflict diff
    try:
        diff_output = git_repo.diff_stat(repo_root)
        if diff_output.strip():
            emit(SyncEventType.CONFLICT_DIFF, diff=diff_output)
    except GitError:
        logger.debug("sync.conflict_diff_read_failed", exc_info=True)

    return SyncResult(
        success=False,
        current_branch=current,
        main_branch=resolved_main,
        conflicts=conflicts,
        events=events,
    )


# ---------------------------------------------------------------------------
# Work done
# ---------------------------------------------------------------------------


def done(
    target: str | None = None,
    plan_file: Path | None = None,
    no_close: bool = False,
    draft: bool = False,
    no_cleanup: bool = False,
    project_root: Path | None = None,
) -> bool:
    """Complete work session — create PR or merge directly.

    Detects current branch, extracts issue number, reads merge strategy
    from config, and delegates to _done_via_pr or _done_via_direct.

    Args:
        target: Optional issue number, worktree name, or plan file.
            If None, detects from current branch.
        no_close: Don't close the issue on merge.
        draft: Create PR as draft.
        no_cleanup: Don't remove worktree after merge (direct strategy).
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

    # Check clean
    if not git_repo.is_clean(cwd):
        detail_str = _format_uncommitted_summary(cwd)
        console.error_with_fix(
            f"Working tree is dirty ({detail_str})",
            "Commit or stash your changes first",
            "git stash",
        )
        return False

    main_branch = config.project.main_branch
    if not main_branch:
        try:
            main_branch = git_repo.detect_main_branch(repo_root)
        except GitError:
            console.error("Cannot detect main branch")
            return False

    console.rule(f"work done #{issue_number}")

    strategy = config.project.merge_strategy

    if strategy == MergeStrategy.DIRECT:
        return _done_via_direct(
            repo_root=repo_root,
            branch=branch,
            issue_number=issue_number,
            main_branch=main_branch,
            close_issue=not no_close,
            config=config,
            no_cleanup=no_cleanup,
            worktree_path=wt_path,
        )
    else:
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
    """Finalize work — update existing draft PR or create a new one.

    In the new workflow, a draft PR should already exist (created by plan-task
    or implement-task). This function:
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

    # Check for existing PR (expected from plan-task or implement-task bootstrap)
    existing_pr = git_pr.get_pr_for_branch(repo_root, branch)

    # Resolve PR-SUMMARY.md from worktree root
    pr_summary_path: Path | None = None
    if worktree_path and (worktree_path / "PR-SUMMARY.md").exists():
        pr_summary_path = worktree_path / "PR-SUMMARY.md"

    if pr_summary_path is None:
        console.warn("No PR-SUMMARY.md found — PR description will have no summary.")
        if worktree_path:
            console.detail(f"Expected: {worktree_path / 'PR-SUMMARY.md'}")

    if existing_pr:
        # Update existing PR: append summary
        pr_number = int(existing_pr["number"])
        pr_url = str(existing_pr.get("url", ""))
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
            logger.debug("work.parent_issue_detection_failed", exc_info=True)

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

        if git_pr.update_pr_body(repo_root, pr_number, updated_body):
            console.success("PR body updated with summary.")

        # Mark draft as ready
        is_draft = existing_pr.get("isDraft", False)
        if is_draft:
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
            logger.debug("work.parent_issue_detection_failed", exc_info=True)

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
    console.panel("\n".join(lines), title="Work done")

    return True


def _done_via_direct(
    repo_root: Path,
    branch: str,
    issue_number: str,
    main_branch: str,
    close_issue: bool,
    config: ProjectConfig,
    no_cleanup: bool = False,
    worktree_path: Path | None = None,
) -> bool:
    """Merge directly into main and clean up."""
    provider = get_provider(config)

    # Sync first
    console.step(f"Merging {main_branch} into {branch}...")
    with contextlib.suppress(GitError):
        git_sync.fetch_origin(repo_root)

    try:
        sync_cwd = worktree_path if worktree_path and worktree_path.is_dir() else repo_root
        merge_result = git_sync.merge_branch(sync_cwd, main_branch)
        if not merge_result.success:
            console.error("Merge conflicts detected. Resolve them first.")
            return False
    except GitError as e:
        console.error(f"Merge failed: {e}")
        return False

    # Switch to main, fast-forward to origin, and merge feature branch
    console.step(f"Merging {branch} into {main_branch}...")
    try:
        git_repo.checkout(repo_root, main_branch)
        git_repo.merge_ff_only(repo_root, f"origin/{main_branch}")
        git_repo.merge_no_edit(repo_root, branch)
        git_repo.push_branch(repo_root, main_branch)
        console.success("Merged and pushed.")
    except GitError as e:
        console.error(f"Direct merge failed: {e}")
        return False

    # Remove in-progress label
    with contextlib.suppress(Exception):
        remove_in_progress_label(provider, issue_number)

    # Close issue
    if close_issue:
        try:
            provider.close_task(issue_number)
            console.success(f"Closed #{issue_number}")
        except Exception as e:
            console.warn(f"Could not close issue #{issue_number}: {e}")

    # Cleanup worktree (unless --no-cleanup)
    if not no_cleanup:
        console.step("Cleaning up worktree...")
        try:
            if worktree_path:
                _cleanup_worktree(repo_root, worktree_path, main_branch)
            else:
                git_branch.delete_branch(repo_root, branch, force=True)
                git_worktree.prune_worktrees(repo_root)
            console.success("Worktree cleaned up.")
        except Exception as e:
            choice = prompts.select(
                f"Worktree cleanup failed: {e}. What would you like to do?",
                ["Retry", "Skip (leave worktree in place)"],
            )
            if choice == 0:  # Retry
                try:
                    if worktree_path:
                        _cleanup_worktree(repo_root, worktree_path, main_branch)
                    else:
                        git_branch.delete_branch(repo_root, branch, force=True)
                        git_worktree.prune_worktrees(repo_root)
                    console.success("Worktree cleaned up.")
                except Exception:
                    logger.warning("worktree.cleanup_skipped", reason="retry_failed", exc_info=True)
            else:  # Skip
                logger.warning("worktree.cleanup_skipped", reason="user_skipped")

    lines = []
    lines.append(f"  Branch   {console.git_ref(branch)} merged into {console.git_ref(main_branch)}")
    lines.append(f"  Issue    #{issue_number}")
    console.panel("\n".join(lines), title="Work done")

    return True


# ---------------------------------------------------------------------------
# Work list
# ---------------------------------------------------------------------------


def list_sessions(
    show_all: bool = False,
    json_output: bool = False,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    """List active work sessions / worktrees.

    Returns a list of dicts with worktree info (path, branch, issue, staleness).
    """
    config = load_config(project_root)
    cwd = project_root or Path.cwd()

    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error("Not inside a git repository")
        return []

    main_branch = config.project.main_branch
    if not main_branch:
        try:
            main_branch = git_repo.detect_main_branch(repo_root)
        except GitError:
            main_branch = "main"

    worktrees = git_worktree.list_worktrees(repo_root)
    sessions: list[dict[str, Any]] = []
    provider_inst = get_provider(config)

    # The first worktree is the main checkout — skip unless --all
    for i, wt in enumerate(worktrees):
        wt_branch = wt.get("branch", "")
        wt_path = wt.get("path", "")

        # Skip main checkout unless --all
        if i == 0 and not show_all:
            continue

        # Skip non-wade branches unless --all
        issue_number = extract_issue_from_branch(wt_branch)
        if not issue_number and not show_all:
            continue

        staleness = classify_staleness(
            repo_root=repo_root,
            branch=wt_branch,
            main_branch=main_branch,
            issue_number=issue_number,
            provider=provider_inst,
        )

        # Fetch issue state for display
        issue_state: str | None = None
        issue_title: str | None = None
        if issue_number:
            try:
                task_info = provider_inst.read_task(issue_number)
                issue_state = task_info.state.value
                issue_title = task_info.title
            except Exception:
                logger.debug("work.issue_read_failed", issue=issue_number, exc_info=True)

        # Count commits ahead
        try:
            ahead = git_branch.commits_ahead(repo_root, wt_branch, main_branch)
        except GitError:
            ahead = 0

        session_info = {
            "path": wt_path,
            "branch": wt_branch,
            "issue": issue_number,
            "issue_state": issue_state,
            "issue_title": issue_title,
            "staleness": staleness.value,
            "commits_ahead": ahead,
        }
        sessions.append(session_info)

    if json_output:
        console.raw(json.dumps(sessions, indent=2))
        return sessions

    if not sessions:
        console.info("No active wade worktrees found.")
        return sessions

    console.rule(f"Work sessions ({len(sessions)})")
    for s in sessions:
        staleness_label = s["staleness"].upper().replace("_", " ")
        issue_str = f"#{s['issue']}" if s["issue"] else "(no issue)"
        state_str = f" [{s['issue_state'].upper()}]" if s.get("issue_state") else ""
        title_str = f" {s['issue_title']}" if s.get("issue_title") else ""
        console.step(f"[{staleness_label}] {issue_str}{state_str}{title_str}")
        console.detail(f"Path: {s['path']}")
        console.detail(f"Branch: {s['branch']} ({s['commits_ahead']} commit(s) ahead)")

    return sessions


# ---------------------------------------------------------------------------
# Work remove
# ---------------------------------------------------------------------------


def remove(
    target: str | None = None,
    stale: bool = False,
    force: bool = False,
    project_root: Path | None = None,
) -> bool:
    """Remove a worktree.

    Modes:
    - target: remove a specific worktree by issue number or name
    - stale: remove all stale (non-active) worktrees
    """
    config = load_config(project_root)
    cwd = project_root or Path.cwd()

    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error("Not inside a git repository")
        return False

    main_branch = config.project.main_branch
    if not main_branch:
        try:
            main_branch = git_repo.detect_main_branch(repo_root)
        except GitError:
            main_branch = "main"

    if stale:
        return _remove_stale(repo_root, main_branch, force)

    if target:
        return _remove_target(repo_root, target, main_branch, force)

    console.error("Specify a target or use --stale")
    return False


def _remove_target(repo_root: Path, target: str, main_branch: str, force: bool = False) -> bool:
    """Remove a specific worktree by issue number or name."""
    wt_path = find_worktree_path(target, project_root=repo_root)
    if not wt_path:
        console.error(f"No worktree found for: {target}")
        return False

    if not force and not prompts.confirm(f"Remove worktree {wt_path.name}?"):
        return False

    return _cleanup_worktree(repo_root, wt_path, main_branch)


def _remove_stale(repo_root: Path, main_branch: str, force: bool) -> bool:
    """Remove all stale worktrees."""
    worktrees = git_worktree.list_worktrees(repo_root)
    stale_wts: list[dict[str, Any]] = []

    for i, wt in enumerate(worktrees):
        if i == 0:
            continue  # Skip main

        wt_branch = wt.get("branch", "")
        wt_path = wt.get("path", "")
        issue_number = extract_issue_from_branch(wt_branch)

        staleness = classify_staleness(
            repo_root=repo_root,
            branch=wt_branch,
            main_branch=main_branch,
            issue_number=issue_number,
        )

        if staleness != WorktreeState.ACTIVE:
            stale_wts.append(
                {
                    "path": wt_path,
                    "branch": wt_branch,
                    "staleness": staleness.value,
                }
            )

    if not stale_wts:
        console.info("No stale worktrees found.")
        return True

    console.rule(f"Stale worktrees ({len(stale_wts)})")
    for wt in stale_wts:
        console.step(f"[{wt['staleness'].upper()}] {wt['branch']}")
        console.detail(f"Path: {wt['path']}")

    if not force:
        console.info("Use --force to remove these worktrees.")
        return True

    removed = 0
    for wt in stale_wts:
        if _cleanup_worktree(repo_root, Path(wt["path"]), main_branch):
            removed += 1

    console.panel(f"  Removed {removed} stale worktree(s)", title="Stale cleanup")
    return removed > 0


def _cleanup_worktree(repo_root: Path, wt_path: Path, main_branch: str) -> bool:
    """Remove a single worktree and its branch."""
    console.step(f"Removing worktree: {wt_path}")

    # Find the branch name for this worktree
    worktrees = git_worktree.list_worktrees(repo_root)
    branch_name: str | None = None
    for wt in worktrees:
        if wt.get("path") == str(wt_path):
            branch_name = wt.get("branch")
            break

    try:
        git_worktree.remove_worktree(repo_root, wt_path)
    except GitError as e:
        console.warn(f"Worktree removal failed: {e}")
        return False

    if branch_name and branch_name != main_branch:
        with contextlib.suppress(GitError):
            git_branch.delete_branch(repo_root, branch_name, force=True)

    with contextlib.suppress(GitError):
        git_worktree.prune_worktrees(repo_root)

    console.success(f"Removed {wt_path.name}")
    return True
