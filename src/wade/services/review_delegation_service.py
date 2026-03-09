"""Review delegation service — plan review and implementation review via delegation."""

from __future__ import annotations

from pathlib import Path

import structlog

from wade.config.loader import load_config
from wade.models.delegation import DelegationMode, DelegationRequest, DelegationResult
from wade.services.ai_resolution import resolve_ai_tool, resolve_model
from wade.services.delegation_service import delegate, resolve_mode
from wade.skills.installer import load_prompt_template
from wade.ui.console import console
from wade.utils.process import run

logger = structlog.get_logger()


def review_plan(
    plan_file: str,
    *,
    ai_tool: str | None = None,
    model: str | None = None,
    mode: str | None = None,
) -> DelegationResult:
    """Review a plan file via the delegation infrastructure."""
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

    config = load_config()
    cmd_config = config.ai.review_plan

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
    resolved_tool = resolve_ai_tool(ai_tool, config, command="review_plan")
    resolved_model = resolve_model(model, config, command="review_plan", tool=resolved_tool)

    request = DelegationRequest(
        mode=delegation_mode,
        prompt=prompt,
        ai_tool=resolved_tool,
        model=resolved_model,
        effort=cmd_config.effort,
    )

    result = delegate(request)
    if result.success:
        console.out.print(result.feedback)
    else:
        console.error(result.feedback)
    return result


def review_implementation(
    *,
    staged: bool = False,
    ai_tool: str | None = None,
    model: str | None = None,
    mode: str | None = None,
) -> DelegationResult:
    """Review implementation changes via the delegation infrastructure."""
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
        )

    template = load_prompt_template("review-code.md")
    prompt = template.replace("{diff_content}", diff_content)

    config = load_config()
    cmd_config = config.ai.review_implementation

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
    resolved_tool = resolve_ai_tool(ai_tool, config, command="review_implementation")
    resolved_model = resolve_model(
        model, config, command="review_implementation", tool=resolved_tool
    )

    request = DelegationRequest(
        mode=delegation_mode,
        prompt=prompt,
        ai_tool=resolved_tool,
        model=resolved_model,
        effort=cmd_config.effort,
    )

    delegation_result = delegate(request)
    if delegation_result.success:
        console.out.print(delegation_result.feedback)
    else:
        console.error(delegation_result.feedback)
    return delegation_result
