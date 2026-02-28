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
from pathlib import Path
from typing import Any

import structlog

from ghaiw.ai_tools.base import AbstractAITool
from ghaiw.config.loader import load_config
from ghaiw.git import branch as git_branch
from ghaiw.git import pr as git_pr
from ghaiw.git import repo as git_repo
from ghaiw.git import sync as git_sync
from ghaiw.git import worktree as git_worktree
from ghaiw.git.repo import GitError
from ghaiw.models.ai import AIToolID, TokenUsage
from ghaiw.models.config import ProjectConfig
from ghaiw.models.deps import DependencyGraph
from ghaiw.models.task import Task
from ghaiw.models.work import MergeStrategy, SyncEvent, SyncResult, WorktreeState
from ghaiw.providers.base import AbstractTaskProvider
from ghaiw.providers.registry import get_provider
from ghaiw.services.task_service import (
    add_in_progress_label,
    add_worked_by_labels,
    remove_in_progress_label,
)
from ghaiw.ui import prompts
from ghaiw.ui.console import console
from ghaiw.utils.markdown import remove_marker_block
from ghaiw.utils.terminal import (
    compose_work_title,
    launch_in_new_terminal,
    set_terminal_title,
    start_title_keeper,
    stop_title_keeper,
)

logger = structlog.get_logger()

# --- Implementation usage block markers ---
IMPL_USAGE_MARKER_START = "<!-- ghaiw:impl-usage:start -->"
IMPL_USAGE_MARKER_END = "<!-- ghaiw:impl-usage:end -->"

# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------


def _resolve_worktrees_dir(config: ProjectConfig, repo_root: Path) -> Path:
    """Resolve the worktrees directory from config."""
    wt_dir = config.project.worktrees_dir
    if Path(wt_dir).is_absolute():
        return Path(wt_dir)
    return (repo_root / wt_dir).resolve()


def _complexity_to_model(
    config: ProjectConfig,
    ai_tool: str,
    complexity: str | None,
) -> str | None:
    """Map task complexity to model ID from config."""
    if not complexity:
        return None
    return config.get_complexity_model(ai_tool, complexity)


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
    from ghaiw.skills.installer import install_skills

    install_skills(worktree_path, is_self_init=False, force=True)
    logger.debug("work.bootstrap_skills", path=str(worktree_path))

    # Propagate allowlist from project root to worktree if already configured
    from ghaiw.config.claude_allowlist import configure_allowlist, is_allowlist_configured

    if is_allowlist_configured(repo_root):
        configure_allowlist(worktree_path)

    # Run post-create hook
    if config.hooks.post_worktree_create:
        import subprocess

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
                logger.warning(
                    "work.hook_failed",
                    hook=config.hooks.post_worktree_create,
                    error=e.stderr,
                )


def _is_inside_ai_cli() -> bool:
    """Detect if we are running inside an AI CLI session.

    When an AI agent calls ``ghaiw implement-task`` from within its own
    session, we must not launch another AI instance (infinite nesting).
    Instead, create the worktree and print the path.
    """
    # Claude Code sets CLAUDE_CODE=1 or CLAUDE_CODE_ENTRYPOINT
    if os.environ.get("CLAUDE_CODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return True
    # Copilot CLI
    if os.environ.get("COPILOT_CLI"):
        return True
    # Gemini CLI
    if os.environ.get("GEMINI_CLI"):
        return True
    # Codex CLI
    if os.environ.get("CODEX_CLI"):
        return True
    # Generic: ghaiw sets this when launching an AI tool
    return bool(os.environ.get("GHAIW_IN_AI_SESSION"))


# ---------------------------------------------------------------------------
# Draft PR bootstrap (shared by plan and work flows)
# ---------------------------------------------------------------------------

PLAN_MARKER_START = "<!-- ghaiw:plan:start -->"
PLAN_MARKER_END = "<!-- ghaiw:plan:end -->"


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

    # Push branch to origin
    try:
        git_repo._run_git("push", "-u", "origin", branch_name, cwd=repo_root)
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


def _post_exit_capture(
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

    if not usage or (not usage.total_tokens and not usage.input_tokens):
        logger.debug("work.no_token_usage")
        return None

    # Use transcript model_breakdown as source of truth when model wasn't set explicitly
    effective_model = model or (usage.model_breakdown[0].model if usage.model_breakdown else None)

    usage_block = build_impl_usage_block(
        ai_tool=ai_tool,
        model=effective_model,
        token_usage=usage,
    )

    # Update PR body with usage stats
    pr_info = git_pr.get_pr_for_branch(repo_root, branch)
    if pr_info:
        pr_number = int(pr_info["number"])
        try:
            result = git_pr._run_gh(
                "pr",
                "view",
                str(pr_number),
                "--json",
                "body",
                cwd=repo_root,
                check=False,
            )
            if result.returncode == 0:
                import json as json_mod

                current_body = json_mod.loads(result.stdout).get("body", "")
                cleaned_body = _strip_impl_usage_block(current_body)
                new_body = cleaned_body.rstrip("\n") + "\n\n" + usage_block + "\n"
                if git_pr.update_pr_body(repo_root, pr_number, new_body):
                    console.success("Updated PR with implementation usage stats.")
                    logger.info(
                        "work.impl_usage_updated",
                        pr=pr_number,
                        total_tokens=usage.total_tokens,
                    )
        except Exception:
            logger.debug("work.pr_body_read_failed", exc_info=True)
    else:
        logger.debug("work.no_pr_for_branch", branch=branch)

    # Embed usage stats in the issue body (consistent with plan summary)
    if issue_number and provider:
        with contextlib.suppress(Exception):
            task = provider.read_task(str(issue_number))
            body = _strip_impl_usage_block(task.body)
            new_body = body.rstrip("\n") + "\n\n" + usage_block + "\n"
            provider.update_task(str(issue_number), body=new_body)
            console.success("Updated issue with implementation usage stats.")
            logger.info("work.impl_usage_issue_updated", issue=issue_number)

    return effective_model


def build_work_prompt(task: Task, ai_tool: str | None = None) -> str:
    """Build the initial prompt for a work session."""
    from ghaiw.skills.installer import get_templates_dir

    template_path = get_templates_dir() / "prompts" / "work-context.md"
    if not template_path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")
    template = template_path.read_text(encoding="utf-8")
    return template.format(issue_number=task.id, issue_title=task.title)


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


def _post_work_lifecycle_pr(
    repo_root: Path,
    branch: str,
    issue_number: str | int | None,
    worktree_path: Path | None,
    provider: AbstractTaskProvider,
) -> None:
    provider_get_pr = getattr(provider, "get_pr_for_branch", None)
    pr_info_raw = (
        provider_get_pr(branch)
        if callable(provider_get_pr)
        else git_pr.get_pr_for_branch(repo_root, branch)
    )
    pr_info = pr_info_raw if isinstance(pr_info_raw, dict) else None
    if not pr_info:
        console.warn(f"No open PR found for branch '{branch}'. Skipping lifecycle.")
        return

    pr_number = pr_info.get("number") or pr_info.get("pr_number")
    if not pr_number:
        console.warn(f"Could not determine PR number for branch '{branch}'.")
        return

    if not prompts.confirm(f"Do you want to merge PR #{pr_number}?", default=True):
        return

    # Remove the worktree before merging. git refuses to delete a branch that
    # is checked out in a linked worktree, so `gh pr merge --delete-branch`
    # would fail without this step.
    if worktree_path:
        console.step(f"Removing worktree: {worktree_path.name}")
        with contextlib.suppress(Exception):
            git_worktree.remove_worktree(repo_root, worktree_path)
        with contextlib.suppress(Exception):
            git_worktree.prune_worktrees(repo_root)
        console.success(f"Removed {worktree_path.name}")

    try:
        git_pr.merge_pr(repo_root=repo_root, pr_number=int(pr_number), strategy="squash")
    except Exception as e:
        logger.error("pr_merge.failed", pr_number=pr_number, error=str(e))
        # Try to clean up remote branch
        with contextlib.suppress(Exception):
            subprocess.run(
                ["git", "push", "origin", "--delete", branch],
                check=True,
                capture_output=True,
                cwd=repo_root,
            )
        # Try to clean up local branch (worktree already removed above)
        with contextlib.suppress(Exception):
            git_branch.delete_branch(repo_root, branch, force=True)
        return

    with contextlib.suppress(Exception):
        subprocess.run(["git", "pull", "--quiet"], cwd=repo_root)

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
        subprocess.run(
            ["git", "merge", "--squash", branch],
            check=True,
            capture_output=True,
            cwd=repo_root,
        )
        subprocess.run(
            ["git", "commit", "--no-edit"],
            check=True,
            capture_output=True,
            cwd=repo_root,
        )
        subprocess.run(
            ["git", "push"],
            check=True,
            capture_output=True,
            cwd=repo_root,
        )
    except Exception as e:
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

    # Read the issue
    task = _resolve_target(target, provider, config)
    if not task:
        return False

    console.rule(f"implement-task #{task.id}")
    console.kv("Issue", console.issue_ref(task.id, task.title))

    # Resolve AI tool
    resolved_tool = ai_tool or config.get_ai_tool("work")
    if not resolved_tool:
        installed = AbstractAITool.detect_installed()
        if installed and len(installed) > 1 and prompts.is_tty():
            tool_names = [str(t) for t in installed]
            idx = prompts.select("Select AI tool", tool_names)
            resolved_tool = tool_names[idx]
        elif installed:
            resolved_tool = installed[0].value
        else:
            resolved_tool = None

    # Resolve model: CLI flag → env var override → complexity mapping
    resolved_model = model
    if not resolved_model:
        env_model = os.environ.get("GHAIW_WORK_MODEL")
        if env_model:
            resolved_model = env_model
    if not resolved_model and resolved_tool and task.complexity:
        resolved_model = _complexity_to_model(config, resolved_tool, task.complexity.value)
    # Final fallback: ai.work.model or ai.default_model from config
    if not resolved_model:
        resolved_model = config.get_model("work")

    if resolved_tool:
        console.kv("AI tool", resolved_tool)
    if task.complexity:
        console.kv("Complexity", task.complexity.value)
    if resolved_model:
        console.kv("Model", resolved_model)

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
                console.info("Run `ghaiw plan-task` to create a plan first.")
                return True  # Early exit — user chose to plan
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
                git_repo._run_git("fetch", "origin", f"{branch_name}:{branch_name}", cwd=repo_root)
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
    prompt = build_work_prompt(task, resolved_tool)
    snippet = "\n".join(prompt.splitlines()[:5]) + "\n…"
    console.panel(snippet, title="Work Prompt (preview)")

    # cd_only mode: just print the worktree path and return (no title, no AI)
    if cd_only:
        print(str(worktree_path))
        return True

    # AI-initiated start guard: if we're inside an AI CLI session,
    # don't launch another AI tool — just print the worktree path.
    if _is_inside_ai_cli():
        detected_env = (
            "CLAUDE_CODE"
            if os.environ.get("CLAUDE_CODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT")
            else "COPILOT_CLI"
            if os.environ.get("COPILOT_CLI")
            else "GEMINI_CLI"
            if os.environ.get("GEMINI_CLI")
            else "CODEX_CLI"
            if os.environ.get("CODEX_CLI")
            else "GHAIW_IN_AI_SESSION"
        )
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
        transcript_dir = tempfile.mkdtemp(prefix="ghaiw-work-", dir="/tmp")
        transcript_path = Path(transcript_dir) / f"transcript-{task.id}.log"
        console.hint(f"Transcript: {transcript_path}")
    except OSError:
        logger.warning("work.transcript_dir_failed")

    # Detach mode: launch AI tool in a new terminal, don't block
    if detach and resolved_tool:
        try:
            detach_adapter = AbstractAITool.get(AIToolID(resolved_tool))
            cmd = detach_adapter.build_launch_command(
                model=resolved_model,
                trusted_dirs=[str(worktree_path), "/tmp"],
                initial_message=prompt,
            )
        except (ValueError, KeyError):
            cmd = [resolved_tool]

        console.step(f"Launching {resolved_tool} in new terminal...")
        if launch_in_new_terminal(cmd, cwd=str(worktree_path), title=work_title):
            console.success(f"Detached AI session for #{task.id}")
            return True
        console.warn("Could not launch in new terminal — falling back to inline")
        # Fall through to inline launch below

    # Launch AI tool (inline)
    if not detach and resolved_tool:
        console.step(f"Launching {resolved_tool}...")

        adapter: AbstractAITool | None = None
        try:
            adapter = AbstractAITool.get(AIToolID(resolved_tool))

            # Check model compatibility
            if resolved_model and not adapter.is_model_compatible(resolved_model):
                console.warn(
                    f"Model '{resolved_model}' is not compatible with {resolved_tool}; "
                    "using tool default"
                )
                resolved_model = None

            if resolved_model:
                console.detail(f"Model: {resolved_model}")

            exit_code = adapter.launch(
                worktree_path=worktree_path,
                model=resolved_model,
                prompt=prompt,
                transcript_path=transcript_path,
                trusted_dirs=[str(worktree_path), "/tmp"],
            )
            logger.info("work.ai_exited", exit_code=exit_code, tool=resolved_tool)
        except (ValueError, KeyError):
            console.warn(f"Unknown AI tool: {resolved_tool}")
        except Exception as e:
            console.warn(f"AI tool launch failed: {e}")
        finally:
            stop_title_keeper()

            if adapter is not None:
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

        # Post-exit: parse transcript, update PR body and issue with token usage.
        detected_model: str | None = None
        if adapter is not None:
            detected_model = _post_exit_capture(
                transcript_path=transcript_path,
                adapter=adapter,
                repo_root=repo_root,
                branch=branch_name,
                ai_tool=resolved_tool,
                model=resolved_model,
                issue_number=task.id,
                provider=provider,
            )

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

    lines = []
    lines.append(f"  Worktree   {console.git_ref(branch_name)}")
    lines.append(f"  Issue      {console.issue_ref(task.id, task.title)}")
    console.panel("\n".join(lines), title="Work session complete")

    return True


def _resolve_target(
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
        from ghaiw.services.task_service import create_from_plan_file

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


def batch(
    issue_numbers: list[str],
    ai_tool: str | None = None,
    model: str | None = None,
    project_root: Path | None = None,
) -> bool:
    """Start parallel work sessions for multiple issues.

    Independent issues launch in parallel terminals.
    Dependent chains: only the first issue in each chain is launched; the
    remaining chain members are printed in order for manual sequential
    execution (one cannot work on a dependent issue before its blocker is done).
    """
    config = load_config(project_root)
    cwd = project_root or Path.cwd()

    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error("Not inside a git repository")
        return False

    console.rule(f"work batch ({len(issue_numbers)} issues)")

    # Resolve AI tool
    resolved_tool = ai_tool or config.get_ai_tool("work")

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

    # Launch independent issues in parallel terminals
    for issue_id in independent:
        cmd = ["ghaiw", "implement-task", issue_id]
        if resolved_tool:
            cmd.extend(["--ai", resolved_tool])
        if model:
            cmd.extend(["--model", model])

        console.step(f"Launching #{issue_id} in new terminal")
        if launch_in_new_terminal(cmd, cwd=str(repo_root), title=f"ghaiw #{issue_id}"):
            launched += 1
        else:
            console.warn(f"Could not launch terminal for #{issue_id}")

    # Launch chains: start only the first item, list the rest in order
    for chain in chains:
        console.info(f"Dependency chain: {' → '.join(f'#{n}' for n in chain)}")
        first_id = chain[0]
        cmd = ["ghaiw", "implement-task", first_id]
        if resolved_tool:
            cmd.extend(["--ai", resolved_tool])
        if model:
            cmd.extend(["--model", model])

        console.step(f"Launching #{first_id} (first in chain) in new terminal")
        if launch_in_new_terminal(cmd, cwd=str(repo_root), title=f"ghaiw #{first_id}"):
            launched += 1
        else:
            console.warn(f"Could not launch terminal for #{first_id}")

        if len(chain) > 1:
            remaining = ", ".join(f"#{n}" for n in chain[1:])
            console.info(f"After completing #{first_id}, work on these in order: {remaining}")

    console.panel(f"  Launched {launched} work session(s)", title="Batch started")
    return launched > 0


def _build_graph_from_issues(
    issue_numbers: list[str],
    config: ProjectConfig,
) -> DependencyGraph | None:
    """Try to build a dependency graph from issue body cross-references."""
    from ghaiw.models.deps import DependencyEdge, DependencyGraph
    from ghaiw.models.task import parse_dependency_refs

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

        # Match by worktree directory name
        if target in Path(wt_path).name:
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

    from ghaiw.utils.slug import slugify

    slug = slugify(title)

    wt_path = find_worktree_path(slug, project_root=project_root)
    if not wt_path:
        raise ValueError(
            f"No worktree found matching plan title '{title}' (slug: '{slug}'). "
            "Check active worktrees with: ghaiw work list"
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
            from ghaiw.models.task import TaskState

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
        merge_base = git_repo._run_git("merge-base", branch, main_branch, cwd=repo_root)
        branch_tip = git_repo._run_git("rev-parse", branch, cwd=repo_root)
        if merge_base.stdout.strip() == branch_tip.stdout.strip():
            return WorktreeState.STALE_MERGED
    except GitError:
        logger.debug("staleness.merge_base_check_failed", exc_info=True)

    # 4. Check if remote tracking branch gone
    try:
        result = git_repo._run_git(
            "for-each-ref",
            "--format=%(upstream:trackshort)",
            f"refs/heads/{branch}",
            cwd=repo_root,
            check=False,
        )
        if result.stdout.strip() == "gone":
            return WorktreeState.STALE_REMOTE_GONE
    except GitError:
        logger.debug("staleness.remote_tracking_check_failed", exc_info=True)

    return WorktreeState.ACTIVE


# ---------------------------------------------------------------------------
# Implementation usage block (for PR bodies)
# ---------------------------------------------------------------------------


def build_impl_usage_block(
    ai_tool: str | None = None,
    model: str | None = None,
    token_usage: TokenUsage | None = None,
) -> str:
    """Build the ## Implementation Usage section for PR body."""
    from ghaiw.ai_tools.transcript import format_count

    lines = [
        IMPL_USAGE_MARKER_START,
        "",
        "## Implementation Usage",
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]

    if ai_tool:
        lines.append(f"| Implementation tool | `{ai_tool}` |")
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

    # Model breakdown table
    if token_usage and token_usage.model_breakdown:
        lines.append("")
        lines.append("### Model Breakdown")
        lines.append("")
        lines.append("| Model | Input | Output | Cached |")
        lines.append("|-------|-------|--------|--------|")
        for row in token_usage.model_breakdown:
            inp = format_count(row.input_tokens)
            out = format_count(row.output_tokens)
            cache = format_count(row.cached_tokens)
            lines.append(f"| {row.model} | {inp} | {out} | {cache} |")

    lines.append("")
    lines.append(IMPL_USAGE_MARKER_END)

    return "\n".join(lines)


def _strip_impl_usage_block(body: str) -> str:
    """Remove existing implementation usage block from body (idempotent)."""
    return remove_marker_block(body, IMPL_USAGE_MARKER_START, IMPL_USAGE_MARKER_END)


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
    1. Closes #N
    2. Part of #parent (if detected)
    3. ## Summary (from PR-SUMMARY file)

    Plan summary stays on the issue only — not copied into the PR body.
    """
    lines: list[str] = []

    if close_issue:
        lines.append(f"Closes #{task.id}")
    if parent_issue:
        lines.append(f"Part of #{parent_issue}")

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

    def emit(event: str, **data: str | int) -> None:
        ev = SyncEvent(event=event, data=data)
        events.append(ev)
        if json_output:
            console.raw(json.dumps({"event": event, **data}))

    # Pre-flight checks
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        emit("error", reason="not_git_repo")
        return SyncResult(
            success=False,
            current_branch="",
            main_branch=main_branch or "",
            events=events,
        )

    try:
        current = git_repo.get_current_branch(cwd)
    except GitError:
        emit("error", reason="detached_head")
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
            emit("error", reason="no_main_branch")
            return SyncResult(
                success=False,
                current_branch=current,
                main_branch="",
                events=events,
            )

    if current == resolved_main:
        emit("error", reason="on_main_branch")
        return SyncResult(
            success=False,
            current_branch=current,
            main_branch=resolved_main,
            events=events,
        )

    # Check clean — with detailed diagnostics
    if not git_repo.is_clean(cwd):
        dirty = git_repo.get_dirty_status(cwd)
        detail_parts = []
        if dirty["staged"]:
            detail_parts.append(f"{dirty['staged']} staged")
        if dirty["unstaged"]:
            detail_parts.append(f"{dirty['unstaged']} unstaged")
        if dirty["untracked"]:
            detail_parts.append(f"{dirty['untracked']} untracked")
        detail_str = ", ".join(detail_parts) if detail_parts else "dirty"
        emit("error", reason="dirty_worktree", details=detail_str)
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

    emit("preflight_ok", current_branch=current, main_branch=resolved_main)
    if not json_output:
        console.step(f"Syncing {current} with {resolved_main}")

    # Fetch
    merge_ref = resolved_main
    try:
        remote_result = git_repo._run_git("remote", cwd=repo_root, check=False)
        has_remote = bool(remote_result.stdout.strip())
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
        emit("up_to_date", branch=current, main=resolved_main)
        if not json_output:
            console.success("Already up to date.")
        emit("done", branch=current, main=resolved_main)
        return SyncResult(
            success=True,
            current_branch=current,
            main_branch=resolved_main,
            events=events,
        )

    if dry_run:
        emit("dry_run", action="merge_main_into_feature", behind=behind)
        if not json_output:
            console.info(f"Dry run: {behind} commit(s) would be merged.")
        emit("done", branch=current, main=resolved_main)
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
        emit("merged", commits_merged=merge_result.commits_merged or behind)
        if not json_output:
            console.success(f"Merged {merge_result.commits_merged or behind} commit(s).")
        emit("done", branch=current, main=resolved_main)
        return SyncResult(
            success=True,
            current_branch=current,
            main_branch=resolved_main,
            commits_merged=merge_result.commits_merged or behind,
            events=events,
        )

    # Conflicts
    conflicts = merge_result.conflicts
    emit("conflict", source=resolved_main, target=current, files="\n".join(conflicts))
    if not json_output:
        console.error(f"Merge conflict in {len(conflicts)} file(s):")
        for f in conflicts:
            console.detail(f)
        console.empty()
        console.hint("Resolve conflicts, then run:")
        console.out.print("      [prompt.dimmed]$ ghaiw work sync[/]")

    # Get conflict diff
    try:
        diff_result = git_repo._run_git("diff", cwd=repo_root, check=False)
        if diff_result.stdout.strip():
            emit("conflict_diff", diff=diff_result.stdout)
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
            from ghaiw.services.task_service import create_from_plan_file

            console.info(f"Creating issue from plan file: {target}")
            task = create_from_plan_file(target_path, config=config, provider=provider)
            if not task:
                return False
            target = task.id

    wt_path: Path | None = resolved_wt_path

    if wt_path is not None:
        cwd = wt_path

    # If target specifies a worktree, navigate to it
    if target and wt_path is None:
        wt_path = find_worktree_path(target, project_root=repo_root)
        if wt_path:
            cwd = wt_path

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
        dirty = git_repo.get_dirty_status(cwd)
        detail_parts = []
        if dirty["staged"]:
            detail_parts.append(f"{dirty['staged']} staged")
        if dirty["unstaged"]:
            detail_parts.append(f"{dirty['unstaged']} unstaged")
        if dirty["untracked"]:
            detail_parts.append(f"{dirty['untracked']} untracked")
        detail_str = ", ".join(detail_parts) if detail_parts else "dirty"
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
        git_repo._run_git("push", "-u", "origin", branch, cwd=repo_root)
        console.success("Branch pushed.")
    except GitError as e:
        console.error(f"Push failed: {e}")
        return False

    # Check for existing PR (expected from plan-task or implement-task bootstrap)
    existing_pr = git_pr.get_pr_for_branch(repo_root, branch)

    # Resolve PR-SUMMARY.md path: check worktree first, then fall back to /tmp
    pr_summary_path: Path | None = None
    if worktree_path and (worktree_path / "PR-SUMMARY.md").exists():
        pr_summary_path = worktree_path / "PR-SUMMARY.md"
    else:
        tmp_path = Path("/tmp") / f"PR-SUMMARY-{issue_number}.md"
        if tmp_path.exists():
            pr_summary_path = tmp_path

    if pr_summary_path is None:
        console.warn("No PR-SUMMARY file found — PR description will have no summary.")
        console.detail(f"Expected: /tmp/PR-SUMMARY-{issue_number}.md")

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

        # Build updated body: keep existing content, add close reference + summary
        close_ref = f"Closes #{issue_number}" if close_issue else ""
        parent_ref = f"\nPart of #{parent_issue}" if parent_issue else ""
        refs = close_ref + parent_ref

        # Strip existing "Implements #N" line if we're adding "Closes #N"
        updated_body = current_body
        if close_ref:
            updated_body = re.sub(
                rf"^Implements\s+#{re.escape(issue_number)}\s*\n?",
                "",
                updated_body,
                flags=re.MULTILINE,
            )
            updated_body = refs + "\n\n" + updated_body.lstrip("\n")
        updated_body = updated_body.rstrip("\n") + summary_section + "\n"

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
        merge_result = git_sync.merge_branch(repo_root, main_branch)
        if not merge_result.success:
            console.error("Merge conflicts detected. Resolve them first.")
            return False
    except GitError as e:
        console.error(f"Merge failed: {e}")
        return False

    # Switch to main and merge feature branch
    console.step(f"Merging {branch} into {main_branch}...")
    try:
        git_repo._run_git("checkout", main_branch, cwd=repo_root)
        git_repo._run_git("merge", "--no-edit", branch, cwd=repo_root)
        git_repo._run_git("push", "origin", main_branch, cwd=repo_root)
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

    # The first worktree is the main checkout — skip unless --all
    for i, wt in enumerate(worktrees):
        wt_branch = wt.get("branch", "")
        wt_path = wt.get("path", "")

        # Skip main checkout unless --all
        if i == 0 and not show_all:
            continue

        # Skip non-ghaiw branches unless --all
        issue_number = extract_issue_from_branch(wt_branch)
        if not issue_number and not show_all:
            continue

        # Classify staleness (also fetches issue state)
        provider_inst = get_provider(config)
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
        console.info("No active ghaiw worktrees found.")
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

    if branch_name and branch_name != main_branch:
        with contextlib.suppress(GitError):
            git_branch.delete_branch(repo_root, branch_name, force=True)

    with contextlib.suppress(GitError):
        git_worktree.prune_worktrees(repo_root)

    console.success(f"Removed {wt_path.name}")
    return True
