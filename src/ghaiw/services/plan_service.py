"""Plan service — AI-assisted planning session orchestration.

Implements the two-phase planning design:
  Phase 1: Launch AI with clipboard prompt, let it write plan files to temp dir
  Phase 2: After AI exits, detect new issues (Path A) or read plan files (Path B)

Behavioral reference: lib/task/crud.sh (_task_do_plan, _task_run_ai_planning)
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import structlog

from ghaiw.ai_tools.base import AbstractAITool
from ghaiw.ai_tools.transcript import (
    extract_token_usage_from_text,
    read_transcript_excerpt,
)
from ghaiw.config.loader import load_config
from ghaiw.models.ai import AIToolID, TokenUsage
from ghaiw.models.config import ProjectConfig
from ghaiw.models.task import PlanFile
from ghaiw.providers.base import AbstractTaskProvider
from ghaiw.providers.registry import get_provider
from ghaiw.services.task_service import (
    add_planned_by_labels,
    apply_plan_token_usage,
    create_from_plan_file,
    ensure_issue_label,
)
from ghaiw.ui.console import console
from ghaiw.utils.clipboard import copy_to_clipboard
from ghaiw.utils.process import run_with_transcript

logger = structlog.get_logger()


def get_plan_prompt_template() -> str:
    """Load the plan session prompt template."""
    from ghaiw.skills.installer import get_templates_dir

    template = get_templates_dir() / "prompts" / "plan-session.md"
    if not template.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template}")
    return template.read_text(encoding="utf-8")


def render_plan_prompt(plan_dir: str) -> str:
    """Render the plan prompt template with the plan directory."""
    template = get_plan_prompt_template()
    return template.replace("{plan_dir}", plan_dir)


def _resolve_ai_tool(
    ai_tool: str | None,
    config: ProjectConfig,
    command: str = "plan",
) -> str | None:
    """Resolve AI tool from args → config → detection.

    Behavioral reference: _prompt_ai_tool_for_command() in common.sh
    """
    if ai_tool:
        return ai_tool

    # Config fallback chain: command-specific → global default
    config_tool = config.get_ai_tool(command)
    if config_tool:
        return config_tool

    # Detect installed tools
    installed = AbstractAITool.detect_installed()
    if installed:
        return installed[0].value

    return None


def _resolve_model(
    model: str | None,
    config: ProjectConfig,
    command: str = "plan",
) -> str | None:
    """Resolve model from args → config."""
    if model:
        return model
    return config.get_model(command)


# ---------------------------------------------------------------------------
# Plan file discovery and validation
# ---------------------------------------------------------------------------


def discover_plan_files(plan_dir: Path) -> list[Path]:
    """Find .md files in the plan directory, sorted by name.

    Behavioral reference: _task_convert_plan_to_issues() file discovery
    """
    if not plan_dir.is_dir():
        return []
    return sorted(plan_dir.glob("*.md"))


def validate_plan_files(plan_dir: Path) -> list[PlanFile]:
    """Discover and validate plan files from a directory.

    Returns only files with valid '# Title' headings.
    Behavioral reference: _task_convert_plan_to_issues()
    """
    valid: list[PlanFile] = []
    md_files = discover_plan_files(plan_dir)

    for md_file in md_files:
        try:
            plan = PlanFile.from_markdown(md_file)
            valid.append(plan)
        except (ValueError, OSError) as e:
            console.warn(f"Skipping {md_file.name}: {e}")

    return valid


# ---------------------------------------------------------------------------
# AI session runner
# ---------------------------------------------------------------------------


def run_ai_planning_session(
    ai_tool: str,
    plan_dir: str,
    model: str | None = None,
    transcript_path: Path | None = None,
) -> int:
    """Launch the AI CLI for a planning session.

    Copies the plan prompt to clipboard, launches the AI tool with
    plan-mode and plan-directory permission args.

    Behavioral reference: _task_run_ai_planning() in crud.sh
    """
    # Build and copy prompt
    prompt = render_plan_prompt(plan_dir)

    # For Copilot/Codex, prefix with /plan
    tool_lower = ai_tool.lower()
    if tool_lower in ("copilot", "codex"):
        prompt = f"/plan {prompt}"

    copy_to_clipboard(prompt)
    prompt_file = Path(plan_dir) / "prompt.txt"
    prompt_file.write_text(prompt)
    console.success("Copied planning prompt to clipboard.")
    preview_lines = prompt.splitlines()[:3]
    preview = "  " + "\n  ".join(preview_lines) + "\n  …"
    console.out.print(f"[dim]{preview}[/]")
    console.hint(f"Paste it in the AI tool to get started.  (full prompt: {prompt_file})")

    # Resolve adapter
    try:
        adapter = AbstractAITool.get(AIToolID(ai_tool))
    except (ValueError, KeyError):
        console.warn(f"Unknown AI tool: {ai_tool} — launching directly")
        result = subprocess.run([ai_tool], cwd=None)
        return result.returncode

    # Build command with plan-mode, trusted dirs, and plan-dir permission args
    cmd = adapter.build_launch_command(
        model=model,
        plan_mode=True,
        trusted_dirs=[str(Path.cwd()), "/tmp"],
    )
    plan_dir_args = adapter.plan_dir_args(plan_dir)
    if plan_dir_args:
        cmd.extend(plan_dir_args)
        console.info(f"Plan directory: {plan_dir}")

    console.empty()
    logger.info(
        "plan.ai_launch",
        tool=ai_tool,
        model=model,
        cmd=" ".join(cmd),
    )

    return run_with_transcript(cmd, transcript_path, cwd=Path.cwd())


# ---------------------------------------------------------------------------
# Post-session processing
# ---------------------------------------------------------------------------


def _extract_token_usage(transcript_path: Path | None) -> TokenUsage:
    """Extract token usage from a transcript file."""
    if not transcript_path or not transcript_path.is_file():
        return TokenUsage()
    text = read_transcript_excerpt(transcript_path)
    return extract_token_usage_from_text(text)


def _warn_token_extraction(transcript_path: Path | None) -> None:
    """Warn the user that token usage could not be extracted.

    If a transcript file exists, copies it to a stable debug path so the
    user can inspect it after the temp plan directory is cleaned up.
    """
    if not transcript_path or not transcript_path.is_file():
        logger.warning("plan.transcript_not_captured", path=str(transcript_path))
        console.warn("Session transcript was not captured — token usage unavailable.")
        return

    fd, debug_path_str = tempfile.mkstemp(prefix="ghaiw-transcript-", suffix=".txt")
    os.close(fd)
    debug_path = Path(debug_path_str)
    try:
        shutil.copy2(transcript_path, debug_path)
        logger.warning("plan.token_extraction_failed", transcript=str(debug_path))
        console.warn("Could not extract token usage from session transcript.")
        console.hint(f"Transcript saved for inspection: {debug_path}")
    except OSError:
        logger.warning("plan.token_extraction_failed", transcript=str(transcript_path))
        console.warn("Could not extract token usage from session transcript.")


# ---------------------------------------------------------------------------
# Main plan orchestrator
# ---------------------------------------------------------------------------


def plan(
    ai_tool: str | None = None,
    model: str | None = None,
    project_root: Path | None = None,
) -> bool:
    """Run an AI-assisted planning session.

    Two-path design:
      Path A: Issues created during AI session (detected via snapshot/diff)
      Path B: Plan files written to temp dir → issues created from files

    Behavioral reference: lib/task/crud.sh:_task_do_plan()
    """
    config = load_config(project_root)
    provider = get_provider(config)

    # Resolve AI tool and model
    resolved_tool = _resolve_ai_tool(ai_tool, config, "plan")
    if not resolved_tool:
        console.error("No AI tool specified and none detected. Use --ai <tool>.")
        return False

    resolved_model = _resolve_model(model, config, "plan")

    console.rule("ghaiwpy task plan")
    console.kv("AI tool", resolved_tool)
    if resolved_model:
        console.kv("Model", resolved_model)

    # Ensure task label exists
    ensure_issue_label(provider, config.project.issue_label)

    # Create temp directory for plan files
    plan_dir = tempfile.mkdtemp(prefix="ghaiw-plan-")

    # Snapshot current issue numbers (for Path A detection)
    before_snapshot = provider.snapshot_task_numbers(
        label=config.project.issue_label,
    )
    logger.info(
        "plan.snapshot",
        count=len(before_snapshot),
        numbers=sorted(before_snapshot),
    )

    # Set up transcript capture
    transcript_path = Path(plan_dir) / ".transcript"
    console.hint(f"Transcript: {transcript_path}")

    # Launch AI session
    console.empty()
    exit_code = run_ai_planning_session(
        ai_tool=resolved_tool,
        plan_dir=plan_dir,
        model=resolved_model,
        transcript_path=transcript_path,
    )
    logger.info("plan.ai_exited", exit_code=exit_code)

    # Post-session: extract token usage
    usage = _extract_token_usage(transcript_path)
    if not usage.total_tokens:
        _warn_token_extraction(transcript_path)

    # Path A: Check for issues created during AI session
    after_snapshot = provider.snapshot_task_numbers(
        label=config.project.issue_label,
    )
    new_issue_numbers = sorted(after_snapshot - before_snapshot)

    if new_issue_numbers:
        console.success(f"Detected {len(new_issue_numbers)} new issue(s)")
        _finalize_issues(
            provider=provider,
            config=config,
            issue_numbers=new_issue_numbers,
            ai_tool=resolved_tool,
            model=resolved_model,
            usage=usage,
        )
        _cleanup_plan_dir(plan_dir)
        return True

    # Path B: Check for plan files in temp dir
    plan_files = validate_plan_files(Path(plan_dir))

    if plan_files:
        console.info(f"Found {len(plan_files)} plan file(s)")
        created_numbers = _create_issues_from_plans(
            provider=provider,
            config=config,
            plan_files=plan_files,
        )
        if created_numbers:
            _finalize_issues(
                provider=provider,
                config=config,
                issue_numbers=created_numbers,
                ai_tool=resolved_tool,
                model=resolved_model,
                usage=usage,
            )
            _cleanup_plan_dir(plan_dir)
            return True
        console.warn("No issues were created from plan files.")
    else:
        console.warn("No issues detected and no plan files found.")
        console.hint("The AI session may not have produced any output.")

    _cleanup_plan_dir(plan_dir)
    return False


def _create_issues_from_plans(
    provider: AbstractTaskProvider,
    config: ProjectConfig,
    plan_files: list[PlanFile],
) -> list[str]:
    """Create GitHub issues from validated plan files.

    Returns list of created issue numbers.
    """
    created: list[str] = []
    for plan in plan_files:
        task = create_from_plan_file(
            plan_file=plan.path,
            config=config,
            provider=provider,
        )
        if task:
            created.append(task.id)
    return created


def _finalize_issues(
    provider: AbstractTaskProvider,
    config: ProjectConfig,
    issue_numbers: list[str],
    ai_tool: str | None = None,
    model: str | None = None,
    usage: TokenUsage | None = None,
) -> None:
    """Finalize newly created issues: token summaries, labels, hints.

    Behavioral reference: lib/task/crud.sh:_task_plan_finalize_issues()
    """
    # Apply token usage to issue bodies
    if usage:
        apply_plan_token_usage(
            provider=provider,
            issue_numbers=issue_numbers,
            ai_tool=ai_tool,
            model=model,
            total_tokens=usage.total_tokens,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
            premium_requests=usage.premium_requests,
            model_breakdown=(
                [
                    {
                        "model": b.model,
                        "input": b.input_tokens,
                        "output": b.output_tokens,
                        "cached": b.cached_tokens,
                    }
                    for b in usage.model_breakdown
                ]
                if usage.model_breakdown
                else None
            ),
        )

    # Add planned-by labels
    for issue_id in issue_numbers:
        add_planned_by_labels(provider, issue_id, ai_tool, model)

    # Auto-dependency analysis for 2+ issues
    if len(issue_numbers) >= 2:
        console.empty()
        console.step("Running automatic dependency analysis...")
        try:
            from ghaiw.services.deps_service import analyze_deps

            graph = analyze_deps(
                issue_numbers=issue_numbers,
                ai_tool=ai_tool,
                model=model,
            )
            if graph and graph.edges:
                console.success(f"Applied {len(graph.edges)} dependency edge(s)")
            elif graph is not None:
                console.info("No dependencies found between issues.")
        except Exception as e:
            logger.warning("plan.auto_deps_failed", error=str(e))
            console.warn(f"Auto-dependency analysis failed: {e}")

    # List created issues
    console.empty()
    issue_lines = []
    for issue_id in issue_numbers:
        try:
            task = provider.read_task(issue_id)
            issue_lines.append(f"  {console.issue_ref(task.id, task.title)}")
        except Exception:
            logger.debug("plan.issue_read_failed", issue_id=issue_id, exc_info=True)
            issue_lines.append(f"  {console.issue_ref(issue_id)}")
    console.panel("\n".join(issue_lines), title=f"Created {len(issue_numbers)} issue(s)")

    # Hint for next steps
    console.empty()
    console.info("When you're ready to start, run:")
    if issue_numbers:
        console.detail(f"ghaiwpy work start {issue_numbers[0]}")


def _cleanup_plan_dir(plan_dir: str) -> None:
    """Remove the temporary plan directory."""
    with contextlib.suppress(Exception):
        shutil.rmtree(plan_dir, ignore_errors=True)
