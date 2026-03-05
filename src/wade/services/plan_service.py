"""Plan service — AI-assisted planning session orchestration.

Implements a two-phase planning design:
  Phase 1: Launch AI with initial prompt, let it write plan files to temp dir
  Phase 2: After AI exits, read plan files from temp dir and create issues
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import structlog

from wade.ai_tools.base import AbstractAITool
from wade.ai_tools.transcript import (
    extract_token_usage_from_text,
    read_transcript_excerpt,
)
from wade.config.loader import load_config
from wade.models.ai import AIToolID, TokenUsage
from wade.models.config import ProjectConfig
from wade.models.task import PlanFile, Task
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services.ai_resolution import confirm_ai_selection, resolve_ai_tool, resolve_model
from wade.services.prompt_delivery import deliver_prompt_if_needed
from wade.services.task_service import (
    add_complexity_label,
    add_planned_by_labels,
    apply_plan_token_usage,
    ensure_task_label,
)
from wade.services.work_service import bootstrap_draft_pr
from wade.ui import prompts
from wade.ui.console import console
from wade.utils.markdown import append_session_to_body
from wade.utils.process import run_with_transcript

logger = structlog.get_logger()


def get_plan_prompt_template() -> str:
    """Load the plan session prompt template."""
    from wade.skills.installer import get_templates_dir

    template = get_templates_dir() / "prompts" / "plan-session.md"
    if not template.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template}")
    return template.read_text(encoding="utf-8")


def render_plan_prompt(plan_dir: str, issue_context: str | None = None) -> str:
    """Render the plan prompt template with the plan directory."""
    template = get_plan_prompt_template()
    prompt = template.replace("{plan_dir}", plan_dir)
    if issue_context:
        prompt = issue_context + "\n\n" + prompt
    return prompt


def _build_issue_context_header(issue: Task) -> str:
    """Build a Markdown header injected into the planning prompt for an existing issue."""
    body = (issue.body or "_No description provided._").strip()
    lines = [
        f"# Existing Issue #{issue.id}: {issue.title}",
        "",
        "You are planning the following existing GitHub issue.",
        "**Do NOT ask the user what to plan** — skip step 1 and go straight to analysis.",
        "Write the plan for this specific issue and save it to the plan directory.",
        "",
        "## Issue Details",
        "",
        body,
        "",
        "---",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plan file discovery and validation
# ---------------------------------------------------------------------------


def discover_plan_files(plan_dir: Path) -> list[Path]:
    """Find .md files in the plan directory, sorted by name."""
    if not plan_dir.is_dir():
        return []
    return sorted(plan_dir.glob("*.md"))


def validate_plan_files(plan_dir: Path) -> list[PlanFile]:
    """Discover and validate plan files from a directory.

    Returns only files with valid '# Title' headings.
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
    issue_context: str | None = None,
) -> int:
    """Launch the AI CLI for a planning session.

    Launches the AI tool with the plan prompt as an initial message,
    plan-mode and plan-directory permission args.

    """
    # Build prompt
    prompt = render_plan_prompt(plan_dir, issue_context=issue_context)

    # For Copilot/Codex, prefix with /plan
    tool_lower = ai_tool.lower()
    if tool_lower in ("copilot", "codex"):
        prompt = f"/plan {prompt}"

    prompt_file = Path(plan_dir) / "prompt.txt"
    prompt_file.write_text(prompt)
    snippet = "\n".join(prompt.splitlines()[:5]) + "\n…"
    console.panel(snippet, title="Planning Prompt (preview)")

    # Resolve adapter
    try:
        adapter = AbstractAITool.get(AIToolID(ai_tool))
    except (ValueError, KeyError):
        console.warn(f"Unknown AI tool: {ai_tool} — launching directly")
        try:
            result = subprocess.run([ai_tool], cwd=None)
        except FileNotFoundError:
            console.error(f"AI tool binary not found: {ai_tool}")
            return 1
        return result.returncode

    # Check model compatibility — drop model if it's not valid for this tool
    if model and not adapter.is_model_compatible(model):
        console.warn(f"Model '{model}' is not compatible with {ai_tool}; using tool default")
        model = None

    deliver_prompt_if_needed(adapter, prompt)

    # Build command — plan_dir included in trusted_dirs so all flags precede the
    # initial_message positional arg (many CLIs stop flag-parsing after a positional).
    cmd = adapter.build_launch_command(
        model=model,
        plan_mode=True,
        trusted_dirs=[str(Path.cwd()), tempfile.gettempdir(), plan_dir],
        initial_message=prompt,
    )
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

    fd, debug_path_str = tempfile.mkstemp(prefix="wade-transcript-", suffix=".txt")
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
    issue_id: str | None = None,
    *,
    ai_explicit: bool = False,
    model_explicit: bool = False,
) -> bool:
    """Run an AI-assisted planning session.

    The AI writes plan files to a temp dir. After the session ends, wade reads
    them back and creates issues + draft PRs.

    When ``issue_id`` is provided the session is pre-loaded with that issue's
    context and the resulting plan is attached to it via draft PR (no new issue).
    """
    config = load_config(project_root)
    provider = get_provider(config)

    # Resolve AI tool and model
    resolved_tool = resolve_ai_tool(ai_tool, config, "plan")
    if not resolved_tool:
        console.error("No AI tool specified and none detected. Use --ai <tool>.")
        return False

    resolved_model = resolve_model(model, config, "plan", tool=resolved_tool)

    console.rule("wade plan-task")

    # Offer interactive confirmation unless both flags were explicitly provided.
    resolved_tool, resolved_model = confirm_ai_selection(
        resolved_tool,
        resolved_model,
        tool_explicit=ai_explicit,
        model_explicit=model_explicit,
    )
    if not resolved_tool:
        console.error("No AI tool selected.")
        return False

    # Pre-load existing issue context when issue_id is supplied
    existing_issue: Task | None = None
    if issue_id:
        try:
            existing_issue = provider.read_task(issue_id)
            console.kv("Issue", f"#{existing_issue.id}: {existing_issue.title}")
        except Exception as e:
            console.error(f"Could not fetch issue #{issue_id}: {e}")
            return False

    # Resolve repo root for draft PR creation
    from wade.git import repo as git_repo

    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except Exception:
        console.warn("Not in a git repo — draft PRs will not be created.")
        repo_root = None

    # Ensure task label exists
    ensure_task_label(provider, config.project.issue_label)

    # Create temp directory for plan files
    plan_dir = tempfile.mkdtemp(prefix="wade-plan-")

    # Set up transcript capture
    transcript_path = Path(plan_dir) / ".transcript"
    console.hint(f"Transcript: {transcript_path}")

    # Launch AI session
    console.empty()
    issue_context = _build_issue_context_header(existing_issue) if existing_issue else None
    exit_code = run_ai_planning_session(
        ai_tool=resolved_tool,
        plan_dir=plan_dir,
        model=resolved_model,
        transcript_path=transcript_path,
        issue_context=issue_context,
    )
    logger.info("plan.ai_exited", exit_code=exit_code)

    # Non-blocking tools (VS Code, Antigravity) return immediately.
    # Wait for user confirmation before post-session processing.
    try:
        tool_adapter = AbstractAITool.get(AIToolID(resolved_tool))
        tool_caps = tool_adapter.capabilities()
    except (ValueError, KeyError):
        tool_caps = None

    if tool_caps and not tool_caps.blocks_until_exit:
        console.empty()
        if not prompts.confirm("Have you finished the session?", default=True):
            console.info("Plan directory preserved — review output manually.")
            console.hint(f"Plan dir: {plan_dir}")
            return False

    # Post-session: extract token usage (skip for non-blocking tools)
    usage = TokenUsage()
    if not tool_caps or tool_caps.blocks_until_exit:
        usage = _extract_token_usage(transcript_path)
        if not usage.total_tokens:
            _warn_token_extraction(transcript_path)

    # Existing issue — attach plan files to it (no new issue created)
    if existing_issue is not None:
        plan_files = validate_plan_files(Path(plan_dir))
        if plan_files:
            console.info(f"Found {len(plan_files)} plan file(s)")
            _attach_plan_to_existing_issue(
                provider=provider,
                config=config,
                issue=existing_issue,
                plan_files=plan_files,
                repo_root=repo_root,
            )
            _finalize_issues(
                provider=provider,
                config=config,
                issue_numbers=[existing_issue.id],
                ai_tool=resolved_tool,
                model=resolved_model,
                usage=usage,
                repo_root=repo_root,
            )
            _cleanup_plan_dir(plan_dir)
            return True
        console.warn("No plan files found — the AI session may not have produced output.")
        _cleanup_plan_dir(plan_dir)
        return False

    # Read plan files from temp dir and create issues
    plan_files = validate_plan_files(Path(plan_dir))

    if plan_files:
        console.info(f"Found {len(plan_files)} plan file(s)")
        created_numbers = _create_issues_from_plans(
            provider=provider,
            config=config,
            plan_files=plan_files,
            repo_root=repo_root,
        )
        if created_numbers:
            _finalize_issues(
                provider=provider,
                config=config,
                issue_numbers=created_numbers,
                ai_tool=resolved_tool,
                model=resolved_model,
                usage=usage,
                repo_root=repo_root,
            )
            _cleanup_plan_dir(plan_dir)
            return True
        console.warn("No issues were created from plan files.")
    else:
        console.warn("No plan files found.")
        console.hint("The AI session may not have produced any output.")

    _cleanup_plan_dir(plan_dir)
    return False


def _build_lightweight_issue_body(plan: PlanFile) -> str:
    """Extract a brief context from the plan for the lightweight issue body.

    Takes the first ~500 characters of the ``## Context / Problem`` section
    (or falls back to the first paragraph of the full body).
    """
    # Try to find a context section
    for key in ("context / problem", "context", "problem"):
        if key in plan.sections:
            text = plan.sections[key].strip()
            if len(text) > 500:
                # Truncate at sentence boundary if possible
                cut = text[:500].rfind(". ")
                text = text[: cut + 1] if cut > 250 else text[:500] + "…"
            return text

    # Fallback: first paragraph of the body
    paragraphs = plan.body.split("\n\n")
    if paragraphs:
        text = paragraphs[0].strip()
        if text.startswith("## "):
            # Skip the heading, take the next paragraph
            text = paragraphs[1].strip() if len(paragraphs) > 1 else ""
        if len(text) > 500:
            cut = text[:500].rfind(". ")
            text = text[: cut + 1] if cut > 250 else text[:500] + "…"
        return text

    return ""


def _create_issues_from_plans(
    provider: AbstractTaskProvider,
    config: ProjectConfig,
    plan_files: list[PlanFile],
    repo_root: Path | None = None,
) -> list[str]:
    """Create lightweight GitHub issues + draft PRs from validated plan files.

    Each plan file produces:
    1. A lightweight issue (title + brief context)
    2. A ``complexity:X`` label
    3. A draft PR with the full plan content

    Returns list of created issue numbers.
    """
    from wade.git import repo as git_repo

    created: list[str] = []

    # Resolve repo root for draft PR creation
    if repo_root is None:
        try:
            repo_root = git_repo.get_repo_root(Path.cwd())
        except Exception:
            console.warn("Not in a git repo — skipping draft PR creation.")
            repo_root = None

    for plan in plan_files:
        # Build lightweight body
        brief_body = _build_lightweight_issue_body(plan)

        # Create the issue with lightweight body
        console.step(f"Creating issue: {plan.title}")
        try:
            task = provider.create_task(
                title=plan.title,
                body=brief_body,
                labels=[config.project.issue_label],
            )
            console.success(f"Created {console.issue_ref(task.id, task.title)}")
        except Exception as e:
            console.error(f"Failed to create issue: {e}")
            continue

        # Add complexity label
        if plan.complexity:
            try:
                add_complexity_label(provider, task.id, plan.complexity)
            except Exception as e:
                logger.warning("plan.complexity_label_failed", error=str(e))

        # Bootstrap draft PR with full plan content
        if repo_root is not None:
            pr_info = bootstrap_draft_pr(
                issue_number=task.id,
                issue_title=task.title,
                plan_body=plan.body,
                config=config,
                repo_root=repo_root,
            )
            if pr_info:
                pr_number = pr_info.get("number", "?")
                pr_url = pr_info.get("url", "")
                console.success(f"Draft PR #{pr_number}: {pr_url}")

                # Update issue body with PR link
                updated_body = brief_body.rstrip("\n") + f"\n\n**Full plan**: PR #{pr_number}"
                try:
                    provider.update_task(task.id, body=updated_body)
                except Exception as e:
                    logger.warning("plan.pr_link_update_failed", error=str(e))
            else:
                console.warn(f"Could not create draft PR for #{task.id}")

        created.append(task.id)

    return created


def _attach_plan_to_existing_issue(
    provider: AbstractTaskProvider,
    config: ProjectConfig,
    issue: Task,
    plan_files: list[PlanFile],
    repo_root: Path | None,
) -> None:
    """Attach the first plan file to an existing issue via a draft PR.

    Reuses the same label / PR / body-update logic as _create_issues_from_plans
    but skips issue creation since the issue already exists.  The original issue
    body is preserved — the PR link is appended rather than replacing it.
    """
    if len(plan_files) > 1:
        console.warn(
            f"Multiple plan files found — using '{plan_files[0].path.name}', "
            f"ignoring {len(plan_files) - 1} other(s)."
        )
    plan = plan_files[0]

    # Add complexity label
    if plan.complexity:
        try:
            add_complexity_label(provider, issue.id, plan.complexity)
        except Exception as e:
            logger.warning("plan.complexity_label_failed", error=str(e))

    # Bootstrap draft PR with full plan content
    if repo_root is not None:
        pr_info = bootstrap_draft_pr(
            issue_number=issue.id,
            issue_title=issue.title,
            plan_body=plan.body,
            config=config,
            repo_root=repo_root,
        )
        if pr_info:
            pr_number = pr_info.get("number", "?")
            pr_url = pr_info.get("url", "")
            console.success(f"Draft PR #{pr_number}: {pr_url}")

            # Preserve the original issue body and append the PR link
            original_body = (issue.body or "").rstrip("\n")
            updated_body = original_body + f"\n\n**Full plan**: PR #{pr_number}"
            try:
                provider.update_task(issue.id, body=updated_body)
            except Exception as e:
                logger.warning("plan.pr_link_update_failed", error=str(e))
        else:
            console.warn(f"Could not create draft PR for #{issue.id}")
    else:
        console.warn("Not in a git repo — skipping draft PR creation.")


def _finalize_issues(
    provider: AbstractTaskProvider,
    config: ProjectConfig,
    issue_numbers: list[str],
    ai_tool: str | None = None,
    model: str | None = None,
    usage: TokenUsage | None = None,
    repo_root: Path | None = None,
) -> None:
    """Finalize newly created issues: token summaries, labels, hints."""
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

    # Record plan session ID in issue bodies and draft PR bodies
    if usage and usage.session_id:
        for issue_id in issue_numbers:
            # Update issue body
            with contextlib.suppress(Exception):
                task = provider.read_task(issue_id)
                new_body = append_session_to_body(
                    task.body,
                    phase="Plan",
                    ai_tool=ai_tool or "",
                    session_id=usage.session_id,
                )
                provider.update_task(issue_id, body=new_body)
                logger.info("plan.session_id_recorded", issue=issue_id)

    # Add planned-by labels
    for issue_id in issue_numbers:
        try:
            add_planned_by_labels(provider, issue_id, ai_tool, model)
        except Exception as e:
            console.warn(f"Could not apply planned-by labels to #{issue_id}: {e}")
            logger.warning("plan.planned_by_labels_failed", task_id=issue_id, error=str(e))

    # Auto-dependency analysis for 2+ issues
    if len(issue_numbers) >= 2:
        console.empty()
        console.step("Running automatic dependency analysis...")
        try:
            from wade.services.deps_service import analyze_deps

            graph = analyze_deps(
                issue_numbers=issue_numbers,
                ai_tool=ai_tool,
                model=model,
                ai_explicit=True,
                model_explicit=True,
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
    console.info("When you're ready to implement, run:")
    if issue_numbers:
        console.detail(f"wade implement-task {issue_numbers[0]}")


def _cleanup_plan_dir(plan_dir: str) -> None:
    """Remove the temporary plan directory."""
    with contextlib.suppress(Exception):
        shutil.rmtree(plan_dir, ignore_errors=True)
