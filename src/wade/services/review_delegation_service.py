"""Review delegation service — plan review and implementation review via delegation."""

from __future__ import annotations

from pathlib import Path

import structlog
from crossby.models.ai import EffortLevel

from wade.config.loader import load_config
from wade.git import repo as git_repo
from wade.git.repo import GitError
from wade.models.config import AICommandConfig, ProjectConfig
from wade.models.delegation import DelegationMode, DelegationRequest, DelegationResult
from wade.services.ai_resolution import (
    confirm_ai_selection,
    resolve_ai_tool,
    resolve_effort,
    resolve_model,
)
from wade.services.delegation_service import delegate, resolve_mode
from wade.skills.installer import load_prompt_template
from wade.ui.console import console

logger = structlog.get_logger()


def _load_review_config(
    command: str,
    project_root: Path | None = None,
) -> tuple[ProjectConfig, AICommandConfig]:
    """Load project config and extract the per-command review configuration."""
    config = load_config(project_root)
    cmd_config: AICommandConfig = getattr(config.ai, command)
    return config, cmd_config


def _check_review_enabled(command: str, cmd_config: AICommandConfig) -> DelegationResult | None:
    """Return a skip result if the review command is disabled, else None."""
    if cmd_config.enabled is False:
        config_key = f"ai.{command}.enabled"
        console.info(f"Review skipped — not enabled in .wade.yml ({config_key}).")
        return DelegationResult(
            success=True,
            feedback=f"Review skipped — not enabled in .wade.yml ({config_key}).",
            mode=DelegationMode.PROMPT,
            skipped=True,
        )
    return None


def _run_review_delegation(
    prompt: str,
    command: str,
    *,
    config: ProjectConfig | None = None,
    cmd_config: AICommandConfig | None = None,
    ai_tool: str | None = None,
    model: str | None = None,
    mode: str | None = None,
    effort: str | None = None,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    effort_explicit: bool = False,
) -> DelegationResult:
    """Shared pipeline: config load → mode resolve → AI resolve → confirm → delegate → display."""
    if config is None or cmd_config is None:
        config, cmd_config = _load_review_config(command)

    try:
        default_mode = (
            DelegationMode.INTERACTIVE if command == "review_batch" else DelegationMode.PROMPT
        )
        delegation_mode = (
            DelegationMode(mode) if mode else resolve_mode(cmd_config, default=default_mode)
        )
    except ValueError:
        console.error(f"Invalid delegation mode: {mode}")
        return DelegationResult(
            success=False,
            feedback=f"Invalid delegation mode: {mode}",
            mode=DelegationMode.PROMPT,
            exit_code=1,
        )

    resolved_tool: str | None = None
    resolved_model: str | None = None
    resolved_effort: EffortLevel | None = None

    if delegation_mode != DelegationMode.PROMPT:
        resolved_tool = resolve_ai_tool(ai_tool, config, command=command)
        resolved_model = resolve_model(model, config, command=command, tool=resolved_tool)
        resolved_effort = resolve_effort(effort, config, command=command, tool=resolved_tool)

        resolved_tool, resolved_model, resolved_effort, _yolo = confirm_ai_selection(
            resolved_tool,
            resolved_model,
            tool_explicit=ai_explicit,
            model_explicit=model_explicit,
            resolved_effort=resolved_effort,
            effort_explicit=effort_explicit,
            mode=delegation_mode,
        )

    effort_str = resolved_effort.value if isinstance(resolved_effort, EffortLevel) else None

    request = DelegationRequest(
        mode=delegation_mode,
        prompt=prompt,
        ai_tool=resolved_tool,
        model=resolved_model,
        effort=effort_str,
        **({"timeout": cmd_config.timeout} if cmd_config.timeout is not None else {}),
    )

    if delegation_mode == DelegationMode.HEADLESS:
        # This spawns an external AI subprocess bounded by ``request.timeout``.
        # Announce that budget so the orchestrator driving wade (Claude Code,
        # Cursor, Copilot, …) allows more than its own shell/tool timeout before
        # killing the call — otherwise it aborts the review before wade's own
        # timeout can fire. Configure the budget via ``ai.<command>.timeout``.
        console.info(
            "Launching headless AI review — this runs an external AI subprocess "
            f"that can take up to {request.timeout}s. Keep it in the foreground and "
            f"allow more than {request.timeout}s before timing out (raise your "
            "shell/tool timeout if needed). Do not move it to the background."
        )
    elif delegation_mode == DelegationMode.INTERACTIVE:
        console.info(
            "Launching external AI review session — "
            "please wait, do not move this to the background."
        )

    result = delegate(request)
    if result.success:
        console.out.print(result.feedback)
    else:
        console.error(result.feedback)
    return result


def review_plan(
    plan_file: str,
    *,
    ai_tool: str | None = None,
    model: str | None = None,
    mode: str | None = None,
    effort: str | None = None,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    effort_explicit: bool = False,
) -> DelegationResult:
    """Review a plan file via the delegation infrastructure."""
    config, cmd_config = _load_review_config("review_plan")
    skip = _check_review_enabled("review_plan", cmd_config)
    if skip is not None:
        return skip

    plan_path = Path(plan_file)
    if not plan_path.is_file():
        console.error(f"Plan file not found: {plan_file}")
        return DelegationResult(
            success=False,
            feedback=f"Plan file not found: {plan_file}",
            mode=DelegationMode.PROMPT,
            exit_code=1,
        )

    plan_content = plan_path.read_text(encoding="utf-8")
    template = load_prompt_template("review-plan.md")
    prompt = template.replace("{plan_content}", plan_content)

    return _run_review_delegation(
        prompt,
        "review_plan",
        config=config,
        cmd_config=cmd_config,
        ai_tool=ai_tool,
        model=model,
        mode=mode,
        effort=effort,
        ai_explicit=ai_explicit,
        model_explicit=model_explicit,
        effort_explicit=effort_explicit,
    )


def _committed_diff_fallback() -> str:
    """Return branch diff against the base branch when working tree is clean.

    Uses ``git diff <base>...HEAD`` (three-dot syntax) to show changes
    committed on the current branch since it diverged from the base branch.
    The base branch is the configured ``main_branch`` or auto-detected
    ``main``/``master``.

    Returns empty string if on the base branch, if the repo root cannot be
    resolved, or on any GitError (graceful degradation).
    """
    try:
        repo_root = git_repo.get_repo_root(Path.cwd())
        current_branch = git_repo.get_current_branch(repo_root)
        config = load_config()
        base_branch = config.project.main_branch or git_repo.detect_main_branch(repo_root)
        if current_branch == base_branch:
            return ""
        return git_repo.diff_between(repo_root, base_branch, "HEAD")
    except GitError:
        return ""


def review_implementation(
    *,
    staged: bool = False,
    ai_tool: str | None = None,
    model: str | None = None,
    mode: str | None = None,
    effort: str | None = None,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    effort_explicit: bool = False,
) -> DelegationResult:
    """Review implementation changes via the delegation infrastructure."""
    config, cmd_config = _load_review_config("review_implementation")
    skip = _check_review_enabled("review_implementation", cmd_config)
    if skip is not None:
        return skip

    try:
        repo_root = git_repo.get_repo_root(Path.cwd())
        diff_content = git_repo.diff_worktree(repo_root, staged=staged).strip()
    except GitError as exc:
        # ``GitError`` already names the exact command that failed (e.g.
        # "git diff ... failed (exit N): ..." or "git rev-parse ... failed"),
        # so surface it directly rather than hard-coding "git diff failed",
        # which would mis-attribute a repo-root failure and double-prefix a
        # diff failure.
        message = f"Could not read changes to review: {exc}"
        console.error(message)
        return DelegationResult(
            success=False,
            feedback=message,
            mode=DelegationMode.PROMPT,
            exit_code=1,
        )

    if not diff_content and not staged:
        diff_content = _committed_diff_fallback()

    if not diff_content:
        label = "staged changes" if staged else "changes"
        console.warn(f"No {label} to review.")
        return DelegationResult(
            success=True,
            feedback=f"No {label} to review.",
            mode=DelegationMode.PROMPT,
            skipped=True,
        )

    template = load_prompt_template("review-code.md")
    prompt = template.replace("{diff_content}", diff_content)

    return _run_review_delegation(
        prompt,
        "review_implementation",
        config=config,
        cmd_config=cmd_config,
        ai_tool=ai_tool,
        model=model,
        mode=mode,
        effort=effort,
        ai_explicit=ai_explicit,
        model_explicit=model_explicit,
        effort_explicit=effort_explicit,
    )
