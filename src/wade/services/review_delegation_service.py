"""Review delegation service — plan review and implementation review via delegation."""

from __future__ import annotations

from pathlib import Path

import structlog

from wade.config.loader import load_config
from wade.models.ai import EffortLevel
from wade.models.config import AICommandConfig
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
from wade.utils.process import run

logger = structlog.get_logger()


def _check_review_enabled(command: str) -> DelegationResult | None:
    """Return a skip result if the review command is disabled, else None."""
    config = load_config()
    cmd_config: AICommandConfig = getattr(config.ai, command)
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
    ai_tool: str | None = None,
    model: str | None = None,
    mode: str | None = None,
    effort: str | None = None,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    effort_explicit: bool = False,
) -> DelegationResult:
    """Shared pipeline: config load → mode resolve → AI resolve → confirm → delegate → display."""
    config = load_config()
    cmd_config: AICommandConfig = getattr(config.ai, command)

    try:
        delegation_mode = DelegationMode(mode) if mode else resolve_mode(cmd_config)
    except ValueError:
        console.error(f"Invalid delegation mode: {mode}")
        return DelegationResult(
            success=False,
            feedback=f"Invalid delegation mode: {mode}",
            mode=DelegationMode.PROMPT,
            exit_code=1,
        )

    resolved_tool = resolve_ai_tool(ai_tool, config, command=command)
    resolved_model = resolve_model(model, config, command=command, tool=resolved_tool)
    resolved_effort = resolve_effort(effort, config, command=command, tool=resolved_tool)

    # Confirmation prompt (skipped in prompt mode — no AI tool needed)
    if delegation_mode != DelegationMode.PROMPT and resolved_tool:
        resolved_tool, resolved_model, resolved_effort, _yolo = confirm_ai_selection(
            resolved_tool,
            resolved_model,
            tool_explicit=ai_explicit,
            model_explicit=model_explicit,
            resolved_effort=resolved_effort,
            effort_explicit=effort_explicit,
        )

    effort_str = resolved_effort.value if isinstance(resolved_effort, EffortLevel) else None

    request = DelegationRequest(
        mode=delegation_mode,
        prompt=prompt,
        ai_tool=resolved_tool,
        model=resolved_model,
        effort=effort_str,
    )

    if delegation_mode in (DelegationMode.INTERACTIVE, DelegationMode.HEADLESS):
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
    skip = _check_review_enabled("review_plan")
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
        ai_tool=ai_tool,
        model=model,
        mode=mode,
        effort=effort,
        ai_explicit=ai_explicit,
        model_explicit=model_explicit,
        effort_explicit=effort_explicit,
    )


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
    skip = _check_review_enabled("review_implementation")
    if skip is not None:
        return skip

    diff_cmd = ["git", "diff"]
    if staged:
        diff_cmd.append("--staged")

    result = run(diff_cmd, check=False)
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "unknown error"
        console.error(f"git diff failed: {stderr}")
        return DelegationResult(
            success=False,
            feedback=f"git diff failed: {stderr}",
            mode=DelegationMode.PROMPT,
            exit_code=result.returncode,
        )

    diff_content = result.stdout.strip() if result.stdout else ""

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
        ai_tool=ai_tool,
        model=model,
        mode=mode,
        effort=effort,
        ai_explicit=ai_explicit,
        model_explicit=model_explicit,
        effort_explicit=effort_explicit,
    )
