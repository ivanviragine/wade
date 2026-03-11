"""Task service — CRUD operations and label management.

Orchestrates: issue creation from plan files, list/read/update/close,
label ensure/add/remove for planned-by/implemented-by metadata.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from wade.config.loader import load_config
from wade.models.config import ProjectConfig
from wade.models.task import Complexity, Label, LabelType, PlanFile, Task, TaskState
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.ui.console import console
from wade.utils.markdown import remove_marker_block
from wade.utils.token_usage_markdown import (
    TokenUsageBreakdownRow,
    build_token_usage_block,
)

logger = structlog.get_logger()

# --- Label colors (canonical, no # prefix) ---

LABEL_COLOR_ISSUE = "0E8A16"  # Green
LABEL_COLOR_IN_PROGRESS = "FBCA04"  # Yellow
LABEL_COLOR_COMPLEXITY = "C5DEF5"  # Pale blue
LABEL_COLOR_PLANNED = "BFD4F2"  # Light blue
LABEL_COLOR_IMPLEMENTED = "D4C5F9"  # Light purple
LABEL_COLOR_REVIEWED = "E8D5F5"  # Lighter purple
LABEL_COLOR_DEFAULT = "D3D3D3"  # Gray

# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------


def _label_for_type(
    label_type: LabelType,
    name: str,
    description: str,
) -> Label:
    """Build a Label with canonical color for a label type."""
    color_map = {
        LabelType.ISSUE_LABEL: LABEL_COLOR_ISSUE,
        LabelType.IN_PROGRESS: LABEL_COLOR_IN_PROGRESS,
        LabelType.COMPLEXITY: LABEL_COLOR_COMPLEXITY,
        LabelType.PLANNED_BY: LABEL_COLOR_PLANNED,
        LabelType.PLANNED_MODEL: LABEL_COLOR_PLANNED,
        LabelType.IMPLEMENTED_BY: LABEL_COLOR_IMPLEMENTED,
        LabelType.IMPLEMENTED_MODEL: LABEL_COLOR_IMPLEMENTED,
        LabelType.REVIEW_ADDRESSED_BY: LABEL_COLOR_REVIEWED,
        LabelType.REVIEW_ADDRESSED_MODEL: LABEL_COLOR_REVIEWED,
    }
    return Label(
        name=name,
        color=color_map.get(label_type, LABEL_COLOR_DEFAULT),
        description=description,
        label_type=label_type,
    )


def ensure_task_label(provider: AbstractTaskProvider, label_name: str) -> None:
    """Ensure the main task label exists. Create if missing."""
    label = _label_for_type(
        LabelType.ISSUE_LABEL,
        label_name,
        "Task created via WADE",
    )
    provider.ensure_label(label)


def ensure_in_progress_label(provider: AbstractTaskProvider) -> None:
    """Ensure the in-progress label exists."""
    label = _label_for_type(
        LabelType.IN_PROGRESS,
        "in-progress",
        "Issue is being actively worked on",
    )
    provider.ensure_label(label)


def add_in_progress_label(
    provider: AbstractTaskProvider,
    task_id: str,
) -> None:
    """Apply in-progress label to a task."""
    ensure_in_progress_label(provider)
    provider.add_label(task_id, "in-progress")


def remove_in_progress_label(
    provider: AbstractTaskProvider,
    task_id: str,
) -> None:
    """Remove in-progress label from a task."""
    provider.remove_label(task_id, "in-progress")


def ensure_complexity_label(
    provider: AbstractTaskProvider,
    complexity: Complexity,
) -> None:
    """Ensure a ``complexity:X`` label exists. Create if missing."""
    label = _label_for_type(
        LabelType.COMPLEXITY,
        f"complexity:{complexity.value}",
        f"Task complexity: {complexity.value}",
    )
    provider.ensure_label(label)


def add_complexity_label(
    provider: AbstractTaskProvider,
    task_id: str,
    complexity: Complexity,
) -> None:
    """Apply a ``complexity:X`` label to a task."""
    ensure_complexity_label(provider, complexity)
    provider.add_label(task_id, f"complexity:{complexity.value}")
    logger.info("labels.complexity", task_id=task_id, complexity=complexity.value)


def add_planned_by_labels(
    provider: AbstractTaskProvider,
    task_id: str,
    ai_tool: str | None = None,
    model: str | None = None,
) -> None:
    """Add planned-by labels (tool + optional model) to a task."""
    if not ai_tool:
        return

    tool_label = _label_for_type(
        LabelType.PLANNED_BY,
        f"planned-by:{ai_tool}",
        f"Issue planned using {ai_tool}",
    )
    provider.ensure_label(tool_label)
    provider.add_label(task_id, tool_label.name)

    if model:
        model_label = _label_for_type(
            LabelType.PLANNED_MODEL,
            f"planned-model:{model}",
            f"Issue planned with model {model}",
        )
        provider.ensure_label(model_label)
        provider.add_label(task_id, model_label.name)

    logger.info(
        "labels.planned_by",
        task_id=task_id,
        tool=ai_tool,
        model=model,
    )


def add_implemented_by_labels(
    provider: AbstractTaskProvider,
    task_id: str,
    ai_tool: str | None = None,
    model: str | None = None,
) -> None:
    """Add implemented-by labels (tool + optional model) to a task."""
    if not ai_tool:
        return

    tool_label = _label_for_type(
        LabelType.IMPLEMENTED_BY,
        f"implemented-by:{ai_tool}",
        f"Issue implemented using {ai_tool}",
    )
    provider.ensure_label(tool_label)
    provider.add_label(task_id, tool_label.name)

    if model:
        model_label = _label_for_type(
            LabelType.IMPLEMENTED_MODEL,
            f"implemented-model:{model}",
            f"Issue implemented with model {model}",
        )
        provider.ensure_label(model_label)
        provider.add_label(task_id, model_label.name)

    logger.info(
        "labels.implemented_by",
        task_id=task_id,
        tool=ai_tool,
        model=model,
    )


def add_review_addressed_by_labels(
    provider: AbstractTaskProvider,
    task_id: str,
    ai_tool: str | None = None,
    model: str | None = None,
) -> None:
    """Add review-addressed-by labels (tool + optional model) to a task."""
    if not ai_tool:
        return

    tool_label = _label_for_type(
        LabelType.REVIEW_ADDRESSED_BY,
        f"review-addressed-by:{ai_tool}",
        f"Review addressed using {ai_tool}",
    )
    provider.ensure_label(tool_label)
    provider.add_label(task_id, tool_label.name)

    if model:
        model_label = _label_for_type(
            LabelType.REVIEW_ADDRESSED_MODEL,
            f"review-addressed-model:{model}",
            f"Review addressed with model {model}",
        )
        provider.ensure_label(model_label)
        provider.add_label(task_id, model_label.name)

    logger.info(
        "labels.review_addressed_by",
        task_id=task_id,
        tool=ai_tool,
        model=model,
    )


# ---------------------------------------------------------------------------
# Plan summary (token usage annotation on issue bodies)
# ---------------------------------------------------------------------------

PLAN_SUMMARY_MARKER_START = "<!-- wade:plan-summary:start -->"
PLAN_SUMMARY_MARKER_END = "<!-- wade:plan-summary:end -->"


def _strip_plan_summary(body: str) -> str:
    """Remove existing plan summary block from issue body (idempotent)."""
    return remove_marker_block(body, PLAN_SUMMARY_MARKER_START, PLAN_SUMMARY_MARKER_END)


def build_plan_summary_block(
    ai_tool: str | None = None,
    model: str | None = None,
    total_tokens: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_tokens: int | None = None,
    premium_requests: int | None = None,
    per_issue_estimate: int | None = None,
    model_breakdown: list[dict[str, Any]] | None = None,
) -> str:
    """Build the plan summary markdown block."""
    extra_rows: list[str] | None = None
    if per_issue_estimate is not None and per_issue_estimate > 0:
        from wade.ai_tools.transcript import format_count

        extra_rows = [f"| This issue (est.) | **{format_count(per_issue_estimate)}** |"]

    breakdown_rows: list[TokenUsageBreakdownRow] | None = None
    if model_breakdown:
        breakdown_rows = [
            {
                "model": str(row.get("model", "unknown")),
                "input_tokens": int(row.get("input", 0) or 0),
                "output_tokens": int(row.get("output", 0) or 0),
                "cached_tokens": int(row.get("cached", 0) or 0),
            }
            for row in model_breakdown
        ]

    block = build_token_usage_block(
        marker_start=PLAN_SUMMARY_MARKER_START,
        marker_end=PLAN_SUMMARY_MARKER_END,
        heading="## Token Usage (Planning)",
        ai_tool=ai_tool,
        model=model,
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        extra_metric_rows=extra_rows,
        premium_requests=premium_requests,
        model_breakdown=breakdown_rows,
    )
    return block


def apply_plan_token_usage(
    provider: AbstractTaskProvider,
    issue_numbers: list[str],
    ai_tool: str | None = None,
    model: str | None = None,
    total_tokens: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_tokens: int | None = None,
    premium_requests: int | None = None,
    model_breakdown: list[dict[str, Any]] | None = None,
) -> None:
    """Annotate issues with token usage summaries.

    For multi-issue plans, tokens are allocated proportionally by body line count.
    """
    from wade.ai_tools.transcript import allocate_tokens

    if not issue_numbers:
        return

    # Get body line counts for proportional allocation
    line_counts: list[int] = []
    bodies: list[str] = []
    for issue_id in issue_numbers:
        try:
            task = provider.read_task(issue_id)
            body = task.body
        except Exception:
            logger.debug("task.read_body_failed", issue_id=issue_id, exc_info=True)
            body = ""
        bodies.append(body)
        line_counts.append(max(body.count("\n"), 1))

    # Allocate tokens proportionally
    per_issue_tokens: list[int | None]
    if total_tokens and len(issue_numbers) > 1:
        per_issue_tokens = list(allocate_tokens(total_tokens, line_counts))
    elif total_tokens:
        per_issue_tokens = [total_tokens]
    else:
        per_issue_tokens = [None] * len(issue_numbers)

    for i, issue_id in enumerate(issue_numbers):
        body = _strip_plan_summary(bodies[i])
        summary = build_plan_summary_block(
            ai_tool=ai_tool,
            model=model,
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            premium_requests=premium_requests,
            per_issue_estimate=per_issue_tokens[i],
            model_breakdown=model_breakdown,
        )

        new_body = body.rstrip("\n") + "\n\n" + summary + "\n"

        try:
            provider.update_task(issue_id, body=new_body)
            console.detail(f"Updated #{issue_id} with plan summary")
        except Exception as e:
            logger.warning("token_usage.update_failed", issue=issue_id, error=str(e))


# ---------------------------------------------------------------------------
# Plan status helper
# ---------------------------------------------------------------------------


def _task_has_plan(task: Task) -> bool:
    """Return True if the task has any PLANNED_BY label."""
    return any(label.label_type == LabelType.PLANNED_BY for label in task.labels)


def _plan_badge_label(task: Task, has_plan: bool | None = None) -> str:
    """Return the plan status label string ('PLANNED' or 'NO PLAN').

    Args:
        task: The task to check.
        has_plan: Optional cached result of _task_has_plan(task) to avoid redundant computation.
    """
    if has_plan is None:
        has_plan = _task_has_plan(task)
    return "PLANNED" if has_plan else "NO PLAN"


def _plain_badge(label: str) -> str:
    """Return a plain-text badge string for non-Rich contexts."""
    return f"[{label}]"


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


def create_task(
    title: str,
    body: str = "",
    extra_labels: list[str] | None = None,
    config: ProjectConfig | None = None,
    provider: AbstractTaskProvider | None = None,
) -> Task | None:
    """Create a GitHub Issue with the given title, body, and optional extra labels.

    The project issue label is always applied.  ``extra_labels`` are applied
    in addition to it.
    """
    config = config or load_config()
    provider = provider or get_provider(config)

    ensure_task_label(provider, config.project.issue_label)

    labels = [config.project.issue_label] + (extra_labels or [])

    console.step(f"Creating issue: {title}")

    try:
        task = provider.create_task(title=title, body=body, labels=labels)
        console.success(f"Created {console.issue_ref(task.id, task.title)}")
        if task.url:
            console.detail(task.url)
        return task
    except Exception as e:
        console.error(f"Failed to create issue: {e}")
        return None


def create_interactive(
    config: ProjectConfig | None = None,
    provider: AbstractTaskProvider | None = None,
) -> Task | None:
    """Create a GitHub Issue interactively — prompt for title and body."""
    import sys

    from wade.ui import prompts

    config = config or load_config()
    provider = provider or get_provider(config)

    title = prompts.input_prompt("Task title")
    if not title:
        console.error("Title is required")
        return None

    console.hint("Enter task body (press Enter then Ctrl+D when done, or leave empty):")
    body_lines: list[str] = []
    if not sys.stdin.isatty():
        body = ""
    else:
        try:
            while True:
                line = input()
                body_lines.append(line)
        except EOFError:
            pass  # Expected: signals end of stdin input
        body = "\n".join(body_lines)

    return create_task(title=title, body=body, config=config, provider=provider)


def create_from_plan_file(
    plan_file: Path,
    config: ProjectConfig | None = None,
    provider: AbstractTaskProvider | None = None,
) -> Task | None:
    """Create a GitHub Issue from a plan markdown file."""
    config = config or load_config()
    provider = provider or get_provider(config)

    try:
        plan = PlanFile.from_markdown(plan_file)
    except (ValueError, OSError) as e:
        console.error(f"Failed to parse plan file: {e}")
        return None

    return create_task(title=plan.title, body=plan.body, config=config, provider=provider)


def list_tasks(
    config: ProjectConfig | None = None,
    provider: AbstractTaskProvider | None = None,
    state: str = "open",
    show_deps: bool = False,
    json_mode: bool = False,
    quiet: bool = False,
) -> list[Task]:
    """List tasks matching the configured label and state."""
    config = config or load_config()
    provider = provider or get_provider(config)

    # Map state string to TaskState
    state_map = {
        "open": TaskState.OPEN,
        "closed": TaskState.CLOSED,
        "all": None,
    }
    task_state = state_map.get(state, TaskState.OPEN)

    # Fetch tasks
    exclude = None
    if task_state == TaskState.OPEN:
        exclude = ["in-progress"]

    tasks = provider.list_tasks(
        label=config.project.issue_label,
        state=task_state,  # None passes "all" to the provider
        exclude_labels=exclude,
    )

    # Build dependency info if requested
    deps_map: dict[str, dict[str, list[str]]] = {}
    if show_deps and tasks:
        from wade.models.task import parse_dependency_refs

        for task in tasks:
            if task.body:
                deps_info = parse_dependency_refs(task.body)
                if deps_info["depends_on"] or deps_info["blocks"]:
                    deps_map[task.id] = deps_info

    if json_mode:
        import json

        output = [
            {
                "number": t.id,
                "title": t.title,
                "state": t.state.value,
                "labels": [label.name for label in t.labels],
                "url": t.url,
                **({"deps": deps_map[t.id]} if t.id in deps_map else {}),
            }
            for t in tasks
        ]
        console.raw(json.dumps(output, indent=2))
        return tasks

    if quiet:
        return tasks

    if not tasks:
        console.info("No tasks found.")
        return tasks

    # Human-readable output
    console.rule(f"Tasks ({len(tasks)})")
    for task in tasks:
        state_badge = "OPEN" if task.state == TaskState.OPEN else "CLOSED"
        badge = console.badge_str(state_badge, "open" if task.state == TaskState.OPEN else "closed")
        has_plan = _task_has_plan(task)
        plan_label = _plan_badge_label(task, has_plan=has_plan)
        plan_variant = "planned" if has_plan else "unplanned"
        plan_badge = console.badge_str(plan_label, plan_variant)
        console.out.print(f"  {console.issue_ref(task.id, task.title)}  {badge}  {plan_badge}")
        if task.labels:
            label_str = " ".join(f"[{label.name}]" for label in task.labels)
            console.detail(label_str)
        if show_deps and task.id in deps_map:
            dep_info = deps_map[task.id]
            if dep_info["depends_on"]:
                console.detail(
                    f"  Depends on: {', '.join(f'#{n}' for n in dep_info['depends_on'])}"
                )
            if dep_info["blocks"]:
                console.detail(f"  Blocks: {', '.join(f'#{n}' for n in dep_info['blocks'])}")

    return tasks


def prompt_task_selection(
    prompt_msg: str = "Select an issue",
    state: str = "open",
    allow_manual: bool = True,
    config: ProjectConfig | None = None,
    provider: AbstractTaskProvider | None = None,
) -> str | None:
    """Interactively prompt the user to select an issue using arrow keys."""
    from wade.ui import prompts

    if not prompts.is_tty():
        return prompts.input_prompt(prompt_msg)

    tasks = list_tasks(state=state, config=config, provider=provider, quiet=True)
    if not tasks:
        if allow_manual:
            return prompts.input_prompt(f"{prompt_msg} (Issue number or plan file)")
        console.info(f"No {state} tasks found.")
        return None

    items = []
    for t in tasks:
        state_badge = "OPEN" if t.state == TaskState.OPEN else "CLOSED"
        items.append(
            f"#{t.id}  {t.title}  {_plain_badge(state_badge)}  {_plain_badge(_plan_badge_label(t))}"
        )

    fallback = "(Enter manually...)"
    if allow_manual:
        items.append(fallback)
    items.append("(Cancel)")

    idx = prompts.select(prompt_msg, items)
    if idx < len(tasks):
        return tasks[idx].id
    if allow_manual and items[idx] == fallback:
        return prompts.input_prompt("Enter issue number or plan file")

    return None


def prompt_multi_task_selection(
    prompt_msg: str = "Select issues",
    state: str = "open",
    config: ProjectConfig | None = None,
    provider: AbstractTaskProvider | None = None,
) -> list[str]:
    """Interactively prompt the user to select multiple issues."""
    from wade.ui import prompts

    if not prompts.is_tty():
        selection = prompts.input_prompt(f"{prompt_msg} (space-separated)")
        return selection.split() if selection else []

    tasks = list_tasks(state=state, config=config, provider=provider, quiet=True)
    if not tasks:
        console.info(f"No {state} tasks found.")
        return []

    items = [f"#{t.id}  {t.title}" for t in tasks]
    indices = prompts.multi_select(prompt_msg, items)
    return [tasks[i].id for i in indices]


def read_task(
    task_id: str,
    config: ProjectConfig | None = None,
    provider: AbstractTaskProvider | None = None,
    json_mode: bool = False,
) -> Task | None:
    """Read a single task."""
    config = config or load_config()
    provider = provider or get_provider(config)

    try:
        task = provider.read_task(task_id)
    except Exception as e:
        console.error(f"Failed to read issue #{task_id}: {e}")
        return None

    if json_mode:
        import json

        output = {
            "number": task.id,
            "title": task.title,
            "body": task.body,
            "state": task.state.value,
            "labels": [label.name for label in task.labels],
            "url": task.url,
        }
        console.raw(json.dumps(output, indent=2))
    else:
        state_variant = "open" if task.state == TaskState.OPEN else "closed"
        state_str = console.badge_str(task.state.value, state_variant)
        lines = []
        lines.append(f"  State      {state_str}")
        if task.labels:
            lines.append(f"  Labels     {', '.join(label.name for label in task.labels)}")
        if task.url:
            lines.append(f"  URL        [url]{task.url}[/]")
        console.panel("\n".join(lines), title=f"#{task.id}: {task.title}")
        if task.body:
            console.empty()
            console.markdown(task.body)

    return task


def update_task(
    task_id: str,
    body_file: Path | None = None,
    comment: str | None = None,
    config: ProjectConfig | None = None,
    provider: AbstractTaskProvider | None = None,
) -> bool:
    """Update a task's body and/or add a comment."""
    config = config or load_config()
    provider = provider or get_provider(config)

    if not body_file and not comment:
        console.error("Must provide --plan-file or --comment")
        return False

    success = True

    if body_file:
        body_path = Path(body_file).expanduser()
        if not body_path.is_file():
            console.error(f"File not found: {body_file}")
            return False
        try:
            plan = PlanFile.from_markdown(body_path)
            provider.update_task(task_id, body=plan.body, title=plan.title)
            console.success(f"Updated body of #{task_id}")
        except Exception as e:
            console.error(f"Failed to update #{task_id}: {e}")
            success = False

    if comment:
        try:
            provider.comment_on_task(task_id, comment)
            console.success(f"Added comment to #{task_id}")
        except Exception as e:
            console.error(f"Failed to comment on #{task_id}: {e}")
            success = False

    return success


def close_task(
    task_id: str,
    comment: str | None = None,
    config: ProjectConfig | None = None,
    provider: AbstractTaskProvider | None = None,
) -> bool:
    """Close a task with optional comment."""
    config = config or load_config()
    provider = provider or get_provider(config)

    try:
        if comment:
            provider.comment_on_task(task_id, comment)

        remove_in_progress_label(provider, task_id)
        provider.close_task(task_id)
        console.success(f"Closed #{task_id}")
        return True
    except Exception as e:
        console.error(f"Failed to close #{task_id}: {e}")
        return False
