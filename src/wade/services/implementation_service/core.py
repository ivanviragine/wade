"""Implementation service core — session start entry point and task resolution.

Hosts ``start`` (the implementation entry point) plus its task/worktree
resolution helpers and post-session usage capture. Other session-lifecycle
concerns live in sibling modules: ``draft_pr``, ``sync``, ``lifecycle``,
``done``, ``cleanup``, ``bootstrap``, ``usage_tracking``, and ``batch``.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path

import structlog
from crossby.ai_tools import AbstractAITool
from crossby.models.ai import AIToolID

from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.git.repo import GitError
from wade.models.config import ProjectConfig
from wade.models.permission import permission_mode_launch_kwargs
from wade.models.session import ImplementResult, MergeStatus
from wade.models.task import Task
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services.ai_resolution import (
    confirm_ai_selection,
    resolve_ai_tool,
    resolve_effort,
    resolve_model,
    resolve_permission_mode,
)
from wade.services.implementation_service._shared import (
    extract_issue_from_branch,
    find_worktree_path,
)
from wade.services.implementation_service.bootstrap import (
    _resolve_worktrees_dir,
    bootstrap_worktree,
    write_plan_md,
)
from wade.services.implementation_service.draft_pr import (
    bootstrap_draft_pr,
    build_implementation_prompt,
    extract_plan_from_pr_body,
)
from wade.services.implementation_service.lifecycle import _post_implementation_lifecycle
from wade.services.implementation_service.sync import catchup
from wade.services.implementation_service.usage_tracking import (
    _enrich_body_with_usage,
    _usage_has_token_metrics,
)
from wade.services.prompt_delivery import deliver_prompt_if_needed
from wade.services.task_service import (
    add_implemented_by_labels,
    add_in_progress_label,
)
from wade.ui import prompts
from wade.ui.console import console
from wade.utils.body_markers import enforce_body_budget, update_body_preserving_markers
from wade.utils.terminal import (
    compose_implement_title,
    launch_in_new_terminal,
    set_terminal_title,
    start_title_keeper,
    stop_title_keeper,
)

logger = structlog.get_logger()

# ``ImplementResult`` is re-exported here for backward compatibility — external
# code imports it from ``wade.services.implementation_service``.
__all__ = [
    "ImplementResult",
    "_capture_post_session_usage",
    "_detect_ai_cli_env",
    "_resolve_task_target",
    "_resolve_worktree_from_plan",
    "start",
]


# ---------------------------------------------------------------------------


def _detect_ai_cli_env() -> str | None:
    """Detect which AI CLI session we are running inside, if any.

    Returns the env-var name that triggered detection, or ``None``.

    When an AI agent calls ``wade implement`` from within its own
    session, we must not launch another AI instance (infinite nesting).
    Instead, create the worktree and print the path.
    """
    # Claude Code sets CLAUDE_CODE=1 or CLAUDE_CODE_ENTRYPOINT
    if os.environ.get("CLAUDE_CODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "CLAUDE_CODE"
    # Copilot CLI
    if os.environ.get("COPILOT_CLI"):
        return "COPILOT_CLI"
    # Codex CLI
    if os.environ.get("CODEX_CLI"):
        return "CODEX_CLI"
    # Cursor CLI
    if os.environ.get("CURSOR_CLI"):
        return "CURSOR_CLI"
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

    Returns the primary model detected from the transcript (for implemented-model label),
    or the explicitly passed model if no breakdown is available.
    """
    if not transcript_path or not transcript_path.is_file():
        return None

    # Parse transcript for token usage
    try:
        usage = adapter.parse_transcript(transcript_path)
    except Exception as e:
        logger.warning("implementation.transcript_parse_failed", error=str(e))
        return None

    has_tokens = _usage_has_token_metrics(usage)
    has_session = bool(usage and usage.session_id)
    if not has_tokens and not has_session:
        logger.warning("implementation.no_token_usage", transcript=str(transcript_path))
        console.warn(f"No token usage found in transcript: {transcript_path}")
        return None

    # Use transcript model_breakdown as source of truth when model wasn't set explicitly
    effective_model = model or (
        usage.model_breakdown[0].model if usage and usage.model_breakdown else None
    )

    # Update PR body with usage stats and session ID — only on an OPEN PR
    # (a merged/closed PR is not ours to rewrite; a lookup failure is transient).
    lookup = git_pr.get_pr_for_branch(repo_root, branch)
    if lookup.is_open and lookup.pr is not None:
        pr_number = lookup.pr.number
        try:
            # Re-read the body immediately before writing and rewrite only wade's
            # own usage marker block, so a concurrent edit outside it survives
            # (A4); enforce GitHub's size cap with a visible warning (A5).
            updated = update_body_preserving_markers(
                read_body=lambda: git_pr.get_pr_body(repo_root, pr_number),
                write_body=lambda b: git_pr.update_pr_body(repo_root, pr_number, b),
                transform=lambda b: _enrich_body_with_usage(
                    b, ai_tool, effective_model, usage, has_tokens, has_session
                ),
                warn=console.warn,
                label=f"PR #{pr_number} body",
            )
            if updated:
                if has_tokens:
                    console.success("Updated PR with implementation usage stats.")
                logger.info(
                    "implementation.impl_usage_updated",
                    pr=pr_number,
                    total_tokens=usage.total_tokens if usage else None,
                )
        except Exception:
            logger.debug("implementation.pr_body_read_failed", exc_info=True)
    elif lookup.lookup_failed:
        logger.debug("implementation.pr_lookup_failed", branch=branch)
    else:
        logger.debug("implementation.no_pr_for_branch", branch=branch)

    # Embed usage stats and session ID in the issue body
    if issue_number and provider:
        with contextlib.suppress(Exception):
            task = provider.read_task(str(issue_number))
            new_body = _enrich_body_with_usage(
                task.body,
                ai_tool,
                effective_model,
                usage,
                has_tokens,
                has_session,
            )
            new_body = enforce_body_budget(
                new_body, warn=console.warn, label=f"issue #{issue_number} body"
            )
            provider.update_task(str(issue_number), body=new_body)
            if has_tokens:
                console.success("Updated issue with implementation usage stats.")
            logger.info("implementation.impl_usage_issue_updated", issue=issue_number)

    return effective_model


# ---------------------------------------------------------------------------
# Implementation start
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
    effort: str | None = None,
    effort_explicit: bool = False,
    resume_session_id: str | None = None,
    resume_ai_tool: str | None = None,
    yolo: bool | None = None,
    permission_mode: str | None = None,
    permission_mode_explicit: bool = False,
    base_branch: str | None = None,
) -> ImplementResult:
    """Start an implementation session on an issue.

    Steps:
    1. Read the issue from the provider
    2. Create worktree and branch
    3. Bootstrap worktree (copy files, hooks, issue context)
    4. Resolve model from complexity
    5. Build implementation prompt and pass it as initial message to the AI tool
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
        ImplementResult with success/merged status.
    """
    config = load_config(project_root)
    provider = get_provider(config)

    # Resolve repo root
    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return ImplementResult(success=False)

    # When cd_only, redirect all status output to stderr so stdout stays
    # clean for the machine-readable worktree path.
    _original_out = console.out
    if cd_only:
        console.out = console.err
    try:
        # Read the issue
        task = _resolve_task_target(target, provider, config)
        if not task:
            return ImplementResult(success=False)

        # Tracking issue detection — redirect to batch implementation
        # NOTE: The submodule name "batch" collides with the batch() function
        # re-exported by __init__.py, so we access the module via sys.modules.
        import sys

        _batch_mod = sys.modules["wade.services.implementation_service.batch"]
        batch_result = _batch_mod.check_tracking_issue_and_batch(
            task,
            ai_tool=ai_tool,
            model=model,
            project_root=project_root,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort=effort,
            effort_explicit=effort_explicit,
            yolo=yolo,
            permission_mode=permission_mode,
            cd_only=cd_only,
        )
        if batch_result is not None:
            return ImplementResult(success=batch_result)

        console.rule(f"implement #{task.id}")
        console.kv("Issue", console.issue_ref(task.id, task.title))

        # Generate deterministic branch name early — only needs config + task, so it
        # can be computed before AI selection to allow the PR/plan check below.
        branch_name = git_branch.make_branch_name(
            config.project.branch_prefix,
            int(task.id),
            task.title,
        )

        # Check for existing draft PR (from plan flow) before AI selection so that
        # "Plan first" can short-circuit without ever showing the AI confirmation menu.
        # Only an OPEN PR is resumable — a merged/closed PR must not be treated as a
        # live draft (that would extract a stale plan and skip fresh bootstrap).
        pr_lookup = git_pr.get_pr_for_branch(repo_root, branch_name)
        if pr_lookup.lookup_failed:
            # A failed lookup is NOT "no PR" — bootstrapping now would scaffold a
            # duplicate draft over an existing PR and lose its extracted plan.
            console.error_with_fix(
                f"Could not look up the PR for branch {branch_name}",
                "Transient gh error — try again shortly",
            )
            return ImplementResult(success=False)
        existing_pr = pr_lookup.pr if pr_lookup.is_open else None
        plan_content: str | None = None
        proceed_needs_bootstrap = False

        has_plan = False
        if existing_pr is not None:
            console.info(f"Found existing PR #{existing_pr.number} for this task")
            # Extract plan content from PR body
            pr_body = git_pr.get_pr_body(repo_root, existing_pr.number)
            if pr_body:
                plan_content = extract_plan_from_pr_body(pr_body)
                if plan_content:
                    has_plan = True
                    console.detail("Plan content extracted from draft PR")
        if not has_plan:
            # No plan — warn and prompt (skip prompt when cd_only, consistent with
            # cd_only skipping the AI confirm menu).
            if not cd_only:
                console.warn("This task has no plan attached.")
                if prompts.is_tty():
                    choices = ["Plan first (recommended)", "Proceed without plan"]
                    idx = prompts.select("How would you like to proceed?", choices)
                    if idx == 0:
                        from wade.services.plan_service import plan as do_plan

                        plan_ok = do_plan(issue_id=task.id, project_root=project_root)
                        return ImplementResult(success=plan_ok)
            # Only bootstrap when there is no PR yet.
            proceed_needs_bootstrap = existing_pr is None

        if task.complexity:
            console.kv("Complexity", task.complexity.value)

        # Resolve AI tool and model
        resolved_tool = resolve_ai_tool(ai_tool, config, "implement")
        resolved_model = resolve_model(
            model,
            config,
            "implement",
            tool=resolved_tool,
            complexity=task.complexity.value if task.complexity else None,
        )

        # Resolve effort level (per-tier when complexity is known)
        resolved_effort = resolve_effort(
            effort,
            config,
            "implement",
            tool=resolved_tool,
            complexity=task.complexity.value if task.complexity else None,
        )

        # Resolve autonomy / permission mode (yolo is a back-compat alias)
        resolved_permission_mode = resolve_permission_mode(
            permission_mode, yolo, config, "implement"
        )

        # When resuming, override the resolved tool and skip interactive confirmation
        if resume_ai_tool:
            resolved_tool = resume_ai_tool
            ai_explicit = True

        # Offer interactive confirmation (skipped when cd_only or both flags explicit).
        if not cd_only:
            (
                resolved_tool,
                resolved_model,
                resolved_effort,
                resolved_permission_mode,
            ) = confirm_ai_selection(
                resolved_tool,
                resolved_model,
                tool_explicit=ai_explicit,
                model_explicit=model_explicit,
                resolved_effort=resolved_effort,
                effort_explicit=effort_explicit,
                resolved_permission_mode=resolved_permission_mode,
                permission_mode_explicit=(
                    permission_mode_explicit or permission_mode is not None or yolo is not None
                ),
            )

        # Resolve main branch and compute worktree path (only needed for worktree creation)
        main_branch = config.project.main_branch or git_repo.detect_main_branch(repo_root)

        # For stacked branches (chain execution), use the provided base instead of main
        effective_base = base_branch or main_branch

        worktrees_dir = _resolve_worktrees_dir(config, repo_root)
        repo_name = repo_root.name
        worktree_path = worktrees_dir / repo_name / branch_name.replace("/", "-")

        # Bootstrap draft PR for "Proceed without plan" path (deferred from above so it
        # runs after AI selection rather than before).
        if proceed_needs_bootstrap:
            console.step("Bootstrapping draft PR...")
            pr_info = bootstrap_draft_pr(
                issue_number=task.id,
                issue_title=task.title,
                plan_body=task.body or f"Implements #{task.id}: {task.title}",
                config=config,
                repo_root=repo_root,
                base_branch=base_branch,
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
        elif existing_pr is not None:
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
                return ImplementResult(success=False)
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
                            base_branch=effective_base,
                        )
                console.kv("Worktree", str(branch_name))
                console.kv("Path", str(worktree_path))
            except GitError as e:
                console.error(f"Failed to create worktree: {e}")
                return ImplementResult(success=False)

        console.empty()

        # Bootstrap
        from wade.skills.installer import IMPLEMENT_SKILLS

        write_plan_md(worktree_path, task, plan_content=plan_content)
        bootstrap_worktree(
            worktree_path,
            config,
            repo_root,
            skills=IMPLEMENT_SKILLS,
            selected_ai_tool=resolved_tool,
        )

        # Store stacked base branch metadata so sync can use it instead of main
        if base_branch:
            wade_dir = worktree_path / ".wade"
            wade_dir.mkdir(exist_ok=True)
            (wade_dir / "base_branch").write_text(base_branch + "\n")
            console.detail(f"Stacked on {base_branch}")

        # Catchup: sync worktree with base branch before AI launch (non-blocking)
        try:
            catchup_result = catchup(project_root=worktree_path)
            if not catchup_result.success:
                if catchup_result.conflicts:
                    console.warn(
                        "Startup catchup: merge conflict — "
                        "run `git merge origin/<base>` manually to resolve, then "
                        "re-run `wade implementation-session catchup --json` for inspection."
                    )
                else:
                    console.warn("Startup catchup failed — proceeding anyway.")
        except Exception:
            logger.debug("start.catchup_failed", exc_info=True)
            console.warn("Startup catchup failed — proceeding anyway.")

        # Add in-progress label and move to in-progress on project board (both non-critical)
        with contextlib.suppress(Exception):
            add_in_progress_label(provider, task.id)
        with contextlib.suppress(Exception):
            provider.move_to_in_progress(task.id)

        # Build implementation prompt (skipped when resuming a session)
        prompt: str | None = None
        if not resume_session_id:
            prompt = build_implementation_prompt(task, resolved_tool, has_plan=bool(plan_content))
            snippet = "\n".join(prompt.splitlines()[:5]) + "\n…"
            console.panel(snippet, title="Implementation Prompt (preview)")
        else:
            console.info(f"Resuming session: {resume_session_id[:40]}…")

        # cd_only mode: just print the worktree path and return (no title, no AI)
        if cd_only:
            print(str(worktree_path))
            return ImplementResult(success=True)

        # AI-initiated start guard: if we're inside an AI CLI session,
        # don't launch another AI tool — just print the worktree path.
        detected_env = _detect_ai_cli_env()
        if detected_env:
            logger.info(
                "implementation.ai_launch_skipped",
                reason="inside_ai_cli",
                env_var=detected_env,
            )
            console.info(
                f"Skipping AI launch: already inside AI session (detected via {detected_env})."
            )
            console.detail(f"Worktree ready at: {worktree_path}")
            print(str(worktree_path))
            return ImplementResult(success=True)

        # Set terminal title
        work_title = compose_implement_title(task.id, task.title)
        set_terminal_title(work_title)
        start_title_keeper(work_title)

        # Set up transcript capture
        transcript_path: Path | None = None
        try:
            transcript_dir = tempfile.mkdtemp(prefix="wade-implement-")
            transcript_path = Path(transcript_dir) / f"transcript-{task.id}.log"
            console.hint(f"Transcript: {transcript_path}")
        except OSError:
            logger.warning("implementation.transcript_dir_failed")

        # Detach mode: launch AI tool in a new terminal, don't block
        if detach and resolved_tool:
            cmd: list[str] | None = None
            try:
                detach_adapter = AbstractAITool.get(AIToolID(resolved_tool))
                if resume_session_id:
                    cmd = detach_adapter.build_resume_command(resume_session_id)
                    if cmd is None:
                        console.warn(
                            f"{resolved_tool} does not support resume — starting new session"
                        )
                        resume_session_id = None  # fall back to new session
                if not resume_session_id:
                    if prompt:
                        deliver_prompt_if_needed(detach_adapter, prompt)
                    cmd = detach_adapter.build_launch_command(
                        model=resolved_model,
                        trusted_dirs=[str(worktree_path), tempfile.gettempdir()],
                        initial_message=prompt,
                        effort=resolved_effort,
                        allowed_commands=config.permissions.allowed_commands,
                        **permission_mode_launch_kwargs(resolved_permission_mode),
                    )
            except (ValueError, KeyError):
                cmd = [resolved_tool]

            console.step(f"Launching {resolved_tool} in new terminal...")
            assert cmd is not None  # guaranteed by the two branches above
            if launch_in_new_terminal(cmd, cwd=str(worktree_path), title=work_title):
                console.success(f"Detached AI session for #{task.id}")
                stop_title_keeper()
                return ImplementResult(success=True)
            console.warn("Could not launch in new terminal — falling back to inline")
            detach = False
            # Fall through to inline launch below

        # Launch AI tool (inline)
        merge_status = MergeStatus.NOT_MERGED
        if not detach and resolved_tool:
            resume_label = " (resuming)" if resume_session_id else ""
            console.step(f"Launching {resolved_tool}{resume_label}...")

            adapter: AbstractAITool | None = None
            launch_completed = False
            detected_model: str | None = None
            try:
                adapter = AbstractAITool.get(AIToolID(resolved_tool))

                # Resume path: use build_resume_command() instead of launch()
                resume_cmd: list[str] | None = None
                if resume_session_id:
                    from wade.utils.process import run_with_transcript

                    resume_cmd = adapter.build_resume_command(resume_session_id)
                    if resume_cmd is None:
                        console.warn(
                            f"{resolved_tool} does not support resume — starting new session"
                        )
                        resume_session_id = None  # fall back below

                if resume_session_id and resume_cmd is not None:
                    logger.info(
                        "ai_tool.resume",
                        tool=str(adapter.TOOL_ID),
                        session_id=resume_session_id,
                        cwd=str(worktree_path),
                    )
                    exit_code = run_with_transcript(
                        resume_cmd,
                        transcript_path,
                        cwd=worktree_path,
                    )
                else:
                    if prompt:
                        deliver_prompt_if_needed(adapter, prompt)
                    exit_code = adapter.launch(
                        working_dir=worktree_path,
                        model=resolved_model,
                        prompt=prompt,
                        transcript_path=transcript_path,
                        trusted_dirs=[str(worktree_path), tempfile.gettempdir()],
                        effort=resolved_effort,
                        allowed_commands=config.permissions.allowed_commands,
                        **permission_mode_launch_kwargs(resolved_permission_mode),
                    )

                launch_completed = True
                logger.info("implementation.ai_exited", exit_code=exit_code, tool=resolved_tool)

                # Non-blocking tools (VS Code, Antigravity) return immediately.
                # Wait for the user to confirm they're done before post-session steps.
                if not adapter.capabilities().blocks_until_exit:
                    console.empty()
                    if not prompts.confirm("Have you finished the session?", default=True):
                        console.info(
                            "Worktree preserved — run"
                            " 'wade implementation-session done' when ready."
                        )
                        launch_completed = False
            except (ValueError, KeyError):
                console.warn(f"Unknown AI tool: {resolved_tool}")
                merge_status = MergeStatus.MERGE_FAILED
            except Exception as e:
                console.warn(f"AI tool launch failed: {e}")
                merge_status = MergeStatus.MERGE_FAILED
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
                    effective_model = resolved_model or detected_model
                    try:
                        merge_status = _post_implementation_lifecycle(
                            repo_root=repo_root,
                            branch=branch_name,
                            issue_number=task.id,
                            worktree_path=worktree_path,
                            provider=provider,
                            ai_tool=resolved_tool,
                            model=effective_model,
                            detach=detach,
                            ai_explicit=ai_explicit,
                            model_explicit=model_explicit,
                            permission_mode=resolved_permission_mode.value,
                            permission_mode_explicit=(
                                permission_mode_explicit
                                or permission_mode is not None
                                or yolo is not None
                            ),
                        )
                    except Exception:
                        logger.exception("post_implementation_lifecycle.failed")
                        merge_status = MergeStatus.MERGE_FAILED

            # Use CLI-resolved model, falling back to transcript-detected model.
            effective_model = resolved_model or detected_model
            try:
                add_implemented_by_labels(provider, task.id, resolved_tool, effective_model)
            except Exception as e:
                console.warn(f"Could not apply implemented-by labels: {e}")
                logger.warning("implementation.implemented_by_labels_failed", error=str(e))
        elif not resolved_tool:
            console.info("No AI tool configured. Worktree ready for manual work.")
            console.detail(f"cd {worktree_path}")
            stop_title_keeper()

        lines = []
        lines.append(f"  Worktree   {console.git_ref(branch_name)}")
        lines.append(f"  Issue      {console.issue_ref(task.id, task.title)}")
        console.panel("\n".join(lines), title="Implementation session complete")

        return ImplementResult(
            success=merge_status != MergeStatus.MERGE_FAILED,
            merged=merge_status == MergeStatus.MERGED,
            branch_name=branch_name,
        )
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

    # Treat as issue number — strip leading "#" so "#123" and "123" both work
    issue_id = target.lstrip("#")
    try:
        task = provider.read_task(issue_id)
        return task
    except Exception as e:
        console.error(f"Could not read issue #{issue_id}: {e}")
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
            "Check active worktrees with: wade worktree list"
        )

    branch = git_repo.get_current_branch(wt_path)
    issue_number = extract_issue_from_branch(branch)

    return wt_path, branch, issue_number
