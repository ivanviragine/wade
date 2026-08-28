"""Dependency analysis service — parse, graph, apply, track.

Orchestrates: building context from issues, running AI analysis via the
generic delegation infrastructure, parsing edges, applying cross-references,
and creating tracking issues.
"""

from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path

import structlog
from crossby.models.ai import EffortLevel

from wade.config.loader import load_config
from wade.models.config import ProjectConfig
from wade.models.delegation import DelegationMode, DelegationRequest, DelegationResult
from wade.models.deps import DependencyEdge, DependencyGraph
from wade.models.permission import PermissionMode
from wade.models.workflow import DelegationKind, SessionKind
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services.ai_resolution import (
    confirm_ai_selection,
    resolve_ai_tool,
    resolve_effort,
    resolve_model,
    resolve_permission_mode,
)
from wade.services.delegation_service import (
    delegate,
    effective_timeout,
    extended_timeout,
    resolve_mode,
)
from wade.services.knowledge_recovery import (
    RETAINED_VOTE_RECOVERY_HINT,
    report_retained_vote_recovery,
)
from wade.services.skill_invocation_service import (
    SkillInvocationError,
    cleanup_delegation_bundle,
    compose_delegation_prompt,
    prepare_delegation_method,
)
from wade.services.task_service import ensure_task_label
from wade.ui.console import console

logger = structlog.get_logger()

# A detached dependency agent normally returns its edges through stdout.  Keep
# an ignored local copy before the parent attempts a rating-vote handoff: if
# main is unavailable at that moment, preserving only the worktree would not
# otherwise preserve the analysis the user needs to retry/recover.
DEPS_ANALYSIS_OUTPUT_RELATIVE_PATH = ".wade/deps-analysis-output.txt"

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


def build_deps_prompt(context: str, method_section: str | None = None) -> str:
    """Build the full dependency analysis prompt from context."""
    template = get_deps_prompt_template()
    if method_section is None:
        # Compatibility for pure prompt-builder callers. Runtime orchestration
        # always supplies a validated frozen method section.
        from wade.utils.templates import get_skills_templates_dir

        method_section = (get_skills_templates_dir() / "dependency-analysis/SKILL.md").read_text(
            encoding="utf-8"
        )
    return compose_delegation_prompt(
        DelegationKind.DEPENDENCY_ANALYSIS,
        contract=template,
        method_section=method_section,
        input_label="Task context",
        input_content=context,
    )


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


DEPS_MARKER_START = "<!-- wade:deps:start -->"
DEPS_MARKER_END = "<!-- wade:deps:end -->"


def apply_deps_to_issues(
    provider: AbstractTaskProvider,
    issue_numbers: list[str],
    edges: list[DependencyEdge],
) -> int:
    """Update each issue body with dependency cross-references.

    The ``## Dependencies`` section is wrapped in ``wade:deps`` markers so this
    rewrites only wade's own block and preserves any concurrent edit to the rest
    of the issue body (A4). Returns number of successfully updated issues.
    """
    from wade.utils.body_markers import enforce_body_budget, upsert_marked_block
    from wade.utils.markdown import remove_marker_block

    updated = 0

    for issue_id in issue_numbers:
        deps_inner = build_deps_section(issue_id, edges).strip()

        try:
            task = provider.read_task(issue_id)
            body = task.body
            has_legacy = "## Dependencies" in body
            has_marked = DEPS_MARKER_START in body
            if not deps_inner and not has_legacy and not has_marked:
                continue

            # Remove the prior marked block FIRST: strip_deps_section is not
            # marker-aware, so running it on a body that still contains the block
            # would cut at the ## Dependencies heading *inside* the markers and
            # leave them unbalanced. After the marked block is gone, drop any
            # genuinely legacy unmarked ## Dependencies, then upsert the fresh
            # block — everything else is preserved verbatim.
            cleaned = remove_marker_block(body, DEPS_MARKER_START, DEPS_MARKER_END)
            cleaned = strip_deps_section(cleaned)
            new_body = upsert_marked_block(cleaned, DEPS_MARKER_START, DEPS_MARKER_END, deps_inner)
            new_body = enforce_body_budget(
                new_body, warn=console.warn, label=f"issue #{issue_id} body"
            )

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
    ai_tool: str | None,
    prompt: str,
    mode: DelegationMode,
    *,
    model: str | None = None,
    effort: str | None = None,
    allowed_commands: list[str] | None = None,
    cwd: Path | None = None,
    timeout: int | None = None,
    permission_mode: PermissionMode = PermissionMode.DEFAULT,
    explicit_timeout: bool = False,
) -> DelegationResult:
    """Run dependency analysis via the generic delegation infrastructure.

    Returns the full ``DelegationResult`` so the caller can distinguish a timeout
    (``timed_out=True``, possibly carrying partial output) from a crash and avoid
    applying a partial dependency graph. ``explicit_timeout`` marks a
    user-configured ``ai.deps.timeout`` so the headless path skips its retry.
    """
    request = DelegationRequest(
        mode=mode,
        prompt=prompt,
        ai_tool=ai_tool,
        model=model,
        effort=effort,
        cwd=cwd,
        allowed_commands=allowed_commands or [],
        permission_mode=permission_mode,
        explicit_timeout=explicit_timeout,
        **({"timeout": timeout} if timeout is not None else {}),
    )
    result = delegate(request)
    if result.timed_out:
        logger.warning("deps.delegation_timeout", mode=mode.value)
    elif not result.success:
        logger.warning("deps.delegation_failed", mode=mode.value, feedback=result.feedback)
    return result


def _persist_dependency_output(worktree_path: Path, output: str) -> Path:
    """Durably save a detached agent's returned analysis for handoff recovery.

    This is a session-local, ignored transport/debug artefact, never a task or
    tracked repository file.  The explicit flush makes the failure ordering
    meaningful: a later failed vote handoff can safely tell the user that both
    the staged vote and the generated dependency output remain recoverable in
    the retained worktree.
    """
    path = worktree_path / DEPS_ANALYSIS_OUTPUT_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fd:
        fd.write(output)
        if output and not output.endswith("\n"):
            fd.write("\n")
        fd.flush()
        os.fsync(fd.fileno())
    return path


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
    permission_mode: str | None = None,
    yolo: bool | None = None,
    permission_mode_explicit: bool = False,
    planning_worktree: Path | None = None,
    skills: list[str] | None = None,
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
            worktree instead of creating a new one. Dependency methodology is
            still resolved into its own foreign operation bundle.

    Returns the DependencyGraph, or None on failure.
    """
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

    resolved_tool: str | None = None
    resolved_model: str | None = None
    resolved_effort: EffortLevel | None = None
    effective_permission_mode = PermissionMode.DEFAULT

    if delegation_mode != DelegationMode.PROMPT:
        resolved_tool = resolve_ai_tool(ai_tool, config, "deps")
        if not resolved_tool:
            console.error("No AI tool available for dependency analysis.")
            return None

        resolved_model = resolve_model(model, config, "deps", tool=resolved_tool)
        resolved_effort = resolve_effort(effort, config, "deps", tool=resolved_tool)
        resolved_permission_mode = resolve_permission_mode(permission_mode, yolo, config, "deps")

        # Effective mode enforces the read-only headless *safety* rule
        # (delegation_service.py:126 forces DEFAULT for headless launches) — not
        # confirm_ai_selection's DelegationMode.HEADLESS display guard, which is
        # orthogonal. Forcing DEFAULT here keeps the displayed mode equal to the
        # applied mode. deps defaults to headless, so its applied mode stays
        # DEFAULT; `--mode interactive` (or config) honors the resolved tier.
        display_permission_mode = (
            PermissionMode.DEFAULT
            if delegation_mode == DelegationMode.HEADLESS
            else resolved_permission_mode
        )

        console.rule("wade task deps")
        console.kv("Issues", str(len(issue_numbers)))

        # Prompt mode is raw prompt generation, so it should not run AI-selection UX.
        resolved_tool, resolved_model, resolved_effort, confirmed_permission_mode = (
            confirm_ai_selection(
                resolved_tool,
                resolved_model,
                tool_explicit=ai_explicit,
                model_explicit=model_explicit,
                resolved_effort=resolved_effort,
                effort_explicit=effort_explicit,
                resolved_permission_mode=display_permission_mode,
                permission_mode_explicit=permission_mode_explicit,
                mode=delegation_mode,
            )
        )
        if not resolved_tool:
            console.error("No AI tool selected.")
            return None

        # Re-apply the headless safety rule after confirm: interactive changes are
        # honored, but a headless launch always stays DEFAULT regardless.
        effective_permission_mode = (
            PermissionMode.DEFAULT
            if delegation_mode == DelegationMode.HEADLESS
            else confirmed_permission_mode
        )

    # Set up worktree for deps analysis
    standalone_worktree: Path | None = None
    standalone_repo_root: Path | None = None
    deps_cwd: Path | None = None

    if delegation_mode == DelegationMode.PROMPT:
        deps_cwd = None
    elif planning_worktree is not None:
        # Reuse existing planning worktree (deps skill already installed)
        deps_cwd = planning_worktree
    else:
        # Standalone invocation — create a detached-HEAD worktree
        cwd = project_root or Path.cwd()
        try:
            from wade.git import repo as git_repo
            from wade.git import worktree as git_worktree
            from wade.services.implementation_service import (
                _resolve_worktrees_dir,
                bootstrap_worktree,
            )
            from wade.services.knowledge_service import mark_throwaway_knowledge_session
            from wade.skills.installer import compatibility_skills_for_session

            repo_root = git_repo.get_repo_root(cwd)
            standalone_repo_root = repo_root
            # Hand off votes an earlier session had to leave behind before
            # adding another worktree — retained staging is only retryable if
            # some later run actually retries it.
            report_retained_vote_recovery(repo_root, config)
            worktrees_dir = _resolve_worktrees_dir(config, repo_root)
            repo_name = repo_root.name
            short_id = os.urandom(4).hex()
            wt_dir = worktrees_dir / repo_name / f"deps-{short_id}"
            standalone_worktree = git_worktree.create_detached_worktree(
                repo_root=repo_root,
                worktree_dir=wt_dir,
            )
            bootstrap_worktree(
                standalone_worktree,
                config,
                repo_root,
                skills=compatibility_skills_for_session(SessionKind.DEPS),
            )
            # This process flushes the worktree's staged votes before removing
            # it, so it may authorize staging there (a plain detached HEAD may
            # not — nothing would ever flush it).
            mark_throwaway_knowledge_session(standalone_worktree)
            deps_cwd = standalone_worktree
        except Exception as e:
            logger.warning("deps.worktree_create_failed", error=str(e))
            # Never fall through to the caller's checkout.  In particular, a
            # sandboxed child that cannot create its isolated detached
            # worktree must not silently receive the main checkout as its cwd.
            # That used to make the same `wade task deps` command appear to
            # work in one session and run with fundamentally different
            # containment in another.
            if standalone_worktree is not None and standalone_repo_root is not None:
                with contextlib.suppress(Exception):
                    from wade.git import worktree as git_worktree

                    git_worktree.remove_worktree(
                        standalone_repo_root,
                        standalone_worktree,
                        force=True,
                    )
            console.error(
                "Could not create an isolated dependency worktree; dependency analysis "
                "was not launched. Retry after restoring worktree access."
            )
            return None

    # A detached deps worktree is still an AI session: verify only the
    # capabilities this phase will actually use before launching the agent.
    # Every non-prompt cwd is checked, including a reused planning worktree —
    # blocked `.wade/` staging must stop delegation there too, not only for a
    # worktree this call created.
    if deps_cwd is not None:
        from wade.models.readiness import ReadinessPhase
        from wade.services.check_service import CheckStatus, check_session_readiness

        readiness = check_session_readiness(
            ReadinessPhase.DEPS,
            deps_cwd,
            config,
            resolved_tool,
        )
        if readiness.status != CheckStatus.IN_WORKTREE:
            console.error(readiness.format_output())
            if standalone_worktree is None:
                # A reused planning worktree belongs to the parent `wade plan`
                # lifecycle, which flushes its staged votes and removes it.
                # Never delete it from here.
                return None
            # This is a pre-launch failure: no AI output or vote exists yet,
            # so remove the empty session rather than leaking a worktree. A
            # post-launch handoff failure below deliberately preserves it.
            assert standalone_repo_root is not None
            try:
                from wade.git import worktree as git_worktree

                git_worktree.remove_worktree(
                    standalone_repo_root,
                    standalone_worktree,
                    force=True,
                )
            except Exception as exc:
                logger.warning(
                    "deps.readiness_cleanup_failed",
                    worktree=str(standalone_worktree),
                    error=str(exc),
                )
                console.error(
                    "Could not clean up the blocked dependency worktree; it was preserved at "
                    f"{standalone_worktree}. Retry after restoring access."
                )
            return None

    # Dependency analysis is always a foreign bounded operation—even when its
    # contained deps session reuses a planning worktree. Resolve and snapshot
    # its own binding without touching the host session bundle.
    operation_root = deps_cwd or (project_root or Path.cwd())
    try:
        prepared_method = prepare_delegation_method(
            config,
            DelegationKind.DEPENDENCY_ANALYSIS,
            cwd=operation_root,
            skills=skills,
        )
    except SkillInvocationError as exc:
        console.error(str(exc))
        return None

    # Build context
    context = build_context(provider, issue_numbers)
    prompt = build_deps_prompt(context, prepared_method.method_section)

    valid_numbers = set(issue_numbers)
    task_titles: dict[str, str] = {}
    if delegation_mode != DelegationMode.PROMPT:
        for num in issue_numbers:
            try:
                task = provider.read_task(num)
                task_titles[num] = task.title
                console.step(f"#{num}: {console.escape_markup(task.title)}")
            except Exception:
                logger.debug("deps.issue_read_failed", issue_num=num, exc_info=True)
                task_titles[num] = f"Issue #{num}"

    # Run AI analysis via delegation infrastructure
    effort_str = resolved_effort.value if isinstance(resolved_effort, EffortLevel) else None
    # Scale the budget from payload size + effort; an explicit
    # ``ai.deps.timeout`` is honored verbatim and bypasses scaling + retry.
    deps_timeout = effective_timeout(prompt, cmd_config.timeout, effort_str)
    deps_explicit_timeout = cmd_config.timeout is not None
    if delegation_mode != DelegationMode.PROMPT and resolved_tool:
        console.step(
            f"Running {resolved_tool} ({delegation_mode.value}) for dependency analysis..."
        )
    if delegation_mode == DelegationMode.HEADLESS:
        # This spawns an external AI subprocess bounded by ``deps_timeout``. Announce
        # a budget the orchestrator driving wade must wait out — otherwise it kills
        # the call at its own shorter timeout before wade can preserve partial output
        # or run its retry. Mirrors the advisory in
        # review_delegation_service._run_review_delegation (#366 review: the deps
        # path silently blocked without it).
        if deps_explicit_timeout:
            console.info(
                "This runs an external AI subprocess bounded by your configured "
                f"ai.deps.timeout of {deps_timeout}s (no retry). Keep it in the "
                f"foreground and allow more than {deps_timeout}s before timing "
                "out. Do not move it to the background."
            )
        else:
            worst_case = deps_timeout + extended_timeout(deps_timeout)
            console.info(
                f"This runs an external AI subprocess. wade budgets {deps_timeout}s "
                "and, on timeout, retries once with a longer budget (worst-case "
                f"total {worst_case}s). Keep it in the foreground and allow more "
                f"than {worst_case}s before timing out (raise your shell/tool "
                "timeout if needed). Do not move it to the background."
            )
    delegation_result = _run_delegation(
        resolved_tool,
        prompt,
        delegation_mode,
        model=resolved_model,
        effort=effort_str,
        allowed_commands=config.permissions.allowed_commands,
        cwd=operation_root,
        timeout=deps_timeout,
        permission_mode=effective_permission_mode,
        explicit_timeout=deps_explicit_timeout,
    )
    output = (
        delegation_result.feedback
        if delegation_result.success and delegation_result.feedback
        else None
    )
    cleanup_delegation_bundle(prepared_method, preserve=not delegation_result.success)

    if delegation_mode == DelegationMode.PROMPT:
        if output:
            # AI-generated analysis is untrusted free-form text that can quote
            # bracketed markup (e.g. `[/]`); print literally with markup
            # disabled so Rich doesn't raise MarkupError on it (#394).
            console.out.print(output, markup=False)
            return DependencyGraph()
        console.error("Could not generate dependency analysis prompt.")
        return None

    # Save the raw returned analysis before any parent-side vote handoff or
    # worktree removal.  A throwaway worker's stdout otherwise dies with this
    # process, leaving no useful dependency output to recover after a blocked
    # main-checkout handoff.
    if output and deps_cwd is not None:
        try:
            snapshot = _persist_dependency_output(deps_cwd, output)
        except OSError as exc:
            logger.warning(
                "deps.output_snapshot_failed",
                worktree=str(deps_cwd),
                error=str(exc),
            )
            console.error(
                "Could not durably save dependency analysis output; preserving the "
                f"worktree at {deps_cwd}. Retry after restoring local write access."
            )
            return None
        logger.debug("deps.output_snapshot_saved", path=str(snapshot))

    if output:
        console.success(f"Analysis complete ({delegation_mode.value} mode).")
    elif delegation_result.timed_out:
        # A timed-out deps run may carry *partial* output, but applying an
        # incomplete dependency graph to issue bodies is worse than applying
        # none — a half-finished graph silently overwrites real cross-refs. The
        # bigger budget + one retry (both in the shared headless path) already
        # make a clean run far more likely, so surface the timeout (distinct from
        # a crash) and drop the partial rather than parsing half a graph.
        console.warn(
            f"Dependency analysis timed out ({delegation_mode.value} mode) before "
            "completing — no dependency graph was applied. Re-run, or raise "
            "ai.deps.timeout."
        )
    else:
        console.error(f"Delegation failed ({delegation_mode.value} mode).")

    # Clean up standalone worktree (planning worktree is cleaned by plan_service)
    if standalone_worktree is not None:
        try:
            from wade.git import repo as git_repo
            from wade.git import worktree as git_worktree
            from wade.services.knowledge_service import flush_staged_ratings

            repo_root = standalone_repo_root or git_repo.get_repo_root(project_root or Path.cwd())
            if config.knowledge.enabled:
                handoff = flush_staged_ratings(standalone_worktree, repo_root, config.knowledge)
                if not handoff.success:
                    console.error(
                        "Could not hand off staged knowledge votes; preserving dependency "
                        f"worktree at {standalone_worktree}. "
                        f"{handoff.message or 'Retry after restoring access.'}"
                    )
                    console.hint(RETAINED_VOTE_RECOVERY_HINT)
                    return None
            git_worktree.remove_worktree(repo_root, standalone_worktree, force=True)
        except Exception as exc:
            logger.warning(
                "deps.worktree_cleanup_failed",
                worktree=str(standalone_worktree),
                error=str(exc),
            )
            console.error(
                "Could not clean up the dependency worktree; it was preserved at "
                f"{standalone_worktree}. Retry after restoring access."
            )
            return None

    # A timeout is a hard failure (not "no deps found"), so return None rather
    # than an empty graph — callers must not treat it as an authoritative result.
    # Cleanup above already ran.
    if delegation_result.timed_out:
        return None

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
