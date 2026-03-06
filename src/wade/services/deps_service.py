"""Dependency analysis service — parse, graph, apply, track.

Orchestrates: building context from issues, running AI analysis (headless),
parsing edges, applying cross-references, and creating tracking issues.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import structlog

from wade.ai_tools.base import AbstractAITool
from wade.config.loader import load_config
from wade.models.ai import AIToolID
from wade.models.config import ProjectConfig
from wade.models.deps import DependencyEdge, DependencyGraph
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services.ai_resolution import confirm_ai_selection, resolve_ai_tool, resolve_model
from wade.services.prompt_delivery import deliver_prompt_if_needed
from wade.services.task_service import ensure_task_label
from wade.ui import prompts
from wade.ui.console import console
from wade.utils.process import CommandError, run

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------


def get_deps_prompt_template() -> str:
    """Load the dependency analysis prompt template."""
    from wade.skills.installer import get_templates_dir

    template = get_templates_dir() / "prompts" / "deps-analysis.md"
    if not template.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template}")
    return template.read_text(encoding="utf-8")


def get_deps_interactive_template() -> str:
    """Load the interactive fallback output instruction template."""
    from wade.skills.installer import get_templates_dir

    template = get_templates_dir() / "prompts" / "deps-interactive.md"
    if not template.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template}")
    return template.read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------


def build_context(
    provider: AbstractTaskProvider,
    issue_numbers: list[str],
) -> str:
    """Build a context string with issue details for AI consumption."""
    lines: list[str] = []
    for num in issue_numbers:
        try:
            task = provider.read_task(num)
            lines.append(f"## Issue #{num}: {task.title}")
            lines.append("")
            if task.body:
                lines.append(task.body.strip())
            lines.append("")
        except Exception as e:
            logger.warning("deps.context_failed", issue=num, error=str(e))
            lines.append(f"## Issue #{num}: (could not read)")
            lines.append("")
    return "\n".join(lines)


def build_deps_prompt(context: str) -> str:
    """Build the full dependency analysis prompt from context."""
    template = get_deps_prompt_template()
    return template.replace("{context}", context)


# ---------------------------------------------------------------------------
# Edge parsing
# ---------------------------------------------------------------------------

# Regex for "X -> Y" edges with optional "# reason" comment
_ARROW_RE = re.compile(
    r"^\s*(\d+)\s*->\s*(\d+)(.*?)$",
    re.MULTILINE,
)
_COMMENT_RE = re.compile(r"#\s*(.*)")


def parse_deps_output(
    text: str,
    valid_numbers: set[str],
) -> list[DependencyEdge]:
    """Parse dependency edges from AI output text.

    Args:
        text: Raw AI output containing "X -> Y # reason" lines.
        valid_numbers: Set of valid issue numbers to filter against.

    Returns:
        List of validated DependencyEdge objects.
    """
    edges: list[DependencyEdge] = []

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Strip markdown formatting (backticks, bullets, numbering)
        cleaned = line.replace("`", "")
        cleaned = re.sub(r"^[-*]\s+", "", cleaned)
        cleaned = re.sub(r"^\d+[.)]\s+", "", cleaned)

        match = _ARROW_RE.match(cleaned)
        if not match:
            continue

        from_id = match.group(1)
        to_id = match.group(2)
        rest = match.group(3)

        # Validate both numbers
        if from_id not in valid_numbers or to_id not in valid_numbers:
            console.warn(f"Skipping invalid edge {from_id} -> {to_id} (unknown issue number)")
            continue

        # Extract comment
        reason = ""
        comment_match = _COMMENT_RE.search(rest)
        if comment_match:
            reason = comment_match.group(1).strip()

        edges.append(DependencyEdge(from_task=from_id, to_task=to_id, reason=reason))

    return edges


def output_is_parseable(text: str) -> bool:
    """Check if AI output contains parseable dependency edges or "no deps"."""
    if "# No dependencies found" in text:
        return True
    return bool(_ARROW_RE.search(text))


# ---------------------------------------------------------------------------
# Cross-reference injection
# ---------------------------------------------------------------------------

_DEPS_SECTION_RE = re.compile(
    r"## Dependencies\n.*?(?=\n## (?!Dependencies\n)|\Z)",
    re.DOTALL,
)


def strip_deps_section(body: str) -> str:
    """Remove existing ## Dependencies section from issue body."""
    # Try regex first (handles Dependencies followed by another section)
    result = _DEPS_SECTION_RE.sub("", body)
    # Handle case where Dependencies is the last section
    idx = result.find("## Dependencies")
    if idx != -1:
        result = result[:idx]
    return result.rstrip("\n") + "\n" if result.strip() else ""


def build_deps_section(
    issue_id: str,
    edges: list[DependencyEdge],
) -> str:
    """Build the ## Dependencies section for a single issue.

    Shows "Depends on" and "Blocks" references.
    """
    depends_on: list[str] = []
    blocks: list[str] = []

    for edge in edges:
        if edge.to_task == issue_id:
            depends_on.append(f"#{edge.from_task}")
        if edge.from_task == issue_id:
            blocks.append(f"#{edge.to_task}")

    if not depends_on and not blocks:
        return ""

    lines = ["## Dependencies", ""]
    if depends_on:
        lines.append(f"**Depends on:** {', '.join(depends_on)}")
    if blocks:
        lines.append(f"**Blocks:** {', '.join(blocks)}")
    lines.append("")

    return "\n".join(lines)


def apply_deps_to_issues(
    provider: AbstractTaskProvider,
    issue_numbers: list[str],
    edges: list[DependencyEdge],
) -> int:
    """Update each issue body with dependency cross-references.

    Returns number of successfully updated issues.
    """
    updated = 0

    for issue_id in issue_numbers:
        deps_section = build_deps_section(issue_id, edges)

        try:
            task = provider.read_task(issue_id)
            cleaned_body = strip_deps_section(task.body)

            if deps_section:
                new_body = cleaned_body.rstrip("\n") + "\n\n" + deps_section
            elif "## Dependencies" in task.body:
                # No edges remain but a stale ## Dependencies block was stripped
                new_body = cleaned_body
            else:
                continue

            provider.update_task(issue_id, body=new_body)
            console.detail(f"Updated #{issue_id} with dependency refs")
            updated += 1
        except Exception as e:
            logger.warning("deps.update_failed", issue=issue_id, error=str(e))

    return updated


# ---------------------------------------------------------------------------
# Tracking issue
# ---------------------------------------------------------------------------


def create_tracking_issue(
    provider: AbstractTaskProvider,
    config: ProjectConfig,
    issue_numbers: list[str],
    graph: DependencyGraph,
    task_titles: dict[str, str],
) -> str | None:
    """Create a tracking issue with execution plan and dependency graph.

    Returns the tracking issue ID, or None on failure.
    """
    # Compute execution order
    try:
        ordered = graph.topo_sort(issue_numbers)
    except ValueError:
        # Cycle detected — use original order
        ordered = issue_numbers

    # Build checklist body
    lines = ["## Execution Plan", ""]
    for num in ordered:
        title = task_titles.get(num, f"Issue #{num}")
        lines.append(f"- [ ] #{num} — {title}")
    lines.append("")

    # Add Mermaid diagram
    mermaid = graph.generate_mermaid(task_titles)
    lines.append("## Dependency Graph")
    lines.append("")
    lines.append("```mermaid")
    lines.append(mermaid)
    lines.append("```")
    lines.append("")

    body = "\n".join(lines)

    # Determine title
    if len(issue_numbers) <= 3:
        issue_refs = ", ".join(f"#{n}" for n in issue_numbers)
        title = f"Tracking: {issue_refs}"
    else:
        title = f"Tracking: {len(issue_numbers)} issues"

    try:
        ensure_task_label(provider, config.project.issue_label)
        task = provider.create_task(
            title=title,
            body=body,
            labels=[config.project.issue_label],
        )
        console.success(f"Created tracking issue #{task.id}")
        return task.id
    except Exception as e:
        console.error(f"Failed to create tracking issue: {e}")
        return None


# ---------------------------------------------------------------------------
# AI runner (headless)
# ---------------------------------------------------------------------------


def run_headless_analysis(
    ai_tool: str,
    prompt: str,
    model: str | None = None,
    allowed_commands: list[str] | None = None,
) -> str | None:
    """Run dependency analysis in headless mode.

    Returns the AI output text, or None if headless is not supported.
    """
    try:
        adapter = AbstractAITool.get(AIToolID(ai_tool))
    except (ValueError, KeyError):
        return None

    caps = adapter.capabilities()
    if not caps.supports_headless or not caps.headless_flag:
        return None

    # Build command
    cmd = adapter.build_launch_command(
        model=model,
        prompt=prompt,
        trusted_dirs=[str(Path.cwd()), tempfile.gettempdir()],
        allowed_commands=allowed_commands,
    )

    try:
        result = run(cmd, check=False, timeout=120)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (subprocess.TimeoutExpired, CommandError) as e:
        logger.warning("deps.headless_failed", tool=ai_tool, error=str(e))

    return None


# ---------------------------------------------------------------------------
# Interactive fallback
# ---------------------------------------------------------------------------


def _run_interactive_analysis(
    ai_tool: str,
    prompt: str,
    model: str | None = None,
    plan_dir: str | None = None,
    allowed_commands: list[str] | None = None,
) -> str | None:
    """Run dependency analysis interactively when headless fails.

    Launches AI interactively with the prompt as an initial message, then reads
    the output from a temp file.
    """
    from wade.ai_tools.base import AbstractAITool
    from wade.models.ai import AIToolID

    # Set up output file for the AI to write results to
    created_tmp = plan_dir is None
    output_dir = plan_dir or tempfile.mkdtemp(prefix="wade-deps-")
    output_file = Path(output_dir) / "deps-output.txt"

    # Append output instruction to prompt
    output_instruction = get_deps_interactive_template().replace("{output_file}", str(output_file))
    interactive_prompt = f"{prompt}\n\n{output_instruction}"

    console.empty()

    try:
        # Launch AI interactively
        try:
            adapter = AbstractAITool.get(AIToolID(ai_tool))
            deliver_prompt_if_needed(adapter, interactive_prompt)
            adapter.launch(
                worktree_path=Path.cwd(),
                model=model,
                prompt=interactive_prompt,
                trusted_dirs=[str(Path.cwd()), output_dir, tempfile.gettempdir()],
                allowed_commands=allowed_commands,
            )

            # Non-blocking tools return immediately — wait for user.
            if not adapter.capabilities().blocks_until_exit:
                console.empty()
                if not prompts.confirm("Have you finished the session?", default=True):
                    return None
        except (ValueError, KeyError):
            console.warn(f"Unknown AI tool: {ai_tool}")
            return None
        except Exception as e:
            console.warn(f"AI tool launch failed: {e}")
            return None

        # Read output file after AI exits
        if output_file.is_file():
            text = output_file.read_text(encoding="utf-8").strip()
            if text:
                return text

        console.warn("No dependency output file found after interactive session.")
        return None
    finally:
        # Clean up temp dir if we created it
        if created_tmp:
            import shutil

            shutil.rmtree(output_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def analyze_deps(
    issue_numbers: list[str],
    ai_tool: str | None = None,
    model: str | None = None,
    project_root: Path | None = None,
    *,
    ai_explicit: bool = False,
    model_explicit: bool = False,
) -> DependencyGraph | None:
    """Analyze dependencies between issues.

    Steps:
    1. Build context from issue details
    2. Run AI analysis (headless preferred, interactive fallback)
    3. Parse edges
    4. Apply cross-references to issues
    5. Create tracking issue (2+ issues)

    Returns the DependencyGraph, or None on failure.
    """
    config = load_config(project_root)
    provider = get_provider(config)

    if len(issue_numbers) < 2:
        console.error("Need at least 2 issues for dependency analysis.")
        return None

    # Resolve AI tool
    resolved_tool = resolve_ai_tool(ai_tool, config, "deps")
    if not resolved_tool:
        console.error("No AI tool available for dependency analysis.")
        return None

    resolved_model = resolve_model(model, config, "deps", tool=resolved_tool)

    console.rule("wade task deps")
    console.kv("Issues", str(len(issue_numbers)))

    # Offer interactive confirmation unless both flags were explicitly provided.
    resolved_tool, resolved_model = confirm_ai_selection(
        resolved_tool,
        resolved_model,
        tool_explicit=ai_explicit,
        model_explicit=model_explicit,
    )
    if not resolved_tool:
        console.error("No AI tool selected.")
        return None

    # Build context
    context = build_context(provider, issue_numbers)
    prompt = build_deps_prompt(context)

    # Fetch titles for display
    valid_numbers = set(issue_numbers)
    task_titles: dict[str, str] = {}
    for num in issue_numbers:
        try:
            task = provider.read_task(num)
            task_titles[num] = task.title
            console.step(f"#{num}: {task.title}")
        except Exception:
            logger.debug("deps.issue_read_failed", issue_num=num, exc_info=True)
            task_titles[num] = f"Issue #{num}"

    # Run headless AI analysis
    console.step(f"Running {resolved_tool} for dependency analysis...")
    allowed_cmds = config.permissions.allowed_commands
    output = run_headless_analysis(resolved_tool, prompt, resolved_model, allowed_cmds)

    if output and output_is_parseable(output):
        console.success("Headless analysis complete.")
    else:
        if output:
            console.warn("Headless output not parseable — falling back to interactive...")
        else:
            console.info(
                f"Headless mode not available for {resolved_tool}. Falling back to interactive..."
            )
        output = _run_interactive_analysis(
            resolved_tool,
            prompt,
            resolved_model,
            plan_dir=None,
            allowed_commands=allowed_cmds,
        )

    # Parse edges
    edges = parse_deps_output(output, valid_numbers) if output else []

    if not edges:
        if output and "# No dependencies found" in output:
            console.info("No dependencies found between issues.")
        else:
            console.warn("No dependency edges parsed.")
        return DependencyGraph()

    console.success(f"Found {len(edges)} dependency edge(s)")

    # Build graph
    graph = DependencyGraph(edges=edges)

    # Generate Mermaid
    mermaid = graph.generate_mermaid(task_titles)
    graph.mermaid_diagram = mermaid
    console.empty()
    console.dep_tree([(e.from_task, e.to_task, e.reason) for e in edges], task_titles)

    # Compute topological order
    try:
        graph.topological_order = graph.topo_sort(issue_numbers)
    except ValueError:
        console.warn("Cycle detected — using original order")
        graph.topological_order = issue_numbers

    # Apply cross-references to issues
    updated = apply_deps_to_issues(provider, issue_numbers, edges)
    console.info(f"Updated {updated} issue(s) with dependency refs")

    # Create tracking issue (2+ issues)
    if len(issue_numbers) >= 2:
        tracking_id = create_tracking_issue(provider, config, issue_numbers, graph, task_titles)
        if tracking_id:
            graph.tracking_task_id = tracking_id

    return graph
