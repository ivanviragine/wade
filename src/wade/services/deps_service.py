"""Dependency analysis service — parse, graph, apply, track.

Orchestrates: building context from issues, running AI analysis via the
generic delegation infrastructure, parsing edges, applying cross-references,
and creating tracking issues.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from wade.config.loader import load_config
from wade.models.ai import EffortLevel
from wade.models.config import ProjectConfig
from wade.models.delegation import DelegationMode, DelegationRequest
from wade.models.deps import DependencyEdge, DependencyGraph
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services.ai_resolution import (
    confirm_ai_selection,
    resolve_ai_tool,
    resolve_effort,
    resolve_model,
)
from wade.services.delegation_service import delegate, resolve_mode
from wade.services.task_service import ensure_task_label
from wade.ui.console import console

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------


def get_deps_prompt_template() -> str:
    """Load the dependency analysis prompt template."""
    from wade.skills.installer import load_prompt_template

    return load_prompt_template("deps-analysis.md")


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


def _find_existing_tracking_issue(
    provider: AbstractTaskProvider,
    label: str,
    title: str,
) -> str | None:
    """Check for an existing tracking issue with the same title (any state).

    Returns the issue ID if found, None otherwise.
    Checks all states (open and closed) to prevent duplicate tracking issues
    from being created after a previous one was closed.
    """
    try:
        all_issues = provider.list_tasks(label=label, state=None)
        for issue in all_issues:
            if issue.title == title:
                return issue.id
    except Exception:
        # Non-fatal — fall through to create a new one
        pass
    return None


def create_tracking_issue(
    provider: AbstractTaskProvider,
    config: ProjectConfig,
    issue_numbers: list[str],
    graph: DependencyGraph,
    task_titles: dict[str, str],
) -> str | None:
    """Create a tracking issue with execution plan and dependency graph.

    Returns the tracking issue ID, or None on failure.
    If a tracking issue with the same title already exists, returns
    its ID instead of creating a duplicate.
    """
    # Determine title first so we can check for duplicates
    if len(issue_numbers) <= 3:
        issue_refs = ", ".join(f"#{n}" for n in issue_numbers)
        title = f"Tracking: {issue_refs}"
    else:
        title = f"Tracking: {len(issue_numbers)} issues"

    # Check for existing tracking issue with the same title
    existing_id = _find_existing_tracking_issue(provider, config.project.issue_label, title)
    if existing_id:
        console.info(f"Tracking issue #{existing_id} already exists — skipping creation")
        return existing_id

    # Compute execution order
    try:
        ordered = graph.topo_sort(issue_numbers)
    except ValueError:
        # Cycle detected — use original order
        ordered = issue_numbers

    # Build checklist body
    lines = ["## Execution Plan", ""]
    for num in ordered:
        title_text = task_titles.get(num, f"Issue #{num}")
        lines.append(f"- [ ] #{num} — {title_text}")
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
# AI delegation helpers
# ---------------------------------------------------------------------------


def _run_delegation(
    ai_tool: str,
    prompt: str,
    mode: DelegationMode,
    *,
    model: str | None = None,
    effort: str | None = None,
    allowed_commands: list[str] | None = None,
    cwd: Path | None = None,
) -> str | None:
    """Run dependency analysis via the generic delegation infrastructure.

    Returns the AI output text, or None on failure.
    """
    request = DelegationRequest(
        mode=mode,
        prompt=prompt,
        ai_tool=ai_tool,
        model=model,
        effort=effort,
        cwd=cwd,
        allowed_commands=allowed_commands or [],
    )
    result = delegate(request)
    if result.success and result.feedback:
        return result.feedback
    if not result.success:
        logger.warning("deps.delegation_failed", mode=mode.value, feedback=result.feedback)
    return None


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
    effort: str | None = None,
    effort_explicit: bool = False,
    mode: str | None = None,
    planning_worktree: Path | None = None,
) -> DependencyGraph | None:
    """Analyze dependencies between issues.

    Steps:
    1. Build context from issue details
    2. Run AI analysis via delegation infrastructure
    3. Parse edges
    4. Apply cross-references to issues
    5. Create tracking issue (2+ issues)

    Args:
        mode: Delegation mode override (prompt/headless/interactive).
            Defaults to config ``ai.deps.mode``, then ``headless``.
        planning_worktree: If provided (e.g. from plan auto-deps), reuse this
            worktree instead of creating a new one.  The worktree already has
            deps skill installed via ``PLAN_SKILLS``.

    Returns the DependencyGraph, or None on failure.
    """
    import contextlib
    import os

    config = load_config(project_root)
    provider = get_provider(config)

    if len(issue_numbers) < 2:
        console.error("Need at least 2 issues for dependency analysis.")
        return None

    # Resolve delegation mode (default to headless for deps)
    cmd_config = config.ai.deps
    if mode:
        try:
            delegation_mode = DelegationMode(mode)
        except ValueError:
            console.error(f"Invalid delegation mode: {mode}")
            return None
    else:
        # deps defaults to headless (not prompt) when no mode is configured
        delegation_mode = resolve_mode(cmd_config) if cmd_config.mode else DelegationMode.HEADLESS

    # Resolve AI tool
    resolved_tool = resolve_ai_tool(ai_tool, config, "deps")
    if not resolved_tool:
        console.error("No AI tool available for dependency analysis.")
        return None

    resolved_model = resolve_model(model, config, "deps", tool=resolved_tool)
    resolved_effort = resolve_effort(effort, config, "deps", tool=resolved_tool)

    console.rule("wade task deps")
    console.kv("Issues", str(len(issue_numbers)))

    # Offer interactive confirmation unless all flags were explicitly provided.
    resolved_tool, resolved_model, resolved_effort, _yolo = confirm_ai_selection(
        resolved_tool,
        resolved_model,
        tool_explicit=ai_explicit,
        model_explicit=model_explicit,
        resolved_effort=resolved_effort,
        effort_explicit=effort_explicit,
    )
    if not resolved_tool:
        console.error("No AI tool selected.")
        return None

    # Set up worktree for deps analysis
    standalone_worktree: Path | None = None
    deps_cwd: Path | None = None

    if planning_worktree is not None:
        # Reuse existing planning worktree (deps skill already installed)
        deps_cwd = planning_worktree
    else:
        # Standalone invocation — create a detached-HEAD worktree
        cwd = project_root or Path.cwd()
        try:
            from wade.git import repo as git_repo
            from wade.git import worktree as git_worktree
            from wade.services.work_service import _resolve_worktrees_dir, bootstrap_worktree
            from wade.skills.installer import DEPS_SKILLS

            repo_root = git_repo.get_repo_root(cwd)
            worktrees_dir = _resolve_worktrees_dir(config, repo_root)
            repo_name = repo_root.name
            short_id = os.urandom(4).hex()
            wt_dir = worktrees_dir / repo_name / f"deps-{short_id}"
            standalone_worktree = git_worktree.create_detached_worktree(
                repo_root=repo_root,
                worktree_dir=wt_dir,
            )
            bootstrap_worktree(standalone_worktree, config, repo_root, skills=DEPS_SKILLS)
            deps_cwd = standalone_worktree
        except Exception as e:
            logger.warning("deps.worktree_create_failed", error=str(e))
            # Fall through — deps_cwd stays None, analysis runs in CWD

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

    # Run AI analysis via delegation infrastructure
    effort_str = resolved_effort.value if isinstance(resolved_effort, EffortLevel) else None
    console.step(f"Running {resolved_tool} ({delegation_mode.value}) for dependency analysis...")
    output = _run_delegation(
        resolved_tool,
        prompt,
        delegation_mode,
        model=resolved_model,
        effort=effort_str,
        allowed_commands=config.permissions.allowed_commands,
        cwd=deps_cwd,
    )

    if output:
        console.success(f"Analysis complete ({delegation_mode.value} mode).")
    else:
        console.error(f"Delegation failed ({delegation_mode.value} mode).")

    # Clean up standalone worktree (planning worktree is cleaned by plan_service)
    if standalone_worktree is not None:
        with contextlib.suppress(Exception):
            from wade.git import repo as git_repo
            from wade.git import worktree as git_worktree

            repo_root = git_repo.get_repo_root(project_root or Path.cwd())
            git_worktree.remove_worktree(repo_root, standalone_worktree, force=True)

    # Prompt mode: the output is the raw template text, not AI output.
    # The user must run the prompt manually and re-run with a different mode.
    if delegation_mode == DelegationMode.PROMPT:
        console.info(
            "Prompt mode: copy the prompt above and run it manually. "
            "Then re-run with --mode headless or --mode interactive to parse results."
        )
        return DependencyGraph()

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
