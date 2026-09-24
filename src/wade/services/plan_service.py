"""Plan service — AI-assisted planning session orchestration.

Launch native planning terminals with an explicit reviewed handoff; preserve
Crossby's collected-session path for tools without native terminal Plan support.
The parent validates and persists accepted plans.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import re
import shlex
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import structlog
from crossby.ai_tools import (
    AbstractAITool,
    InteractiveLaunchEvent,
    InteractiveLaunchEventKind,
    InteractiveSession,
    PlanSessionError,
    preflight_plan_session,
    terminal_interaction_handler,
)
from crossby.ai_tools.plan_mode import PlanModeLaunchError
from crossby.models.ai import (
    EffortLevel,
    PlanApprovalPolicy,
    PlanArtifactLocation,
    PlanInteraction,
    PlanInteractionOutcome,
    PlanInteractionResponse,
    PlanInteractionSupport,
    PlanSessionRequest,
    PlanSessionResult,
    TokenUsage,
)

from wade.config.loader import load_config
from wade.models.config import ProjectConfig, with_wade_base_pattern
from wade.models.hooks import PLAN_ISSUE_REF_FILE, SessionPhase
from wade.models.interactive_plan import PlanHandoffBinding, PlanHandoffProgress
from wade.models.permission import PermissionMode, permission_mode_launch_kwargs
from wade.models.plan_bundle import PlanBundle, PlanKnowledgeVote
from wade.models.task import CloseReason, PlanFile, Task, TaskState
from wade.models.workflow import SessionKind
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services import interactive_plan_service as interactive_plan
from wade.services import native_plan_service as native_plan
from wade.services.ai_resolution import (
    announce_inherited_sandbox,
    build_relaunch_command,
    confirm_ai_selection,
    resolve_ai_tool,
    resolve_model,
    resolve_permission_mode,
)
from wade.services.implementation_service import bootstrap_draft_pr
from wade.services.implementation_service import start as start_implementation_session
from wade.services.knowledge_recovery import (
    RETAINED_VOTE_RECOVERY_HINT,
    report_retained_vote_recovery,
)
from wade.services.session_composition_service import (
    load_session_manifest_strict,
    validate_frozen_session_bundle,
)
from wade.services.task_service import (
    add_complexity_label,
    add_planned_by_labels,
    apply_plan_token_usage,
    ensure_task_label,
)
from wade.ui import prompts
from wade.ui.console import console
from wade.utils.markdown import append_session_to_body
from wade.utils.plan_validation import PlanDiagnostic as PlanDiagnostic
from wade.utils.plan_validation import PlanDiagnosticLevel as PlanDiagnosticLevel
from wade.utils.plan_validation import PlanValidationResult as PlanValidationResult
from wade.utils.plan_validation import discover_plan_files as discover_plan_files
from wade.utils.plan_validation import has_valid_plan as has_valid_plan
from wade.utils.plan_validation import load_plan_file
from wade.utils.plan_validation import plan_done as plan_done
from wade.utils.plan_validation import validate_plan_dir as validate_plan_dir
from wade.utils.runtime_env import detect_parent_runtime, requires_unsandboxed_relaunch
from wade.utils.safe_state import (
    StateFileAccessError,
    StateFileError,
    StateFileIOError,
    StateFileUnsafeError,
    atomic_write_state_file,
    exclusive_write_state_file,
    list_state_files_strict,
    read_state_file_strict,
    state_file_present,
)
from wade.utils.terminal import (
    compose_plan_title,
    set_terminal_title,
    start_title_keeper,
    stop_title_keeper,
)

logger = structlog.get_logger()


class _PlanFinalizationFailure:
    """Sentinel for failures that require preserving generated plan artifacts."""


PLAN_FINALIZATION_FAILED = _PlanFinalizationFailure()
HANDOFF_PROGRESS_FILE = "handoff-progress.json"
HANDOFF_BINDING_FILE = "handoff-binding.json"


class HandoffProgressSaveError(ValueError):
    """A completed handoff could not durably record its recoverable state."""


def _save_handoff_binding(
    root: Path, model: str | None, config: ProjectConfig
) -> PlanHandoffBinding:
    """Persist launch settings before an AI session can produce a recoverable handoff."""

    binding = PlanHandoffBinding(
        model=model,
        provider=config.provider.model_copy(deep=True),
        project=config.project.model_copy(deep=True),
        knowledge=config.knowledge.model_copy(deep=True),
        knowledge_required=config.knowledge.enabled,
    )
    if not exclusive_write_state_file(
        root, ("plans",), HANDOFF_BINDING_FILE, binding.model_dump_json()
    ):
        raise HandoffProgressSaveError("Cannot safely save planning handoff binding before launch")
    return binding


def _load_handoff_binding(root: Path) -> PlanHandoffBinding:
    """Load the launch settings that identify a recoverable handoff's backend."""

    if not state_file_present(root, ("plans",), HANDOFF_BINDING_FILE):
        raise ValueError("Planning handoff binding was never recorded; cannot safely recover")
    try:
        raw = read_state_file_strict(root, ("plans",), HANDOFF_BINDING_FILE)
    except StateFileUnsafeError as exc:
        raise ValueError("Planning handoff binding is absent or unsafe") from exc
    try:
        return PlanHandoffBinding.model_validate_json(raw)
    except ValueError as exc:
        raise ValueError("Planning handoff binding is invalid") from exc


def _validate_handoff_binding(binding: PlanHandoffBinding, config: ProjectConfig) -> None:
    """Reject recovery through a provider other than the one selected at launch."""

    if binding.provider != config.provider:
        raise ValueError(
            "Planning handoff belongs to a different provider configuration; "
            "restore the original provider before recovering"
        )
    if binding.knowledge.enabled != binding.knowledge_required:
        raise ValueError("Planning handoff binding has inconsistent knowledge settings")


def _load_handoff_progress(root: Path, session_id: str) -> PlanHandoffProgress | None:
    """Load progress only when it belongs to this completed handoff.

    Older retained handoffs have no progress file. They remain recoverable, but
    cannot recover launch-specific metadata that was not recorded at collection
    time. Any present progress file must be safe, valid, and tied to the exact
    completed handoff rather than being silently replaced.
    """

    if not state_file_present(root, ("plans",), HANDOFF_PROGRESS_FILE):
        return None
    try:
        raw = read_state_file_strict(root, ("plans",), HANDOFF_PROGRESS_FILE)
    except StateFileUnsafeError as exc:
        raise ValueError("Planning handoff progress is absent or unsafe") from exc
    try:
        progress = PlanHandoffProgress.model_validate_json(raw)
    except ValueError as exc:
        raise ValueError("Planning handoff progress is invalid") from exc
    if progress.session_id != session_id:
        raise ValueError("Planning handoff progress belongs to a different completed session")
    return progress


def _save_handoff_progress(root: Path, progress: PlanHandoffProgress) -> bool:
    """Atomically persist retry state without following handoff-path links."""

    return atomic_write_state_file(
        root,
        ("plans",),
        HANDOFF_PROGRESS_FILE,
        progress.model_dump_json().encode(),
    )


def _validate_handoff_progress_binding(
    progress: PlanHandoffProgress, config: ProjectConfig
) -> None:
    """Reject recovery when its durable external bindings no longer match."""

    if progress.provider is None:
        raise ValueError(
            "Planning handoff progress lacks its original provider binding; cannot safely recover"
        )
    if progress.provider != config.provider:
        raise ValueError(
            "Planning handoff belongs to a different provider configuration; "
            "restore the original provider before recovering"
        )
    if progress.project is None:
        raise ValueError(
            "Planning handoff progress lacks its original task settings; cannot safely recover"
        )
    if progress.knowledge is None:
        raise ValueError(
            "Planning handoff progress lacks its original knowledge configuration; "
            "cannot safely recover"
        )
    if progress.knowledge_required is None:
        raise ValueError(
            "Planning handoff progress lacks its original knowledge requirement; "
            "cannot safely recover"
        )
    if progress.knowledge.enabled != progress.knowledge_required:
        raise ValueError("Planning handoff progress has inconsistent knowledge settings")
    if progress.knowledge_required and progress.knowledge_votes is None:
        raise ValueError(
            "Planning handoff progress lacks its original knowledge vote binding; "
            "cannot safely recover"
        )


def _config_for_handoff_recovery(
    config: ProjectConfig, progress: PlanHandoffProgress | PlanHandoffBinding
) -> ProjectConfig:
    """Reuse the task and knowledge settings that created a retained handoff."""

    if isinstance(progress, PlanHandoffProgress):
        _validate_handoff_progress_binding(progress, config)
        assert progress.project is not None
        assert progress.knowledge is not None
        project = progress.project
        knowledge = progress.knowledge
    else:
        _validate_handoff_binding(progress, config)
        project = progress.project
        knowledge = progress.knowledge
    return config.model_copy(
        deep=True,
        update={
            "project": project.model_copy(deep=True),
            "knowledge": knowledge.model_copy(deep=True),
        },
    )


def _ensure_handoff_progress(
    root: Path,
    session_id: str,
    model: str | None,
    config: ProjectConfig,
    knowledge_votes: tuple[PlanKnowledgeVote, ...] | None = None,
) -> PlanHandoffProgress:
    """Return one handoff's durable metadata, creating it before mutations."""

    binding = _load_handoff_binding(root)
    _validate_handoff_binding(binding, config)
    if binding.model != model:
        raise ValueError(
            "Planning handoff model does not match its original launch binding; "
            "cannot safely recover"
        )
    progress = _load_handoff_progress(root, session_id)
    if progress is not None:
        _validate_handoff_progress_binding(progress, config)
        if (
            progress.model != binding.model
            or progress.provider != binding.provider
            or progress.project != binding.project
            or progress.knowledge != binding.knowledge
            or progress.knowledge_required != binding.knowledge_required
        ):
            raise ValueError(
                "Planning handoff progress does not match its original launch binding; "
                "cannot safely recover"
            )
        return progress

    progress = PlanHandoffProgress(
        session_id=session_id,
        model=binding.model,
        provider=binding.provider.model_copy(deep=True),
        project=binding.project.model_copy(deep=True),
        knowledge=binding.knowledge.model_copy(deep=True),
        knowledge_required=binding.knowledge_required,
        knowledge_votes=knowledge_votes if binding.knowledge_required else None,
    )
    if not _save_handoff_progress(root, progress):
        raise HandoffProgressSaveError("Cannot safely save planning handoff progress")
    return progress


def _handoff_progress_saver(
    root: Path | None, progress: PlanHandoffProgress | None
) -> Callable[[], bool] | None:
    """Bind an optional worktree progress record to the issue-creation loop."""

    if root is None or progress is None:
        return None
    return lambda: _save_handoff_progress(root, progress)


def _bind_handoff_knowledge_votes(
    progress: PlanHandoffProgress | None,
    votes: tuple[PlanKnowledgeVote, ...] | None,
) -> None:
    """Reject a recovered bundle whose votes differ from its durable binding."""

    if progress is None:
        return
    if progress.knowledge_votes is None:
        raise ValueError(
            "Planning handoff progress lacks its original knowledge vote binding; "
            "cannot safely recover"
        )
    if progress.knowledge_votes != votes:
        raise ValueError("Planning handoff knowledge votes changed; cannot safely recover")


def _plan_content_digest(plan: PlanFile) -> str:
    """Return the exact plan content binding for one persisted task."""

    return hashlib.sha256(f"{plan.title}\0{plan.body}".encode()).hexdigest()


def _validate_persisted_plan_bindings(
    progress: PlanHandoffProgress | None, plans: list[PlanFile]
) -> None:
    """Reject recovery when a persisted task no longer matches its draft PR plan."""

    if progress is None or not progress.persisted_issues:
        return
    if set(progress.persisted_plan_digests) != set(progress.persisted_issues):
        raise ValueError(
            "Planning handoff progress lacks its original plan content binding; "
            "cannot safely recover"
        )

    plans_by_name = {plan.path.name: plan for plan in plans}
    for name, digest in progress.persisted_plan_digests.items():
        plan = plans_by_name.get(name)
        if plan is None:
            raise ValueError(
                "Planning handoff progress references a plan that is no longer accepted; "
                "cannot safely recover"
            )
        if _plan_content_digest(plan) != digest:
            raise ValueError(
                "Planning handoff plan content changed after its task and draft PR were "
                "persisted; cannot safely recover"
            )


def _handoff_issue_marker(session_id: str, plan_name: str) -> str:
    """Return a stable, hidden external identity for one handoff plan member."""

    digest = hashlib.sha256(f"{session_id}\0{plan_name}".encode()).hexdigest()
    return f"<!-- wade:plan-handoff:{digest} -->"


def _find_pending_handoff_issue(
    provider: AbstractTaskProvider,
    config: ProjectConfig,
    marker: str,
) -> Task | None:
    """Find the one open task created for a pending handoff marker.

    The marker is written in the task's initial body before the provider call
    returns.  A failed local progress update can therefore be reconciled without
    relying on titles or on a best-effort process-local mapping.
    """

    try:
        matches = provider.find_tasks_by_body_marker(
            marker,
            label=config.project.issue_label,
            state=TaskState.OPEN,
        )
    except Exception as exc:
        raise HandoffProgressSaveError(
            "Cannot safely reconcile a pending planning task; output was retained"
        ) from exc
    if len(matches) > 1:
        raise HandoffProgressSaveError(
            "Multiple open tasks match one pending planning handoff; output was retained"
        )
    return matches[0] if matches else None


def get_plan_prompt_template() -> str:
    """Load the plan session prompt template."""
    from wade.skills.installer import get_templates_dir

    template = get_templates_dir() / "prompts" / "plan-session.md"
    if not template.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template}")
    return template.read_text(encoding="utf-8")


def render_plan_prompt(
    plan_dir: str,
    issue_context: str | None = None,
    session_bundle: str = ".wade/session",
    source_root: str = ".",
) -> str:
    """Render the plan prompt template with the plan directory."""
    template = get_plan_prompt_template()
    prompt = template.replace("{plan_dir}", plan_dir).replace("{session_bundle}", session_bundle)
    prompt = prompt.replace("{source_root}", source_root)
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
        "**Do NOT ask the user what to plan** — still perform readiness and every fixed step.",
        "Return the native plan artifact for this specific issue.",
        "",
        "## Issue Details",
        "",
        body,
        "",
        "---",
    ]
    return "\n".join(lines)


def _persist_plan_issue_ref(worktree_root: Path, issue: Task) -> None:
    """Persist a compact issue heading so the PLAN SessionStart hook can re-inject it.

    ``wade plan --issue-id`` pre-loads the issue into the *launch* prompt only;
    after a resume or compaction that context is gone, and a detached plan
    worktree has no root ``PLAN.md`` to recover it from. Writing the ``# Issue
    #<id>: <title>`` heading to :data:`PLAN_ISSUE_REF_FILE` lets
    :func:`wade.hooks.policies.session_start_context` restore *which* issue is
    being planned on every SessionStart source. Best-effort — a write failure is
    logged and swallowed so it can never abort the plan session.
    """
    ref_path = worktree_root / PLAN_ISSUE_REF_FILE
    wade_dir = ref_path.parent
    # Refuse to write through a symlinked ``.wade``: ``mkdir(exist_ok=True)`` would
    # accept it (the dir check follows the link) and the write would land at
    # ``<link-target>/plan-issue.md`` outside the ephemeral planning worktree.
    # ``is_symlink`` does not follow the link, so this is a no-follow guard.
    if wade_dir.is_symlink():
        logger.warning("plan.issue_ref_symlinked_dir_skipped", path=str(wade_dir))
        return
    try:
        wade_dir.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(f"# Issue #{issue.id}: {issue.title}\n", encoding="utf-8")
    except OSError as e:  # pragma: no cover - defensive; must never break planning
        logger.warning("plan.issue_ref_persist_failed", error=str(e))


# ---------------------------------------------------------------------------
# Plan file discovery and validation
# ---------------------------------------------------------------------------
#
# ``discover_plan_files`` / ``validate_plan_dir`` / ``plan_done`` and the
# diagnostic types now live in :mod:`wade.utils.plan_validation` (a lean,
# UI-free module the ``wade-hook`` Stop path can import cheaply); they are
# re-exported at the top of this module for back-compat. ``validate_plan_files``
# stays here because it calls ``console.warn`` — a UI dependency that must not
# leak into ``utils/``.


def validate_plan_files(plan_dir: Path) -> list[PlanFile]:
    """Discover and validate plan files from a directory.

    Returns only files with valid '# Title' headings.
    """
    valid: list[PlanFile] = []
    md_files = discover_plan_files(plan_dir)

    for md_file in md_files:
        try:
            plan = load_plan_file(md_file)
            valid.append(plan)
        except (ValueError, OSError) as e:
            console.warn(f"Skipping {md_file.name}: {e}")

    return valid


def _select_valid_plans(
    plan_dir: Path,
    plan_files: list[PlanFile],
    *,
    yolo: bool,
    selected_names: set[str] | None = None,
) -> list[PlanFile] | None:
    """Strict-validate discovered plans before wade turns them into issues.

    This is the enforcement that makes ``## Complexity`` + a conventional-commit
    title mandatory on the issue-creation path — independent of whether the agent
    ran ``wade plan-session done``. ``plan_files`` are the title-parseable files
    from :func:`validate_plan_files`; here we drop any that fail the *strict*
    :func:`validate_plan_dir` gate (missing/invalid complexity, bad title prefix).

    Returns:
        - The filtered ``list[PlanFile]`` (only files with no error diagnostics)
          when at least one valid file remains and the user did not abort.
        - ``[]`` when **no** valid files remain.
        - ``None`` when the run is interactive, some files are valid and some are
          not, and the user declined the partial run.

    For both ``[]`` and ``None`` the caller creates nothing and returns ``False``,
    but the generated ``PLAN*.md`` are first salvaged to a stable temp dir (see
    :func:`_preserve_generated_plans`) so a trivial validation miss doesn't force a
    full re-run — this helper's job is only to keep invalid plans from silently
    becoming issues, not to decide their fate.

    Every error is surfaced loudly via ``console.error`` (invalid files are never
    silently dropped); warnings via ``console.warn`` do not exclude a file. In a
    mixed-validity run, even ``yolo``, the user must explicitly accept the valid
    subset. Noninteractive execution preserves the artifacts and creates nothing.
    """
    result = validate_plan_dir(plan_dir)
    errors_by_file: dict[str, list[str]] = {}
    for diag in result.errors:
        if selected_names is not None and diag.file not in selected_names:
            continue
        errors_by_file.setdefault(diag.file, []).append(diag.message)
    for diag in result.warnings:
        console.warn(f"{diag.file}: {diag.message}")

    valid: list[PlanFile] = []
    for filename, messages in errors_by_file.items():
        for message in messages:
            console.error(f"{filename}: {message}", markup=False)
    for plan in plan_files:
        if selected_names is not None and plan.path.name not in selected_names:
            continue
        file_errors = errors_by_file.get(plan.path.name)
        if not file_errors:
            valid.append(plan)

    if not valid:
        console.error(
            f"No valid plan files — {len(errors_by_file)} failed validation "
            "(need a '## Complexity' and a conventional-commit title prefix)."
        )
        return []

    if errors_by_file:
        console.warn(
            f"{len(errors_by_file)} plan file(s) failed validation and will be skipped: "
            f"{', '.join(errors_by_file)}"
        )
        if not prompts.is_tty():
            console.error("An invalid subset requires an explicit decision; retaining all plans.")
            return None
        if prompts.is_tty():
            proceed = prompts.confirm(
                f"{len(errors_by_file)} plan file(s) failed validation and will be skipped — "
                f"continue with the {len(valid)} valid one(s)?",
                default=False,
            )
            if not proceed:
                console.info(
                    "Aborted — no issues created. Re-run `wade plan` to regenerate the plan."
                )
                return None

    return valid


# ---------------------------------------------------------------------------
# AI session runner
# ---------------------------------------------------------------------------


@contextmanager
def _plan_dir_fallback_env(plan_dir: str, planning_worktree: Path | None) -> Iterator[None]:
    """Advertise the plan directory to the child only in worktree-less fallback mode.

    ``wade plan-session check`` runs inside the AI tool and sees only its own
    cwd, which in this mode is the isolated fallback root, not a git worktree.
    Exporting the plan directory
    for the duration of the launch is what lets the check recognise the
    supported fallback (``PLAN_DIR_ONLY``) instead of telling the agent to stop.

    With a planning worktree the check succeeds on its own, so nothing is
    exported — the variable never leaks into the normal path. The previous value
    is restored afterwards so a nested/parent ``wade`` process is unaffected.
    """
    from wade.models.readiness import PLAN_DIR_ENV_VAR

    if planning_worktree is not None:
        yield
        return
    previous = os.environ.get(PLAN_DIR_ENV_VAR)
    os.environ[PLAN_DIR_ENV_VAR] = plan_dir
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(PLAN_DIR_ENV_VAR, None)
        else:
            os.environ[PLAN_DIR_ENV_VAR] = previous


def run_ai_planning_session(
    ai_tool: str,
    plan_dir: str,
    *,
    request: PlanSessionRequest,
    issue_context: str | None = None,
    session_bundle: str = ".wade/session",
    source_root: str = ".",
) -> PlanSessionResult:
    """Submit the raw managed prompt to one complete native planning session."""
    prompt = render_plan_prompt(
        plan_dir,
        issue_context=issue_context,
        session_bundle=session_bundle,
        source_root=source_root,
    )
    if not exclusive_write_state_file(request.working_dir, ("plans",), "prompt.txt", prompt):
        raise ValueError("Cannot safely save planning prompt; existing artifacts were not replaced")
    console.panel("\n".join(prompt.splitlines()[:5]) + "\n…", title="Planning Prompt (preview)")
    console.info(f"Plan directory: {plan_dir}")
    cancelled = False

    def present(interaction: PlanInteraction) -> PlanInteractionResponse:
        nonlocal cancelled
        response = native_plan.interact(interaction)
        cancelled = cancelled or response.outcome is PlanInteractionOutcome.CANCELLED
        return response

    adapter = AbstractAITool.get(ai_tool)
    handler = present if prompts.is_tty() else None
    if (
        prompts.is_tty()
        and adapter.capabilities().plan_mode.interaction is PlanInteractionSupport.TERMINAL
    ):
        # Public identity-bearing consent: Crossby owns the terminal and process.
        handler = terminal_interaction_handler
    result = adapter.run_plan_session(
        request.model_copy(update={"prompt": prompt}),
        interaction_handler=handler,
    )
    if cancelled:
        native_plan.save_artifact(request.working_dir, result)
        raise KeyboardInterrupt
    return result


def run_interactive_planning_session(
    ai_tool: str,
    plan_dir: str,
    *,
    request: PlanSessionRequest,
    permission_mode: PermissionMode,
    config: ProjectConfig,
    issue_context: str | None,
    session_bundle: str,
    source_root: str,
) -> tuple[PlanBundle, TokenUsage | None]:
    """Leave questions and review inside the native terminal; collect an explicit handoff."""
    prompt = render_plan_prompt(plan_dir, issue_context, session_bundle, source_root)
    if not exclusive_write_state_file(request.working_dir, ("plans",), "prompt.txt", prompt):
        raise ValueError("Cannot safely save the interactive planning prompt")
    interactive_plan.begin(
        request.working_dir,
        ai_tool,
        review_required=config.ai.review_plan.enabled is not False,
        knowledge_required=config.knowledge.enabled,
        model=request.model,
        effort=request.effort.value if request.effort is not None else None,
        sandbox=request.sandbox,
    )
    console.panel("\n".join(prompt.splitlines()[:5]) + "\n…", title="Planning Prompt (preview)")
    console.info(f"Plan directory: {plan_dir}")
    adapter = AbstractAITool.get(ai_tool)
    transcript = request.working_dir / ".wade/plans/terminal.log"
    if not exclusive_write_state_file(request.working_dir, ("plans",), "terminal.log", ""):
        raise ValueError("Cannot safely create the native terminal transcript")
    native_dir = (
        request.working_dir / ".wade/plans/native"
        if adapter.capabilities().plan_mode.artifact_location is PlanArtifactLocation.REQUESTED_PATH
        else None
    )
    if native_dir is not None and not exclusive_write_state_file(
        request.working_dir, ("plans", "native"), ".wade-owned", ""
    ):
        raise ValueError("Cannot safely create the native plan directory")
    deferred_input = adapter.capabilities().plan_mode.supports_ready_event
    ready = False
    submitted = False

    def on_event(event: InteractiveLaunchEvent, session: InteractiveSession) -> None:
        nonlocal ready, submitted
        if event.tool_id != adapter.TOOL_ID:
            raise ValueError("Planning startup event belongs to another AI tool")
        if event.kind is InteractiveLaunchEventKind.PLAN_READY:
            if ready:
                raise ValueError("Planning CLI reported Plan readiness more than once")
            ready = True
            session.send_message(prompt)
        elif event.kind is InteractiveLaunchEventKind.MESSAGE_SUBMITTED:
            if not ready or submitted:
                raise ValueError("Planning CLI reported task submission out of order")
            submitted = True

    exit_code = adapter.launch(
        request.working_dir,
        model=request.model,
        effort=request.effort,
        prompt=None if deferred_input else prompt,
        on_event=on_event if deferred_input else None,
        plan_mode=True,
        plan_output_dir=native_dir,
        transcript_path=transcript,
        trusted_dirs=[str(path) for path in request.trusted_dirs] or None,
        allowed_commands=with_wade_base_pattern(config.permissions.allowed_commands),
        sandbox=request.sandbox,
        network_access=request.network_access,
        **permission_mode_launch_kwargs(permission_mode),
    )
    if exit_code != 0:
        raise ValueError(f"Native planning CLI exited with code {exit_code}; output was retained")
    if deferred_input and not submitted:
        raise ValueError("Native Plan task submission was not confirmed; output was retained")
    bundle = interactive_plan.collect(request.working_dir)
    usage = adapter.parse_transcript(transcript) if transcript.is_file() else None
    return bundle, usage


# ---------------------------------------------------------------------------
# Post-session processing
# ---------------------------------------------------------------------------


def _prepare_plan_handoff(
    *,
    bundle: PlanBundle,
    plan_dir: str,
    session_cwd: Path,
    config: ProjectConfig,
    provider: AbstractTaskProvider,
    planning_worktree: Path | None,
    project_root: Path | None,
    interactive: bool,
    handoff_id: str,
    handoff_progress: PlanHandoffProgress | None,
    knowledge_required: bool,
    resolved_yolo: bool,
) -> list[PlanFile] | None:
    """Revalidate one collected handoff before any provider mutation."""

    if bundle.knowledge_votes and (not knowledge_required or planning_worktree is None):
        raise ValueError("Knowledge votes require an enabled, managed planning worktree")
    if knowledge_required and planning_worktree is not None and bundle.knowledge_votes is None:
        raise ValueError(
            "Knowledge-enabled planning requires the bundle's explicit knowledge_votes handoff"
        )
    accepted_plans = _select_valid_plans(
        Path(plan_dir), validate_plan_files(Path(plan_dir)), yolo=resolved_yolo
    )
    if not accepted_plans:
        return None
    names = native_plan.validate_selection(bundle, accepted_plans)
    if not interactive and not native_plan.review_materialized_plans(
        [plan.path for plan in accepted_plans], project_root or Path.cwd(), yolo=resolved_yolo
    ):
        return None
    # Review may have edited a plan. Repeat parent validation before persistence.
    native_plan.validate_imported_set(session_cwd, bundle)
    accepted_plans = _select_valid_plans(
        Path(plan_dir),
        validate_plan_files(Path(plan_dir)),
        yolo=resolved_yolo,
        selected_names=names,
    )
    if not accepted_plans:
        return None
    native_plan.validate_selection(bundle, accepted_plans)
    _validate_persisted_plan_bindings(handoff_progress, accepted_plans)
    if knowledge_required and planning_worktree is not None:
        from wade.services.knowledge_service import record_handoff_rating_for_session
        from wade.utils.knowledge_file import parse_entries, resolve_knowledge_path

        if bundle.knowledge_votes:
            knowledge_path = resolve_knowledge_path(planning_worktree, config.knowledge)
            known_ids = {
                entry.entry_id
                for entry in parse_entries(knowledge_path.read_text(encoding="utf-8"))
            }
            if any(vote.entry_id not in known_ids for vote in bundle.knowledge_votes):
                raise ValueError("Native plan returned a rating for an unknown knowledge entry")

        _bind_handoff_knowledge_votes(handoff_progress, bundle.knowledge_votes)
        for vote in bundle.knowledge_votes or ():
            record_handoff_rating_for_session(
                planning_worktree,
                config.knowledge,
                vote.entry_id,
                vote.direction,
                handoff_id,
            )
    if not interactive:
        console.info(
            "Native session collected; transcript and token usage are unavailable from Crossby."
        )
    # No provider mutation occurs until collection, validation, and required review succeed.
    ensure_task_label(provider, config.project.issue_label)
    return accepted_plans


def _is_filesystem_access_denied(exc: BaseException) -> bool:
    """Whether *exc* represents an EACCES/EPERM failure retaining a handoff."""
    return isinstance(exc, (StateFileAccessError, interactive_plan.InteractivePlanAccessError)) or (
        isinstance(exc, OSError) and exc.errno in {errno.EACCES, errno.EPERM}
    )


def _persist_accepted_plans(
    *,
    accepted_plans: list[PlanFile],
    bundle: PlanBundle,
    provider: AbstractTaskProvider,
    config: ProjectConfig,
    existing_issue: Task | None,
    plan_dir: str,
    repo_root: Path | None,
    planning_worktree: Path | None,
    resolved_tool: str,
    resolved_model: str | None,
    resolved_effort: EffortLevel | None,
    resolved_yolo: bool,
    resolved_sandbox: bool,
    usage: TokenUsage | None,
    collected: PlanSessionResult | None,
    handoff_id: str,
    refresh_existing_plan: bool = False,
) -> bool:
    """Persist a fully revalidated handoff and finish its managed lifecycle."""

    plan_files = accepted_plans
    console.info(f"Found {len(plan_files)} plan file(s)")
    progress: PlanHandoffProgress | None = None
    if planning_worktree is not None:
        try:
            progress = _ensure_handoff_progress(
                planning_worktree,
                handoff_id,
                resolved_model,
                config,
                bundle.knowledge_votes,
            )
        except (StateFileError, ValueError) as exc:
            console.error(f"Could not record planning recovery progress: {exc}", markup=False)
            _retain_inaccessible_handoff(plan_dir, planning_worktree, access_denied=False)
            return False

    if existing_issue is not None:
        if len(plan_files) == 1:
            if not _attach_plan_to_existing_issue(
                provider=provider,
                config=config,
                issue=existing_issue,
                plan_file=plan_files[0],
                repo_root=repo_root,
                yolo=resolved_yolo,
                refresh_existing_plan=refresh_existing_plan,
            ):
                _preserve_generated_plans(plan_dir, repo_root, planning_worktree, config)
                stop_title_keeper()
                return False
            finalize_issue_numbers = [existing_issue.id]
        else:
            try:
                finalize_issue_numbers = _supersede_issue_with_plans(
                    provider=provider,
                    config=config,
                    issue=existing_issue,
                    plan_files=plan_files,
                    repo_root=repo_root,
                    yolo=resolved_yolo,
                    persisted_issues=progress.persisted_issues if progress is not None else None,
                    persisted_plan_digests=(
                        progress.persisted_plan_digests if progress is not None else None
                    ),
                    pending_issue_markers=(
                        progress.pending_issue_markers if progress is not None else None
                    ),
                    pending_issue_branch_titles=(
                        progress.pending_issue_branch_titles if progress is not None else None
                    ),
                    handoff_id=progress.session_id if progress is not None else None,
                    save_progress=_handoff_progress_saver(planning_worktree, progress),
                )
            except HandoffProgressSaveError as exc:
                console.error(f"Could not record planning recovery progress: {exc}", markup=False)
                _retain_inaccessible_handoff(plan_dir, planning_worktree, access_denied=False)
                stop_title_keeper()
                return False
        stop_title_keeper()
        if not finalize_issue_numbers:
            console.warn("No issues were created from plan files.")
            if progress is not None and progress.pending_issue_markers:
                _retain_inaccessible_handoff(plan_dir, planning_worktree, access_denied=False)
            else:
                _preserve_generated_plans(plan_dir, repo_root, planning_worktree, config)
            return False
        offer_result = _finalize_issues(
            provider=provider,
            config=config,
            issue_numbers=finalize_issue_numbers,
            ai_tool=resolved_tool,
            model=resolved_model,
            usage=usage,
            native_result=collected,
            plan_bundle=bundle,
            plan_files=plan_files,
            repo_root=repo_root,
            planning_worktree=planning_worktree,
            effort=resolved_effort,
            yolo=resolved_yolo,
            sandbox=resolved_sandbox,
        )
        if offer_result is PLAN_FINALIZATION_FAILED:
            _retain_finalization_failed_handoff(
                plan_dir,
                repo_root,
                planning_worktree,
                config,
                issue_numbers=finalize_issue_numbers,
                plan_files=plan_files,
                progress=progress,
            )
            return False
        if not _cleanup_plan_dir_or_worktree(plan_dir, repo_root, planning_worktree, config):
            return False
        if isinstance(offer_result, bool):
            return offer_result
        return True

    try:
        created_numbers, failed_files = _create_issues_from_plans(
            provider=provider,
            config=config,
            plan_files=plan_files,
            repo_root=repo_root,
            persisted_issues=progress.persisted_issues if progress is not None else None,
            persisted_plan_digests=(
                progress.persisted_plan_digests if progress is not None else None
            ),
            pending_issue_markers=(
                progress.pending_issue_markers if progress is not None else None
            ),
            pending_issue_branch_titles=(
                progress.pending_issue_branch_titles if progress is not None else None
            ),
            handoff_id=progress.session_id if progress is not None else None,
            save_progress=_handoff_progress_saver(planning_worktree, progress),
        )
    except HandoffProgressSaveError as exc:
        console.error(f"Could not record planning recovery progress: {exc}", markup=False)
        _retain_inaccessible_handoff(plan_dir, planning_worktree, access_denied=False)
        return False
    if created_numbers:
        stop_title_keeper()
        offer_result = _finalize_issues(
            provider=provider,
            config=config,
            issue_numbers=created_numbers,
            ai_tool=resolved_tool,
            model=resolved_model,
            usage=usage,
            native_result=collected,
            plan_bundle=bundle,
            plan_files=plan_files,
            repo_root=repo_root,
            planning_worktree=planning_worktree,
            effort=resolved_effort,
            yolo=resolved_yolo,
            sandbox=resolved_sandbox,
        )
        if offer_result is PLAN_FINALIZATION_FAILED:
            _retain_finalization_failed_handoff(
                plan_dir,
                repo_root,
                planning_worktree,
                config,
                issue_numbers=created_numbers,
                plan_files=plan_files,
                progress=progress,
            )
            return False
        if failed_files or (progress is not None and progress.pending_issue_markers):
            console.warn(
                f"{len(failed_files)} plan(s) could not be persisted to a draft "
                f"PR; preserving planning output. Failed: {', '.join(failed_files)}"
            )
            # A pending marker may identify a task created just before a local
            # state write or provider response failed.  Keep the registered
            # worktree so --recover can reconcile it rather than discard that
            # identity along with the output copy.
            if progress is not None and progress.pending_issue_markers:
                _retain_inaccessible_handoff(plan_dir, planning_worktree, access_denied=False)
                cleanup_succeeded = False
            else:
                cleanup_succeeded = _preserve_generated_plans(
                    plan_dir, repo_root, planning_worktree, config
                )
        else:
            cleanup_succeeded = _cleanup_plan_dir_or_worktree(
                plan_dir, repo_root, planning_worktree, config
            )
        if not cleanup_succeeded:
            return False
        if isinstance(offer_result, bool):
            return offer_result
        return True
    if failed_files:
        console.warn(
            f"No plan was persisted to a draft PR — {len(failed_files)} plan(s) "
            f"failed; preserving planning output. Failed: {', '.join(failed_files)}"
        )
        if progress is not None and progress.pending_issue_markers:
            _retain_inaccessible_handoff(plan_dir, planning_worktree, access_denied=False)
        else:
            _preserve_generated_plans(plan_dir, repo_root, planning_worktree, config)
        stop_title_keeper()
        return False
    console.warn("No issues were created from plan files.")
    _cleanup_plan_dir_or_worktree(plan_dir, repo_root, planning_worktree, config)
    stop_title_keeper()
    return False


def _recover_completed_handoff(
    *,
    recovery_root: Path,
    project_root: Path | None,
    config: ProjectConfig,
    yolo: bool,
) -> bool:
    """Revalidate and consume one retained managed planning handoff."""

    from wade.git import repo as git_repo
    from wade.git import worktree as git_worktree

    repo_root = git_repo.get_repo_root(project_root or Path.cwd())
    root = Path(os.path.abspath(recovery_root))
    linked = next(
        (
            entry
            for entry in git_worktree.list_worktrees(repo_root)
            if Path(os.path.abspath(entry.path)) == root
        ),
        None,
    )
    if linked is None or linked.branch != "(detached)":
        console.error(
            "Planning recovery requires a retained detached worktree registered with this repo."
        )
        return False

    plan_dir = str(root / ".wade/plans")
    console.rule("wade plan recovery")
    console.kv("Planning worktree", str(root))
    try:
        manifest = load_session_manifest_strict(root)
        validate_frozen_session_bundle(root, manifest, expected_kind=SessionKind.PLAN)
        collected: PlanSessionResult | None = None
        if interactive_plan.active(root):
            state, bundle = interactive_plan.collect_with_state(root)
            adapter = AbstractAITool.get(state.tool)
            transcript = root / ".wade/plans/terminal.log"
            usage = adapter.parse_transcript(transcript) if transcript.is_file() else None
            resolved_tool = state.tool
            resolved_model = state.model or config.get_model("plan")
            raw_effort = state.effort or config.ai.plan.effort or config.ai.effort
            resolved_sandbox = (
                state.sandbox
                if state.sandbox is not None
                else next(
                    (
                        value
                        for value in (config.ai.plan.sandbox, config.ai.sandbox)
                        if value is not None
                    ),
                    False,
                )
            )
            interactive = True
            handoff_id = state.session_id
            frozen_knowledge_required = state.knowledge_required
        else:
            collected, bundle = native_plan.load_artifact(root)
            usage = None
            resolved_tool = str(collected.tool)
            raw_effort = config.ai.plan.effort or config.ai.effort
            resolved_sandbox = next(
                (
                    value
                    for value in (config.ai.plan.sandbox, config.ai.sandbox)
                    if value is not None
                ),
                False,
            )
            interactive = False
            handoff_id = collected.session_id
            resolved_model = config.get_model("plan")
            frozen_knowledge_required = config.knowledge.enabled
        binding = _load_handoff_binding(root)
        config = _config_for_handoff_recovery(config, binding)
        resolved_model = binding.model
        frozen_knowledge_required = binding.knowledge_required
        progress = _load_handoff_progress(root, handoff_id)
        if progress is not None:
            config = _config_for_handoff_recovery(config, progress)
            assert progress.knowledge_required is not None
            if (
                progress.model != binding.model
                or progress.provider != binding.provider
                or progress.project != binding.project
                or progress.knowledge != binding.knowledge
                or progress.knowledge_required != binding.knowledge_required
            ):
                raise ValueError(
                    "Planning handoff progress does not match its original launch binding; "
                    "cannot safely recover"
                )
            if not interactive:
                resolved_model = progress.model
            frozen_knowledge_required = progress.knowledge_required
        else:
            # The launch binding was saved before the planner started, so a
            # failed initial progress write cannot substitute current project
            # settings before re-staging votes or touching the provider.
            progress = _ensure_handoff_progress(
                root, handoff_id, resolved_model, config, bundle.knowledge_votes
            )
        resolved_effort = EffortLevel(raw_effort) if raw_effort is not None else None
        provider = get_provider(config)
        existing_issue = provider.read_task(manifest.task_id) if manifest.task_id else None
        accepted_plans = _prepare_plan_handoff(
            bundle=bundle,
            plan_dir=plan_dir,
            session_cwd=root,
            config=config,
            provider=provider,
            planning_worktree=root,
            project_root=project_root or repo_root,
            interactive=interactive,
            handoff_id=handoff_id,
            handoff_progress=progress,
            knowledge_required=frozen_knowledge_required,
            resolved_yolo=yolo,
        )
        if not accepted_plans:
            _retain_inaccessible_handoff(plan_dir, root, access_denied=False)
            return False
    except (Exception, KeyboardInterrupt) as exc:
        category = type(exc).__name__
        message = (
            "Cancelled" if isinstance(exc, KeyboardInterrupt) else native_plan.failure_message(exc)
        )
        console.error(f"Planning recovery failed ({category}): {message}", markup=False)
        access_denied = _is_filesystem_access_denied(exc)
        _retain_inaccessible_handoff(plan_dir, root, access_denied=access_denied)
        return False

    return _persist_accepted_plans(
        accepted_plans=accepted_plans,
        bundle=bundle,
        provider=provider,
        config=config,
        existing_issue=existing_issue,
        plan_dir=plan_dir,
        repo_root=repo_root,
        planning_worktree=root,
        resolved_tool=resolved_tool,
        resolved_model=resolved_model,
        resolved_effort=resolved_effort,
        resolved_yolo=yolo,
        resolved_sandbox=resolved_sandbox,
        usage=usage,
        collected=collected,
        handoff_id=handoff_id,
        refresh_existing_plan=True,
    )


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
    effort: str | None = None,
    effort_explicit: bool = False,
    yolo: bool | None = None,
    permission_mode: str | None = None,
    permission_mode_explicit: bool = False,
    sandbox: bool | None = None,
    work_skills: list[str] | None = None,
    review_skills: list[str] | None = None,
    refresh_skills: bool = False,
    network_access: bool | None = None,
    collector_network_access: bool | None = None,
    approval_policy: str = "on-request",
    trusted_dirs: list[Path] | None = None,
    timeout: int | None = None,
    recover: Path | None = None,
) -> bool:
    """Run an AI-assisted planning session.

    A native terminal hands off reviewed files, or Crossby collects the artifact. WADE gates its
    members before creating issues and draft PRs.

    When ``issue_id`` is provided the session is pre-loaded with that issue's
    context and the resulting plan is attached to it via draft PR (no new issue).
    """
    config = load_config(project_root)

    if recover is not None:
        if (
            any(
                value is not None
                for value in (
                    ai_tool,
                    model,
                    issue_id,
                    effort,
                    permission_mode,
                    sandbox,
                    work_skills,
                    review_skills,
                    network_access,
                    collector_network_access,
                    trusted_dirs,
                    timeout,
                )
            )
            or refresh_skills
            or approval_policy != "on-request"
        ):
            console.error(
                "--recover consumes the frozen completed handoff; do not combine it with "
                "AI launch, policy, timeout, or skill options."
            )
            return False
        return _recover_completed_handoff(
            recovery_root=recover,
            project_root=project_root,
            config=config,
            yolo=bool(yolo),
        )

    provider = get_provider(config)

    # Resolve AI tool and model
    resolved_tool = resolve_ai_tool(ai_tool, config, "plan")
    if not resolved_tool:
        console.error("No AI tool specified and none detected. Use --ai <tool>.")
        return False

    # Do not use the ordinary launch resolvers' compatibility downgrades.
    resolved_model = resolve_model(model, config, "plan")
    raw_effort = (
        effort or os.environ.get("WADE_EFFORT") or config.ai.plan.effort or config.ai.effort
    )
    try:
        resolved_effort = EffortLevel(raw_effort) if raw_effort is not None else None
        if permission_mode is not None:
            PermissionMode(permission_mode)
    except ValueError:
        console.error("Invalid planning effort or permission mode; no session was started.")
        return False
    # Resolve autonomy / permission mode (yolo is a back-compat alias)
    resolved_permission_mode = resolve_permission_mode(permission_mode, yolo, config, "plan")

    # Resolve explicit sandbox policy first; the selected transport supplies its default. CLI/
    # project requirements still win; ordinary non-plan launch defaults do not change.
    sandbox_requirement = next(
        (
            value
            for value in (sandbox, config.ai.plan.sandbox, config.ai.sandbox)
            if value is not None
        ),
        None,
    )

    console.rule("wade plan")

    # Offer interactive confirmation unless both flags were explicitly provided.
    resolved_tool, resolved_model, resolved_effort, resolved_permission_mode = confirm_ai_selection(
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
        sandbox=sandbox_requirement,
        native_plan=True,
    )
    resolved_yolo = resolved_permission_mode is PermissionMode.YOLO
    if not resolved_tool:
        console.error("No AI tool selected.")
        return False

    try:
        adapter = AbstractAITool.get(resolved_tool)
        interactive = adapter.capabilities().supports_plan_mode
        effective_collector_network_access = (
            collector_network_access if collector_network_access is not None else network_access
        )
        resolved_sandbox = (
            sandbox_requirement if sandbox_requirement is not None else not interactive
        )
        request = PlanSessionRequest(
            prompt="WADE native planning preflight",
            working_dir=(project_root or Path.cwd()).resolve(),
            model=resolved_model,
            effort=resolved_effort,
            sandbox=resolved_sandbox,
            network_access=network_access is True,
            approval_policy=PlanApprovalPolicy(approval_policy),
            trusted_dirs=tuple(path.resolve() for path in (trusted_dirs or ())),
            timeout_seconds=timeout
            if timeout is not None
            else (config.ai.plan.timeout if config.ai.plan.timeout is not None else 600),
        )
        if interactive:
            if config.ai.plan.mode not in {None, "interactive"}:
                raise ValueError(
                    "Native terminal planning requires ai.plan.mode: interactive or unset"
                )
            if approval_policy != "on-request":
                raise ValueError(
                    "Use --permission-mode for native terminals; "
                    "--approval-policy is collector-only"
                )
            if timeout is not None or config.ai.plan.timeout is not None:
                raise ValueError(
                    "--timeout/ai.plan.timeout applies to collection, not an interactive terminal"
                )
            caps = adapter.capabilities()
            if sandbox_requirement is True and not caps.supports_sandbox_toggle:
                raise ValueError(f"{resolved_tool} cannot guarantee explicit sandbox confinement")
            if network_access is not None and not caps.supports_network_access:
                raise ValueError(
                    f"{resolved_tool} cannot apply an explicit network restriction or grant"
                )
            if trusted_dirs and not caps.supports_trusted_dirs:
                raise ValueError(f"{resolved_tool} cannot apply explicit trusted directories")
            adapter.validate_plan_mode_request(
                plan_mode=True,
                initial_message=request.prompt,
                working_dir=request.working_dir,
                plan_output_dir=(
                    request.working_dir / ".wade/plans/native"
                    if caps.plan_mode.artifact_location is PlanArtifactLocation.REQUESTED_PATH
                    else None
                ),
                **permission_mode_launch_kwargs(resolved_permission_mode),
            )
            console.info(f"Native terminal Plan preflight passed: {resolved_tool}")
        else:
            if config.ai.plan.mode is not None:
                raise ValueError(
                    "ai.plan.mode cannot select a native collector transport; remove this override"
                )
            if resolved_permission_mode not in {PermissionMode.DEFAULT, PermissionMode.YOLO}:
                raise ValueError(
                    "This collector cannot apply auto or accept-edits; "
                    "use default or parent-only yolo"
                )
            request = request.model_copy(
                update={"network_access": effective_collector_network_access is True}
            )
            request = native_plan.prepare_request(
                resolved_tool,
                request,
                allowed_commands=config.permissions.allowed_commands,
                confinement_required=sandbox_requirement is True,
                network_restriction_required=effective_collector_network_access is False,
            )
            checked = preflight_plan_session(resolved_tool, request)
            if (
                checked.capability.interaction is PlanInteractionSupport.TERMINAL
                and not prompts.is_tty()
            ):
                raise ValueError(
                    "This native collector needs an attached terminal; run wade plan interactively"
                )
            console.info(
                f"Native plan preflight passed: {resolved_tool} {checked.detected_version}"
            )
    except (PlanSessionError, PlanModeLaunchError, ValueError, OSError) as exc:
        console.error(
            f"Planning preflight failed: {native_plan.failure_message(exc)}", markup=False
        )
        return False
    console.hint("Model availability, authentication, and plan handoff remain runtime checks.")

    # Pre-load existing issue context when issue_id is supplied
    existing_issue: Task | None = None
    if issue_id:
        try:
            existing_issue = provider.read_task(issue_id)
            safe_title = console.escape_markup(existing_issue.title)
            console.kv("Issue", f"#{existing_issue.id}: {safe_title}")
        except Exception as e:
            console.error(f"Could not fetch issue #{issue_id}: {e}")
            return False

    # Set terminal title for the plan session
    plan_title = compose_plan_title(
        existing_issue.id if existing_issue else None,
        existing_issue.title if existing_issue else None,
    )
    set_terminal_title(plan_title)
    start_title_keeper(plan_title)

    # Resolve repo root for draft PR creation
    from wade.git import repo as git_repo
    from wade.git import worktree as git_worktree
    from wade.services.implementation_service import _resolve_worktrees_dir, bootstrap_worktree
    from wade.services.knowledge_service import mark_throwaway_knowledge_session
    from wade.skills.installer import support_skills_for_session

    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except Exception:
        console.warn("Not in a git repo — draft PRs will not be created.")
        repo_root = None

    # Recover votes a previous run had to leave behind before starting a new
    # session — a retained worktree is only retryable if something retries it.
    if repo_root is not None:
        report_retained_vote_recovery(repo_root, config)

    # Create a detached-HEAD planning worktree
    planning_worktree: Path | None = None
    if repo_root is not None:
        worktrees_dir = _resolve_worktrees_dir(config, repo_root)
        repo_name = repo_root.name
        short_id = os.urandom(4).hex()
        planning_worktree_dir = worktrees_dir / repo_name / f"plan-{short_id}"
        try:
            planning_worktree = git_worktree.create_detached_worktree(
                repo_root=repo_root,
                worktree_dir=planning_worktree_dir,
            )
            bootstrap_worktree(
                planning_worktree,
                config,
                repo_root,
                skills=support_skills_for_session(SessionKind.PLAN),
                plan_mode=True,
                selected_ai_tool=resolved_tool,
                session_phase=SessionPhase.PLAN,
                session_kind=SessionKind.PLAN,
                task_id=existing_issue.id if existing_issue else None,
                work_skills=work_skills,
                review_skills=review_skills,
                refresh_skills=refresh_skills,
                sandbox=resolved_sandbox,
            )
            # This process flushes the worktree's staged votes on the way out
            # (``_flush_planning_ratings``), so it is entitled to authorize
            # staging in it. Without the marker a vote would be written to the
            # canonical sidecar of a worktree that is about to be deleted.
            mark_throwaway_knowledge_session(planning_worktree)
            console.kv("Planning worktree", str(planning_worktree))
        except Exception as e:
            console.warn(f"Could not create planning worktree: {e}")
            if planning_worktree is not None:
                _remove_planning_worktree(repo_root, planning_worktree)
            planning_worktree = None

    if planning_worktree is None:
        fallback_root = Path(tempfile.mkdtemp(prefix="wade-plan-")).resolve()
        plan_dir = str(fallback_root / ".wade/plans")
        # The supported no-worktree fallback cannot write the caller's checkout.
        # Materialize its immutable bundle under the writable plan directory and
        # name that exact path in the launch prompt.
        from wade.services.session_composition_service import (
            SessionCompositionError,
            compose_session,
        )

        try:
            compose_session(
                fallback_root,
                repo_root or Path.cwd(),
                config,
                kind=SessionKind.PLAN,
                task_id=existing_issue.id if existing_issue else None,
                work_skills=work_skills,
                review_skills=review_skills,
                refresh=refresh_skills,
                display_root=str(fallback_root / ".wade/session"),
            )
        except SessionCompositionError as exc:
            console.error(f"Cannot start planning session: {exc}")
            _cleanup_plan_dir_or_worktree(plan_dir, repo_root, planning_worktree)
            stop_title_keeper()
            return False
    else:
        # Plan directory: when using a planning worktree, isolate outputs to a
        # dedicated subdirectory so repo markdown files (e.g., README.md) are not
        # misinterpreted as generated plans.
        plan_output_dir = planning_worktree / ".wade" / "plans"
        if (planning_worktree / ".wade").is_symlink() or plan_output_dir.is_symlink():
            console.error("Unsafe planning output directory; no native session was started.")
            _remove_planning_worktree(repo_root, planning_worktree, config)
            stop_title_keeper()
            return False
        plan_dir = str(plan_output_dir)
        # Persist the issue ref so a resumed/compacted issue-scoped plan session
        # can re-inject "which issue am I planning" via the SessionStart hook.
        if existing_issue is not None:
            _persist_plan_issue_ref(planning_worktree, existing_issue)

    # Launch AI session
    console.empty()
    issue_context = _build_issue_context_header(existing_issue) if existing_issue else None
    session_cwd = (planning_worktree or fallback_root).resolve()
    session_bundle = (
        ".wade/session" if planning_worktree is not None else str(session_cwd / ".wade/session")
    )
    if planning_worktree is not None:
        try:
            _save_handoff_binding(planning_worktree, resolved_model, config)
        except HandoffProgressSaveError as exc:
            console.error(f"Cannot start planning: {exc}", markup=False)
            _remove_planning_worktree(repo_root, planning_worktree, config)
            stop_title_keeper()
            return False
    # Unlike implement and pr-comment review, planning has no nested-AI guard: it
    # launches a runtime unconditionally, so an inherited parent sandbox reaches
    # the planner silently. Say so before the launch rather than letting the
    # session discover it as an opaque credential or network failure (#480). The
    # planner still starts — wade cannot prove it will fail — it just stops being
    # a surprise.
    parent = detect_parent_runtime()
    if requires_unsandboxed_relaunch(resolved_sandbox=resolved_sandbox, parent=parent):
        announce_inherited_sandbox(
            parent,
            resolved_sandbox=resolved_sandbox,
            operation="the planning session",
            relaunch_command=build_relaunch_command(
                ["wade", "plan"]
                + (["--issue", existing_issue.id] if existing_issue is not None else []),
                ai_tool=resolved_tool,
                model=resolved_model,
                effort=resolved_effort.value if isinstance(resolved_effort, EffortLevel) else None,
                permission_mode=resolved_permission_mode,
                skills=work_skills,
                review_skills=review_skills,
            ),
        )
    collected: PlanSessionResult | None = None
    usage: TokenUsage | None = None
    handoff_progress: PlanHandoffProgress | None = None
    try:
        request = request.model_copy(update={"working_dir": session_cwd})
        with _plan_dir_fallback_env(plan_dir, planning_worktree):
            if interactive:
                bundle, usage = run_interactive_planning_session(
                    ai_tool=resolved_tool,
                    plan_dir=plan_dir,
                    request=request,
                    permission_mode=resolved_permission_mode,
                    config=config,
                    issue_context=issue_context,
                    session_bundle=session_bundle,
                    source_root=str(planning_worktree or repo_root or cwd),
                )
            else:
                request = request.model_copy(
                    update={"network_access": effective_collector_network_access is True}
                )
                request = native_plan.prepare_request(
                    resolved_tool,
                    request,
                    allowed_commands=config.permissions.allowed_commands,
                    confinement_required=sandbox_requirement is True,
                    network_restriction_required=effective_collector_network_access is False,
                )
                collected = run_ai_planning_session(
                    ai_tool=resolved_tool,
                    plan_dir=plan_dir,
                    request=request,
                    issue_context=issue_context,
                    session_bundle=session_bundle,
                    source_root=str(planning_worktree or repo_root or cwd),
                )
                native_plan.save_artifact(session_cwd, collected)
                bundle = native_plan.parse_artifact(collected.plan)
                native_plan.materialize(session_cwd, bundle)
                if planning_worktree is not None:
                    handoff_progress = _ensure_handoff_progress(
                        planning_worktree,
                        collected.session_id,
                        resolved_model,
                        config,
                        bundle.knowledge_votes,
                    )
        if interactive:
            handoff_id = interactive_plan.collect_with_state(session_cwd)[0].session_id
            if planning_worktree is not None:
                handoff_progress = _ensure_handoff_progress(
                    planning_worktree,
                    handoff_id,
                    resolved_model,
                    config,
                    bundle.knowledge_votes,
                )
        else:
            assert collected is not None
            handoff_id = collected.session_id
        accepted_plans = _prepare_plan_handoff(
            bundle=bundle,
            plan_dir=plan_dir,
            session_cwd=session_cwd,
            config=config,
            provider=provider,
            planning_worktree=planning_worktree,
            project_root=project_root,
            interactive=interactive,
            handoff_id=handoff_id,
            handoff_progress=handoff_progress,
            knowledge_required=config.knowledge.enabled,
            resolved_yolo=resolved_yolo,
        )
        if not accepted_plans:
            _preserve_generated_plans(plan_dir, repo_root, planning_worktree, config)
            stop_title_keeper()
            return False
    except (Exception, KeyboardInterrupt) as exc:
        category = type(exc).__name__
        message = (
            "Cancelled" if isinstance(exc, KeyboardInterrupt) else native_plan.failure_message(exc)
        )
        if isinstance(exc, PlanSessionError):
            for native_path in exc.paths:
                console.hint(f"Native recovery reference (not imported): {native_path}")
        console.error(f"Planning failed ({category}): {message}", markup=False)
        if isinstance(
            exc,
            (
                interactive_plan.InteractivePlanAccessError,
                StateFileIOError,
                HandoffProgressSaveError,
            ),
        ) or (_is_filesystem_access_denied(exc)):
            _retain_inaccessible_handoff(
                plan_dir,
                planning_worktree,
                access_denied=_is_filesystem_access_denied(exc),
            )
        else:
            _preserve_generated_plans(plan_dir, repo_root, planning_worktree, config)
        stop_title_keeper()
        return False

    return _persist_accepted_plans(
        accepted_plans=accepted_plans,
        bundle=bundle,
        provider=provider,
        config=config,
        existing_issue=existing_issue,
        plan_dir=plan_dir,
        repo_root=repo_root,
        planning_worktree=planning_worktree,
        resolved_tool=resolved_tool,
        resolved_model=resolved_model,
        resolved_effort=resolved_effort,
        resolved_yolo=resolved_yolo,
        resolved_sandbox=resolved_sandbox,
        usage=usage,
        collected=collected,
        handoff_id=handoff_id,
    )


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
    persisted_issues: dict[str, str] | None = None,
    persisted_plan_digests: dict[str, str] | None = None,
    pending_issue_markers: dict[str, str] | None = None,
    pending_issue_branch_titles: dict[str, str] | None = None,
    handoff_id: str | None = None,
    save_progress: Callable[[], bool] | None = None,
) -> tuple[list[str], list[str]]:
    """Create lightweight GitHub issues + draft PRs from validated plan files.

    Each plan file produces:
    1. A lightweight issue (title + brief context)
    2. A ``complexity:X`` label
    3. A draft PR with the full plan content

    Returns a tuple of (created issue numbers, names of plan files that failed
    to become issues).
    """
    from wade.git import repo as git_repo

    created: list[str] = []
    failed: list[str] = []

    # Resolve repo root for draft PR creation
    if repo_root is None:
        try:
            repo_root = git_repo.get_repo_root(Path.cwd())
        except Exception:
            console.warn("Not in a git repo — skipping draft PR creation.")
            repo_root = None

    for plan in plan_files:
        persisted_issue = (
            persisted_issues.get(plan.path.name) if persisted_issues is not None else None
        )
        if persisted_issue is not None:
            created.append(persisted_issue)
            continue

        marker = (
            pending_issue_markers.get(plan.path.name) if pending_issue_markers is not None else None
        )
        reconciled: Task | None = None
        if marker is not None:
            reconciled = _find_pending_handoff_issue(provider, config, marker)
        if reconciled is not None:
            # The marker proves the lightweight issue was created before a
            # process or progress-write failure. It still needs the same
            # complexity label and full-plan draft PR as a newly created task.
            task = reconciled
            brief_body = task.body
        else:
            # Build lightweight body
            brief_body = _build_lightweight_issue_body(plan)

            # Write the durable intent before provider mutation.  A task created
            # before a process or state-write failure carries this hidden marker, so
            # recovery can reconcile it without guessing from its title or creating
            # another task.
            if pending_issue_markers is not None:
                if persisted_issues is None or save_progress is None or handoff_id is None:
                    raise HandoffProgressSaveError(
                        "Cannot safely record planning task intent; output was retained"
                    )
                marker = _handoff_issue_marker(handoff_id, plan.path.name)
                pending_issue_markers[plan.path.name] = marker
                if not save_progress():
                    raise HandoffProgressSaveError(
                        "Cannot safely save planning task intent before creation; "
                        "output was retained"
                    )
                brief_body = f"{brief_body.rstrip()}\n\n{marker}".lstrip()

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
                failed.append(plan.path.name)
                continue

        # Add complexity label
        if plan.complexity:
            try:
                add_complexity_label(provider, task.id, plan.complexity)
            except Exception as e:
                logger.warning("plan.complexity_label_failed", error=str(e))

        # The task title selects the deterministic branch name. A reviewed plan
        # can rename a reconciled task after its draft PR exists, so persist the
        # original title before that remote mutation. If the mutation succeeds
        # but its caller gets an error (or the later progress write fails), a
        # retry still finds and refreshes the original branch/PR rather than
        # creating one from the reviewed title.
        branch_title = task.title
        if reconciled is not None and pending_issue_branch_titles is not None:
            branch_title = pending_issue_branch_titles.get(plan.path.name, task.title)
            if plan.path.name not in pending_issue_branch_titles:
                pending_issue_branch_titles[plan.path.name] = branch_title
                if save_progress is None or not save_progress():
                    raise HandoffProgressSaveError(
                        "Cannot safely save recovered task branch identity before title update; "
                        "output was retained"
                    )

        # Bootstrap draft PR with full plan content
        if repo_root is not None:
            pr_info = bootstrap_draft_pr(
                issue_number=task.id,
                issue_title=branch_title,
                plan_body=plan.body,
                config=config,
                repo_root=repo_root,
                base_branch=plan.base_branch,
                refresh_existing_plan=reconciled is not None,
                refresh_title=plan.title if reconciled is not None else None,
            )
            if pr_info:
                pr_number = pr_info.get("number", "?")
                pr_url = pr_info.get("url", "")
                console.success(f"Draft PR #{pr_number}: {pr_url}")

                # Recovery can reuse the same draft PR after the prior body
                # update succeeded but progress persistence failed.
                plan_link = f"**Full plan**: PR #{pr_number}"
                if plan_link not in brief_body:
                    updated_body = brief_body.rstrip("\n") + f"\n\n{plan_link}"
                    try:
                        provider.update_task(task.id, body=updated_body)
                    except Exception as e:
                        logger.warning("plan.pr_link_update_failed", error=str(e))
            else:
                # The draft PR never got created (e.g. a plan-declared base that can't
                # be resolved). The full plan lives only in the planning worktree — if
                # we counted this as created, the caller would finalize the issue and
                # force-remove the worktree, discarding the plan. Record it as failed so
                # the caller preserves the planning output instead (#376 review).
                #
                # A fresh lightweight issue has no durable progress mapping yet. Leaving
                # it open would orphan an issue with no full plan, and re-running the
                # no-issue planning flow would create a second issue for the same plan.
                # A reconciled issue, however, is the durable identity named by the
                # pending marker: retain it so a later recovery retries its PR refresh
                # instead of missing a closed marker and creating a duplicate.
                console.warn(
                    f"Could not create draft PR for #{task.id} — the plan was not "
                    "persisted; preserving planning output."
                )
                if reconciled is None:
                    try:
                        provider.close_task(task.id, reason=CloseReason.NOT_PLANNED)
                        console.detail(
                            f"Closed #{task.id} (no plan persisted) to avoid an orphaned issue"
                        )
                    except Exception as e:
                        logger.warning(
                            "plan.orphan_issue_close_failed", issue=task.id, error=str(e)
                        )
                failed.append(plan.path.name)
                continue

        if reconciled is not None and task.title != plan.title:
            # Locate any existing PR via the original task title first, then
            # update the durable task title. Saving progress only after this
            # succeeds ensures a retry cannot silently discard a reviewed H1.
            try:
                provider.update_task(task.id, title=plan.title)
            except Exception as e:
                logger.warning("plan.reconciled_title_update_failed", issue=task.id, error=str(e))
                failed.append(plan.path.name)
                continue

        # A retained handoff may need to retry finalization or worktree cleanup
        # after this issue and its draft PR are durable. Save the per-plan mapping
        # first so recovery resumes from this task rather than creating another.
        if persisted_issues is not None:
            persisted_issues[plan.path.name] = task.id
            if persisted_plan_digests is not None:
                persisted_plan_digests[plan.path.name] = _plan_content_digest(plan)
            if pending_issue_markers is not None:
                pending_issue_markers.pop(plan.path.name, None)
            if pending_issue_branch_titles is not None:
                pending_issue_branch_titles.pop(plan.path.name, None)
            if save_progress is None or not save_progress():
                raise HandoffProgressSaveError(
                    f"Could not save recovery progress for #{task.id}; output was retained"
                )

        created.append(task.id)

    return created, failed


def _branch_work_in_flight(repo_root: Path, branch_name: str, base: str) -> bool:
    """Return True when a branch's implementation appears to have started.

    "In flight" means either an active worktree is checked out on the branch, or the
    branch carries real work past its bare scaffold commit. Both signal that a worktree's
    ``.wade/base_branch`` may already be pinned to the current base, so silently
    retargeting the PR would diverge the two and merge into the wrong branch.

    The real-work half delegates to :func:`_branch_has_real_work` — the **same** signal the
    reroot uses to decide whether a retarget can be applied loss-free — so the two never
    drift: being exactly one commit ahead is real work only when that commit is not WADE's
    empty scaffold (an amended scaffold, a squash to one commit, or a PR opened outside
    WADE), and an indeterminate count fails closed as in-flight (#376 review). A
    checked-out worktree counts as in-flight here even though it is **not** real work for
    the reroot: the plan path additionally guards the worktree's merge-target pin, which
    the reroot does not touch.
    """
    from wade.git import worktree as git_worktree
    from wade.services.implementation_service.draft_pr import _branch_has_real_work

    try:
        for wt in git_worktree.list_worktrees(repo_root):
            if wt.branch == branch_name:
                return True
    except Exception:
        logger.debug("plan.in_flight_worktree_check_failed", exc_info=True)

    return _branch_has_real_work(repo_root, branch_name, base)


def _base_retarget_is_safe(
    config: ProjectConfig,
    issue: Task,
    plan_file: PlanFile,
    repo_root: Path,
    *,
    yolo: bool,
) -> bool:
    """Guard re-planning from silently changing an in-flight PR's base.

    Returns True when it is safe to proceed with (re)bootstrapping the draft PR —
    i.e. there is no open PR yet, the base is unchanged, or the change was
    explicitly confirmed. Returns False to abort (an in-flight base change was
    refused), leaving the PR and its base intact.

    Base *removal* (the section deleted on re-plan) is a documented no-op:
    ``bootstrap_draft_pr`` never retargets when no base is passed, so an existing
    PR keeps its current base. We surface this rather than silently reverting an
    in-flight PR to main — the exact wrong-merge-target risk this feature avoids.
    """
    from wade.git import branch as git_branch
    from wade.git import pr as git_pr
    from wade.git import repo as git_repo

    branch_name = git_branch.make_branch_name(
        config.project.branch_prefix, int(issue.id), issue.title
    )
    lookup = git_pr.get_pr_for_branch(repo_root, branch_name)
    if lookup.lookup_failed:
        # A transient gh error is NOT "no PR" (git/pr.py contract). We cannot tell
        # whether a retarget would be safe, so abort rather than risk one — the
        # user can re-run the attach once gh recovers.
        console.error(
            f"Could not look up the PR for {branch_name} — transient gh error; "
            "re-run once it recovers."
        )
        return False
    if not (lookup.is_open and lookup.pr is not None):
        return True  # No open PR to retarget — fresh create path is always safe.

    main_branch = config.project.main_branch or git_repo.detect_main_branch(repo_root)
    # Base from the successful lookup — no separate get_pr_base_branch() call whose
    # None would conflate "no base" with "gh failed".
    current_base = lookup.pr.base_ref_name or main_branch
    desired_effective = plan_file.base_branch or main_branch
    if desired_effective == current_base:
        return True  # No base change requested.

    in_flight = _branch_work_in_flight(repo_root, branch_name, current_base)

    if plan_file.base_branch is None:
        # Base section removed on re-plan. bootstrap_draft_pr won't retarget, so
        # the PR keeps its current (non-main) base. Inform, then proceed.
        console.warn(
            f"Plan removed the '## Base Branch' section, but PR #{lookup.pr.number} "
            f"keeps its current base '{current_base}' (wade does not auto-revert an "
            "existing PR's base). Retarget it explicitly with "
            f"`wade implement {issue.id} --base {main_branch}` if intended."
        )
        return True

    if not in_flight:
        return True  # Only a scaffold so far — retargeting is safe.

    console.error(
        f"Re-planning would change PR #{lookup.pr.number}'s base from "
        f"'{current_base}' to '{desired_effective}', but implementation is already "
        "in flight (a worktree exists or commits were made). Retargeting now would "
        "diverge the worktree's merge target from the PR."
    )
    if (
        prompts.is_tty()
        and not yolo
        and prompts.confirm(
            f"Retarget PR #{lookup.pr.number} base to '{desired_effective}' anyway?",
            default=False,
        )
    ):
        return True
    console.info(
        f"Left PR #{lookup.pr.number} targeting '{current_base}'. "
        f"To retarget deliberately, run `wade implement {issue.id} "
        f"--base {desired_effective}`."
    )
    return False


def _reconcile_inflight_worktree_base(
    config: ProjectConfig, issue: Task, repo_root: Path, declared_base: str | None
) -> bool:
    """Bring an in-flight worktree's ``.wade/base_branch`` in line with a just-applied
    PR retarget, so a resumed session's ``sync``/``done`` merge into the new base — not
    the pre-retarget one (#376 review).

    Only acts when a worktree is actually checked out on the branch; otherwise there is
    no pin to diverge (``start()`` writes a fresh one later). Mirrors ``start()``'s
    write-or-clear rule: pin a non-main base, clear the file when the base is ``main``.
    A base *removal* (``declared_base is None``) is a documented no-op — ``bootstrap_draft_pr``
    does not retarget, so the existing pin stays correct and is left untouched.

    Returns ``True`` when the pin was reconciled or no reconciliation was needed (no
    worktree, or a base removal). Returns ``False`` when the pin write/clear *failed*
    (e.g. a read-only worktree): the PR was already retargeted, so a stale pin would
    silently merge a resumed session into the old base — the caller must surface this
    and must not report success (#376 review).
    """
    from wade.git import branch as git_branch
    from wade.git import repo as git_repo
    from wade.git import worktree as git_worktree
    from wade.git.repo import GitError

    if declared_base is None:
        return True

    branch_name = git_branch.make_branch_name(
        config.project.branch_prefix, int(issue.id), issue.title
    )
    try:
        wt_path = next(
            (
                Path(wt.path)
                for wt in git_worktree.list_worktrees(repo_root)
                if wt.branch == branch_name
            ),
            None,
        )
    except Exception:
        # The PR is already retargeted; if a worktree exists we cannot confirm its
        # merge-target pin matches the new base, so a resumed sync/done could target
        # the old one. Fail CLOSED — surface it and let the caller preserve-and-abort
        # rather than report success on a possibly-divergent pin (#376 review).
        logger.warning("plan.inflight_worktree_lookup_failed", exc_info=True)
        console.error(
            f"Retargeted the PR to '{declared_base}', but could not read the repo's "
            f"worktrees to update the in-flight merge-target pin. If a worktree exists "
            f"for #{issue.id}, set its .wade/base_branch to '{declared_base}' (or delete "
            "it if the base is the main branch) before resuming implementation."
        )
        return False
    if wt_path is None:
        return True  # No worktree checked out — nothing to reconcile.

    main_branch = config.project.main_branch
    if not main_branch:
        try:
            main_branch = git_repo.detect_main_branch(repo_root)
        except (GitError, OSError):
            # detect_main_branch shells out to git; a spawn failure raises OSError, not
            # only GitError. Preserve the None fallback so pin reconciliation continues
            # through the caller's preserve-and-abort path (#376 review).
            main_branch = None

    base_file = wt_path / ".wade" / "base_branch"
    try:
        if declared_base != main_branch:
            base_file.parent.mkdir(exist_ok=True)
            base_file.write_text(declared_base + "\n")
        elif base_file.exists():
            # Retargeted back to main — clear the stale non-main pin.
            base_file.unlink()
    except OSError:
        # The PR base is already changed; a stale pin here would merge a resumed
        # session into the OLD base. Surface it loudly and fail so the caller aborts
        # rather than reporting success on a divergent merge target (#376 review).
        logger.warning("plan.inflight_worktree_base_write_failed", exc_info=True)
        console.error(
            f"Retargeted PR to '{declared_base}', but could not update the worktree's "
            f"merge-target pin at {base_file}. A resumed session would still merge into "
            f"its old base. Restore write access to the worktree, then set that file to "
            f"'{declared_base}' (or delete it if the base is '{main_branch}') before "
            "resuming implementation."
        )
        return False
    return True


def _attach_plan_to_existing_issue(
    provider: AbstractTaskProvider,
    config: ProjectConfig,
    issue: Task,
    plan_file: PlanFile,
    repo_root: Path | None,
    *,
    yolo: bool = False,
    refresh_existing_plan: bool = False,
) -> bool:
    """Attach a single plan file to an existing issue via a draft PR.

    Reuses the same label / PR / body-update logic as _create_issues_from_plans
    but skips issue creation since the issue already exists.  The original issue
    body is preserved — the PR link is appended rather than replacing it.

    When the plan declares a base branch that differs from an already-in-flight
    PR's base, the retarget is guarded (:func:`_base_retarget_is_safe`) so it is
    never applied silently.

    Returns ``False`` when attaching did not fully succeed and the caller must
    preserve the freshly generated plan and abort finalization — instead of
    finalizing the issue and force-removing the planning worktree, which would
    discard the replacement plan (#376). That covers three cases: the retarget
    guard refused an in-flight base change, ``bootstrap_draft_pr`` failed to
    create/retarget the draft PR, or the in-flight worktree's merge-target pin
    could not be reconciled after a retarget. Returns ``True`` on success and on
    the "not in a git repo" path (no draft PR is expected there).
    """
    # Add complexity label
    if plan_file.complexity:
        try:
            add_complexity_label(provider, issue.id, plan_file.complexity)
        except Exception as e:
            logger.warning("plan.complexity_label_failed", error=str(e))

    # Bootstrap draft PR with full plan content
    if repo_root is not None:
        if not _base_retarget_is_safe(config, issue, plan_file, repo_root, yolo=yolo):
            return False
        pr_info = bootstrap_draft_pr(
            issue_number=issue.id,
            issue_title=issue.title,
            plan_body=plan_file.body,
            config=config,
            repo_root=repo_root,
            base_branch=plan_file.base_branch,
            refresh_existing_plan=refresh_existing_plan,
        )
        if pr_info:
            pr_number = pr_info.get("number", "?")
            pr_url = pr_info.get("url", "")
            console.success(f"Draft PR #{pr_number}: {pr_url}")

            # A confirmed in-flight retarget changed the PR's base above; keep the
            # existing worktree's merge-target pin in step so sync/done don't keep
            # targeting the old base (#376 review). No-op when no worktree exists.
            # A failed write is not swallowed — abort so the caller preserves the
            # plan instead of finalizing on a divergent merge target.
            if not _reconcile_inflight_worktree_base(
                config, issue, repo_root, plan_file.base_branch
            ):
                return False

            # Preserve the original issue body and append the PR link. A retained
            # handoff replays this path after a failed finalization or cleanup, and
            # bootstrap_draft_pr reuses the same open PR — so only append a link the
            # body does not already carry instead of stacking duplicates (#516).
            original_body = (issue.body or "").rstrip("\n")
            plan_link = f"**Full plan**: PR #{pr_number}"
            if plan_link not in original_body:
                updated_body = original_body + f"\n\n{plan_link}"
                try:
                    provider.update_task(issue.id, body=updated_body)
                except Exception as e:
                    logger.warning("plan.pr_link_update_failed", error=str(e))
        else:
            # Draft-PR bootstrap failed (missing declared base, a failed retarget,
            # or a transient gh error). The plan lives only in the worktree/plan
            # dir; finalizing now would discard it when the worktree is force-
            # removed. Signal preserve-and-abort instead of reporting success (#376).
            console.warn(f"Could not create draft PR for #{issue.id}")
            return False
    else:
        console.warn("Not in a git repo — skipping draft PR creation.")
    return True


_SUPERSEDE_BANNER_RE = re.compile(r"\A\s*>\s*\*\*Superseded by[^\n]*\*\*\n*")


def _with_supersede_banner(body: str, issue_refs: str) -> str:
    """Prepend a 'Superseded by' banner, replacing any existing one instead of stacking."""
    banner = f"> **Superseded by {issue_refs}**"
    rest = _SUPERSEDE_BANNER_RE.sub("", body or "", count=1).strip("\n")
    return f"{banner}\n\n{rest}" if rest else banner


def _supersede_issue_with_plans(
    provider: AbstractTaskProvider,
    config: ProjectConfig,
    issue: Task,
    plan_files: list[PlanFile],
    repo_root: Path | None,
    yolo: bool,
    persisted_issues: dict[str, str] | None = None,
    persisted_plan_digests: dict[str, str] | None = None,
    pending_issue_markers: dict[str, str] | None = None,
    pending_issue_branch_titles: dict[str, str] | None = None,
    handoff_id: str | None = None,
    save_progress: Callable[[], bool] | None = None,
) -> list[str]:
    """Split an existing issue into one new issue per plan file and supersede it.

    Creates a new lightweight issue + draft PR for every plan file (reusing
    _create_issues_from_plans). Only if every plan file became an issue does
    this comment on and close the original issue as "not planned" — a partial
    result leaves the original open so the split is never silently incomplete.

    Returns the list of successfully created issue numbers — the caller must
    pass only these to _finalize_issues, never the closed original.
    """
    created_numbers, failed_files = _create_issues_from_plans(
        provider=provider,
        config=config,
        plan_files=plan_files,
        repo_root=repo_root,
        persisted_issues=persisted_issues,
        persisted_plan_digests=persisted_plan_digests,
        pending_issue_markers=pending_issue_markers,
        pending_issue_branch_titles=pending_issue_branch_titles,
        handoff_id=handoff_id,
        save_progress=save_progress,
    )

    if failed_files:
        console.warn(
            f"Only {len(created_numbers)}/{len(plan_files)} plan file(s) became issues "
            f"— leaving #{issue.id} open. Failed: {', '.join(failed_files)}"
        )
        logger.warning(
            "plan.supersede_partial_failure",
            issue=issue.id,
            plan_file_count=len(plan_files),
            created_count=len(created_numbers),
        )
        return created_numbers

    issue_refs = ", ".join(f"#{n}" for n in created_numbers)

    try:
        provider.comment_on_task(
            issue.id,
            f"\U0001f500 Superseded during planning — split into {issue_refs}.",
        )
    except Exception as e:
        logger.warning("plan.supersede_comment_failed", issue=issue.id, error=str(e))
        console.warn(f"Could not comment on #{issue.id}: {e}")

    try:
        updated_body = _with_supersede_banner(issue.body or "", issue_refs)
        provider.update_task(issue.id, body=updated_body)
    except Exception as e:
        logger.warning("plan.supersede_banner_failed", issue=issue.id, error=str(e))
        console.warn(f"Could not update #{issue.id} body: {e}")

    console.empty()
    proceed = yolo or prompts.confirm(
        f"Close #{issue.id} as superseded by {issue_refs}?",
        default=True,
    )
    if not proceed:
        console.info(f"Leaving #{issue.id} open — superseded by {issue_refs}.")
        return created_numbers

    try:
        provider.close_task(issue.id, reason=CloseReason.NOT_PLANNED)
        console.success(f"Closed #{issue.id} as not planned — superseded by {issue_refs}")
    except Exception as e:
        logger.warning("plan.supersede_close_failed", issue=issue.id, error=str(e))
        console.warn(f"Could not close #{issue.id}: {e}")

    return created_numbers


def _finalize_issues(
    provider: AbstractTaskProvider,
    config: ProjectConfig,
    issue_numbers: list[str],
    ai_tool: str | None = None,
    model: str | None = None,
    usage: TokenUsage | None = None,
    repo_root: Path | None = None,
    planning_worktree: Path | None = None,
    effort: EffortLevel | None = None,
    yolo: bool = False,
    sandbox: bool | None = None,
    native_result: PlanSessionResult | None = None,
    plan_bundle: PlanBundle | None = None,
    plan_files: list[PlanFile] | None = None,
) -> bool | _PlanFinalizationFailure | None:
    """Finalize newly created issues: token summaries, labels, hints.

    *sandbox* is the planning session's resolved profile, carried only so an
    accepted implement offer can hand it to the implementation session.

    Returns a bool if the user accepted the offer to implement (single issue),
    :data:`PLAN_FINALIZATION_FAILED` when generated plans must be preserved, or
    None if no interactive offer was made.
    """
    # Apply token usage to issue bodies
    if usage is not None and usage.total_tokens:
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
    session_id = native_result.session_id if native_result else usage.session_id if usage else None
    if session_id:
        for issue_id in issue_numbers:
            # Update issue body
            try:
                task = provider.read_task(issue_id)
                new_body = append_session_to_body(
                    task.body,
                    phase="Plan",
                    ai_tool=ai_tool or "",
                    session_id=session_id,
                )
                if native_result is not None:
                    from wade.utils.body_markers import upsert_marked_block

                    provenance = native_result.model_dump_json(exclude={"plan"}, indent=2)
                    new_body = upsert_marked_block(
                        new_body,
                        "<!-- wade:plan-provenance:start -->",
                        "<!-- wade:plan-provenance:end -->",
                        "## Native Planning Provenance\n\n```json\n" + provenance + "\n```\n\n"
                        "Transcript and token usage are unavailable "
                        "from the collected-session API.",
                    )
                provider.update_task(issue_id, body=new_body)
                logger.info("plan.session_id_recorded", issue=issue_id)
            except Exception:
                console.warn(f"Could not record planning session provenance on issue #{issue_id}.")
                if native_result is not None:
                    return PLAN_FINALIZATION_FAILED

    # Add planned-by labels
    for issue_id in issue_numbers:
        try:
            add_planned_by_labels(provider, issue_id, ai_tool, model)
        except Exception as e:
            console.warn(f"Could not apply planned-by labels to #{issue_id}: {e}")
            logger.warning("plan.planned_by_labels_failed", task_id=issue_id, error=str(e))

    # Auto-dependency analysis for 2+ issues
    if plan_bundle is not None and plan_files is not None and len(plan_files) != len(issue_numbers):
        console.error("Some plans were not persisted; recover output before implementing any task.")
        return PLAN_FINALIZATION_FAILED
    if plan_bundle is not None and plan_files is not None and len(issue_numbers) >= 2:
        from wade.models.deps import DependencyEdge, DependencyGraph
        from wade.services.deps_service import apply_deps_to_issues, create_tracking_issue

        numbers = {
            plan.path.name: number for plan, number in zip(plan_files, issue_numbers, strict=True)
        }
        declared_graph = DependencyGraph(
            edges=[
                DependencyEdge(from_task=numbers[dependency], to_task=numbers[member.filename])
                for member in plan_bundle.plans
                if member.filename in numbers
                for dependency in member.depends_on
            ]
        )
        declared_graph.topological_order = declared_graph.topo_sort(issue_numbers)
        if declared_graph.edges:
            affected = sorted(
                {node for edge in declared_graph.edges for node in (edge.from_task, edge.to_task)}
            )
            if apply_deps_to_issues(provider, affected, declared_graph.edges) != len(affected):
                console.error(
                    "Task dependency persistence was incomplete; planning output must be recovered."
                )
                return PLAN_FINALIZATION_FAILED
            titles = {
                number: plan.title for plan, number in zip(plan_files, issue_numbers, strict=True)
            }
            if (
                create_tracking_issue(provider, config, issue_numbers, declared_graph, titles)
                is None
            ):
                return PLAN_FINALIZATION_FAILED
    elif len(issue_numbers) >= 2:
        console.empty()
        console.step("Running automatic dependency analysis...")
        try:
            from wade.services.deps_service import analyze_deps

            graph = analyze_deps(
                issue_numbers=issue_numbers,
                ai_tool=ai_tool,
                model=model,
                ai_explicit=False,
                model_explicit=False,
                planning_worktree=planning_worktree,
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
    if len(issue_numbers) == 1:
        if planning_worktree is not None and repo_root is not None and config.knowledge.enabled:
            # The next attached implementation bootstrap carries main's pending
            # ratings into its PR. Delay the handoff until every plan-finalizing
            # side effect above succeeded, but run it immediately before that
            # bootstrap so this detached vote is not left behind.
            result = _offer_to_implement(
                issue_numbers[0],
                before_start=lambda: _flush_planning_votes_before_implementation(
                    planning_worktree, repo_root, config
                ),
            )
        else:
            result = _offer_to_implement(issue_numbers[0])
        if result is not None:
            return result
    elif len(issue_numbers) >= 2:
        console.info("When you're ready to implement, run:")
        console.detail(f"wade implement-batch {' '.join(issue_numbers)}")

    return None


def _print_implement_hint(issue_number: str) -> None:
    """Print the hint for manually starting an implementation session."""
    console.info("When you're ready to implement, run:")
    console.detail(f"wade implement {issue_number}")


def _flush_planning_votes_before_implementation(
    planning_worktree: Path,
    repo_root: Path,
    config: ProjectConfig,
) -> bool:
    """Flush completed plan-session votes just before an attached bootstrap.

    A failed handoff returns ``False`` to prevent implementation from starting.
    The caller's immediate cleanup retry owns the persistent-failure report, so
    users see one recovery message rather than one for each identical attempt.
    """
    from wade.services.knowledge_service import flush_staged_ratings

    handoff = flush_staged_ratings(planning_worktree, repo_root, config.knowledge)
    return handoff.success


def _offer_to_implement(
    issue_number: str,
    *,
    before_start: Callable[[], bool] | None = None,
) -> bool | None:
    """Prompt the user to start an implementation session on the newly planned issue.

    Returns True/False if the user accepted/implementation session succeeded or failed,
    or None if the prompt was skipped (non-TTY) or declined.
    """
    if not prompts.is_tty():
        _print_implement_hint(issue_number)
        return None

    accepted = prompts.confirm(
        f"Start implementing #{issue_number} now?",
        default=True,
    )
    if not accepted:
        _print_implement_hint(issue_number)
        return None

    if before_start is not None and not before_start():
        return False

    try:
        # Tell implementation startup this is an accepted handoff. It assesses
        # the runtime that actually encloses the handoff rather than the former
        # planner child, which has already exited.
        result = start_implementation_session(
            target=issue_number,
            plan_handoff=True,
        )
        return result.success
    except Exception:
        logger.exception("plan.work_session_start_failed", issue=issue_number)
        return False


def _remove_planning_worktree(
    repo_root: Path | None,
    worktree: Path | None,
    config: ProjectConfig | None = None,
) -> bool:
    """Flush staged rating votes, then remove a planning worktree if it exists."""
    if repo_root is None or worktree is None:
        return True
    if config is not None and config.knowledge.enabled:
        from wade.services.knowledge_service import flush_staged_ratings

        result = flush_staged_ratings(worktree, repo_root, config.knowledge)
        if not result.success:
            console.error(
                "Could not hand off staged knowledge votes; preserving the planning "
                f"worktree at {worktree}. {result.message or 'Retry after restoring access.'}"
            )
            console.hint(RETAINED_VOTE_RECOVERY_HINT)
            return False
    from wade.git import worktree as git_worktree

    try:
        git_worktree.remove_worktree(repo_root, worktree, force=True)
    except Exception as exc:
        # The return value is the caller's cleanup contract — a swallowed
        # failure here would leave the worktree behind while `plan()` reports
        # success, so surface it and let the caller decide.
        logger.warning("plan.planning_worktree_remove_failed", error=str(exc))
        console.warn(f"Could not remove the planning worktree at {worktree}: {exc}")
        return False
    return True


def _cleanup_plan_dir_or_worktree(
    plan_dir: str,
    repo_root: Path | None,
    planning_worktree: Path | None,
    config: ProjectConfig | None = None,
) -> bool:
    """Clean up the plan directory — either a worktree or a temp dir."""
    if planning_worktree is not None:
        return _remove_planning_worktree(repo_root, planning_worktree, config)
    else:
        _cleanup_plan_dir(plan_dir)
        return True


def _cleanup_plan_dir(plan_dir: str) -> None:
    """Remove the temporary plan directory."""
    with contextlib.suppress(Exception):
        path = Path(plan_dir)
        if (
            path.name == "plans"
            and path.parent.name == ".wade"
            and path.parent.parent.name.startswith("wade-plan-")
        ):
            path = path.parent.parent
        shutil.rmtree(path, ignore_errors=True)


def _preserve_generated_plans(
    plan_dir: str,
    repo_root: Path | None,
    planning_worktree: Path | None,
    config: ProjectConfig | None = None,
) -> bool:
    """Salvage generated ``PLAN*.md`` before the normal cleanup, then clean up.

    Used when the strict gate (:func:`_select_valid_plans`) rejects a batch — every
    file failed validation, or the user aborted a partial run. Deleting the plan
    dir/worktree outright would discard output the user can often repair by hand (a
    missing ``## Complexity`` header is a one-line edit), forcing a full AI
    re-planning session for a trivial fix. So copy the files to a stable temp dir
    first, point the user at them, and only then run the usual cleanup,
    which keeps the worktree/temp dir from lingering.
    """
    path = Path(plan_dir)
    try:
        if path.name == "plans" and path.parent.name == ".wade":
            names = list_state_files_strict(path.parent.parent, ("plans",))
        else:
            names = tuple(sorted(os.listdir(path)))
    except (StateFileError, OSError) as exc:
        access_denied = isinstance(exc, StateFileAccessError) or (
            isinstance(exc, OSError) and exc.errno in {errno.EACCES, errno.EPERM}
        )
        logger.warning("plan.preserve_enumeration_failed", exc_info=True)
        _retain_inaccessible_handoff(
            plan_dir, planning_worktree, access_denied=access_denied, count=None
        )
        return False

    generated = tuple(name for name in names if name.startswith("PLAN") and name.endswith(".md"))
    has_native_output = bool(
        {"native-session.json", ".native", interactive_plan.STATE}.intersection(names)
    )
    if not generated and not has_native_output:
        # Nothing to salvage — the usual cleanup can run unconditionally.
        return _cleanup_plan_dir_or_worktree(plan_dir, repo_root, planning_worktree, config)

    try:
        preserved = Path(tempfile.mkdtemp(prefix="wade-plans-"))
        # Recovery is not import: keep owned raw output, without following links
        # or treating native source files as additional tasks.
        shutil.copytree(plan_dir, preserved, dirs_exist_ok=True, symlinks=True)
    except OSError:
        # A copy failed after mkdtemp, so the temp dir may hold only part of the
        # batch. Retain the original plan dir/worktree instead of deleting it —
        # never trade a partial salvage for the intact source — and point the
        # user at the untouched originals still sitting in ``plan_dir``.
        logger.warning("plan.preserve_generated_failed", exc_info=True)
        _retain_inaccessible_handoff(
            plan_dir, planning_worktree, access_denied=False, count=len(generated)
        )
        return False

    # Every file copied cleanly — the stable copy now stands in for the source,
    # so the normal cleanup can remove the worktree/temp dir.
    _report_preserved_plans(len(generated), preserved)
    return _cleanup_plan_dir_or_worktree(plan_dir, repo_root, planning_worktree, config)


def _retain_finalization_failed_handoff(
    plan_dir: str,
    repo_root: Path | None,
    planning_worktree: Path | None,
    config: ProjectConfig,
    *,
    issue_numbers: list[str],
    plan_files: list[PlanFile],
    progress: PlanHandoffProgress | None,
) -> None:
    """Keep durable or still-reconcilable handoffs recoverable after finalization fails."""
    pending_markers = progress is not None and bool(progress.pending_issue_markers)
    if planning_worktree is not None and (pending_markers or len(issue_numbers) == len(plan_files)):
        _retain_inaccessible_handoff(plan_dir, planning_worktree, access_denied=False)
        return
    _preserve_generated_plans(plan_dir, repo_root, planning_worktree, config)


def _report_preserved_plans(count: int, location: Path) -> None:
    """Point the user at the ``count`` salvaged plan files under ``location``."""
    console.info(
        f"Preserved {count} generated plan file(s) — fix the reported "
        "errors and re-run `wade plan` instead of regenerating from scratch."
    )
    console.hint(f"Plan files: {location}")


def _retain_inaccessible_handoff(
    plan_dir: str,
    planning_worktree: Path | None,
    *,
    access_denied: bool = True,
    count: int | None = None,
) -> None:
    """Report an intact original without cleaning an uncollected handoff."""

    path = Path(plan_dir)
    if count is None:
        try:
            if path.name == "plans" and path.parent.name == ".wade":
                names = list_state_files_strict(path.parent.parent, ("plans",))
            else:
                names = tuple(sorted(os.listdir(path)))
            count = sum(name.startswith("PLAN") and name.endswith(".md") for name in names)
        except (StateFileError, OSError):
            count = None

    if count is None:
        reason = " because filesystem access was denied" if access_denied else ""
        console.warn(
            "Generated plan files could not be enumerated"
            f"{reason}; the count is unknown. The original planning output was retained."
        )
    else:
        console.info(f"Retained {count} generated plan file(s) in the original planning output.")
    console.hint(f"Plan files: {path}")
    if planning_worktree is not None:
        command = shlex.join(["wade", "plan", "--recover", str(planning_worktree)])
        prefix = (
            "After restoring filesystem access, recover with"
            if access_denied
            else "Recover the retained handoff with"
        )
        console.hint(f"{prefix}: {command}")
