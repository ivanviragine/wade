"""Implementation service core — session start entry point and task resolution.

Hosts ``start`` (the implementation entry point) plus its task/worktree
resolution helpers and post-session usage capture. Other session-lifecycle
concerns live in sibling modules: ``draft_pr``, ``sync``, ``lifecycle``,
``done``, ``cleanup``, ``bootstrap``, ``usage_tracking``, and ``batch``.
"""

from __future__ import annotations

import contextlib
import re
import tempfile
from pathlib import Path

import structlog
from crossby.ai_tools import AbstractAITool
from crossby.models.ai import AIToolID, EffortLevel

from wade.config.loader import load_config
from wade.git import branch as git_branch
from wade.git import pr as git_pr
from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.git.repo import GitError
from wade.models.config import ProjectConfig
from wade.models.hooks import SessionPhase
from wade.models.permission import PermissionMode, permission_mode_launch_kwargs
from wade.models.session import ImplementResult, MergeStatus, SyncEventType, SyncResult
from wade.models.task import Task
from wade.models.workflow import SessionKind
from wade.providers.base import AbstractTaskProvider
from wade.providers.registry import get_provider
from wade.services.ai_resolution import (
    LAUNCH_NETWORK_ACCESS,
    SandboxCapabilityError,
    announce_inherited_sandbox,
    build_relaunch_command,
    confirm_ai_selection,
    enforce_sandbox_capability,
    resolve_ai_tool,
    resolve_effort,
    resolve_model,
    resolve_permission_mode,
    resolve_sandbox,
)
from wade.services.implementation_service._shared import (
    extract_issue_from_branch,
    find_open_pr_branch_for_issue,
    find_worktree_path,
    resolve_task_branch,
)
from wade.services.implementation_service.bootstrap import (
    _resolve_worktrees_dir,
    bootstrap_worktree,
    write_plan_md,
)
from wade.services.implementation_service.draft_pr import (
    _branch_has_real_work,
    _resolve_head_sha,
    _restore_scaffold_head,
    bootstrap_draft_pr,
    build_implementation_prompt,
    extract_plan_from_pr_body,
    reroot_scaffold_branch_for_retarget,
)
from wade.services.implementation_service.lifecycle import _post_implementation_lifecycle
from wade.services.implementation_service.sync import catchup
from wade.services.implementation_service.usage_tracking import (
    _enrich_body_with_usage,
    _usage_has_token_metrics,
)
from wade.services.prompt_delivery import deliver_prompt_if_needed
from wade.services.task_service import (
    add_implemented_by_labels,
    add_in_progress_label,
)
from wade.ui import prompts
from wade.ui.console import console
from wade.utils import stale_base
from wade.utils.body_markers import enforce_body_budget, update_body_preserving_markers
from wade.utils.gitref import is_valid_git_ref
from wade.utils.runtime_env import (
    INHERITED_SANDBOX_HINT,
    detect_ai_cli_env,
    parent_runtime,
    requires_unsandboxed_relaunch,
)
from wade.utils.terminal import (
    compose_implement_title,
    launch_in_new_terminal,
    set_terminal_title,
    start_title_keeper,
    stop_title_keeper,
)

logger = structlog.get_logger()

# ``ImplementResult`` is re-exported here for backward compatibility — external
# code imports it from ``wade.services.implementation_service``.
__all__ = [
    "ImplementResult",
    "_capture_post_session_usage",
    "_detect_ai_cli_env",
    "_resolve_task_target",
    "_resolve_worktree_from_plan",
    "_resume_autonomy_args",
    "start",
]


# ---------------------------------------------------------------------------


def _resume_autonomy_args(adapter: AbstractAITool, permission_mode: PermissionMode) -> list[str]:
    """Autonomy CLI flags to append to a resumed session, matching a fresh launch.

    crossby's ``build_resume_command()`` takes only a session id, so — unlike
    ``build_launch_command(**permission_mode_launch_kwargs(...))`` — it never applies
    the resolved permission mode. Without this, a resumed session runs at the tool's
    default tier regardless of config/UI (e.g. agy subagents get shell denied even
    though the confirmation shows ``yolo``). Recompute the exact autonomy args a
    fresh launch would emit — including crossby's capability-aware downgrades — and
    append them to the resume command.

    Reuses crossby's internal ``_autonomy_launch_args`` for parity with
    ``build_launch_command``; calling ``yolo_args()`` etc. directly would skip the
    downgrade ladder. The proper long-term home is a crossby-side
    ``build_resume_command`` that accepts autonomy — a follow-up there; this MUST be
    re-verified on a crossby bump. ``plan_mode=False`` mirrors this path's fresh
    launch, which never requests plan mode.
    """
    return adapter._autonomy_launch_args(
        adapter.capabilities(),
        plan_mode=False,
        **permission_mode_launch_kwargs(permission_mode),
    )


# The probe itself now lives in the leaf ``utils.runtime_env`` module so the
# review and delegation paths share one implementation (#480). Re-exported under
# its original private name because this module lists it in ``__all__`` and both
# the call site below and a good deal of the test suite patch it here.
_detect_ai_cli_env = detect_ai_cli_env


def _capture_post_session_usage(
    transcript_path: Path | None,
    adapter: AbstractAITool,
    repo_root: Path,
    branch: str,
    ai_tool: str,
    model: str | None,
    issue_number: str | None = None,
    provider: AbstractTaskProvider | None = None,
) -> str | None:
    """Post-AI-exit processing: parse transcript, update PR and issue with token usage.

    Returns the primary model detected from the transcript (for implemented-model label),
    or the explicitly passed model if no breakdown is available.
    """
    if not transcript_path or not transcript_path.is_file():
        return None

    # Parse transcript for token usage
    try:
        usage = adapter.parse_transcript(transcript_path)
    except Exception as e:
        logger.warning("implementation.transcript_parse_failed", error=str(e))
        return None

    has_tokens = _usage_has_token_metrics(usage)
    has_session = bool(usage and usage.session_id)
    if not has_tokens and not has_session:
        logger.warning("implementation.no_token_usage", transcript=str(transcript_path))
        console.warn(f"No token usage found in transcript: {transcript_path}")
        return None

    # Use transcript model_breakdown as source of truth when model wasn't set explicitly
    effective_model = model or (
        usage.model_breakdown[0].model if usage and usage.model_breakdown else None
    )

    # Update PR body with usage stats and session ID — only on an OPEN PR
    # (a merged/closed PR is not ours to rewrite; a lookup failure is transient).
    lookup = git_pr.get_pr_for_branch(repo_root, branch)
    if lookup.is_open and lookup.pr is not None:
        pr_number = lookup.pr.number
        try:
            # Re-read the body immediately before writing and rewrite only wade's
            # own usage marker block, so a concurrent edit outside it survives
            # (A4); enforce GitHub's size cap with a visible warning (A5).
            updated = update_body_preserving_markers(
                read_body=lambda: git_pr.get_pr_body(repo_root, pr_number),
                write_body=lambda b: git_pr.update_pr_body(repo_root, pr_number, b),
                transform=lambda b: _enrich_body_with_usage(
                    b, ai_tool, effective_model, usage, has_tokens, has_session
                ),
                warn=console.warn,
                label=f"PR #{pr_number} body",
            )
            if updated:
                if has_tokens:
                    console.success("Updated PR with implementation usage stats.")
                logger.info(
                    "implementation.impl_usage_updated",
                    pr=pr_number,
                    total_tokens=usage.total_tokens if usage else None,
                )
        except Exception:
            logger.debug("implementation.pr_body_read_failed", exc_info=True)
    elif lookup.lookup_failed:
        logger.debug("implementation.pr_lookup_failed", branch=branch)
    else:
        logger.debug("implementation.no_pr_for_branch", branch=branch)

    # Embed usage stats and session ID in the issue body
    if issue_number and provider:
        with contextlib.suppress(Exception):
            task = provider.read_task(str(issue_number))
            new_body = _enrich_body_with_usage(
                task.body,
                ai_tool,
                effective_model,
                usage,
                has_tokens,
                has_session,
            )
            new_body = enforce_body_budget(
                new_body, warn=console.warn, label=f"issue #{issue_number} body"
            )
            provider.update_task(str(issue_number), body=new_body)
            if has_tokens:
                console.success("Updated issue with implementation usage stats.")
            logger.info("implementation.impl_usage_issue_updated", issue=issue_number)

    return effective_model


# ---------------------------------------------------------------------------
# Stale-base surfacing (#407) — loud, in-session signal when catchup can't advance
# ---------------------------------------------------------------------------

# Human/agent-facing phrasing per reason token, used in the prompt banner + console panel.
_STALE_REASON_LABELS = {
    stale_base.REASON_UNTRACKED_CONFLICT: (
        "startup catchup hit an untracked-file collision and did not advance"
    ),
    stale_base.REASON_MERGE_CONFLICT: "startup catchup hit a merge conflict and aborted",
    stale_base.REASON_SKIP_WORKTREE: (
        "startup catchup was blocked by a wade-managed file and did not advance"
    ),
    stale_base.REASON_UNKNOWN: "startup catchup did not advance",
}


def _classify_catchup_failure(result: SyncResult) -> str:
    """Map a failed catchup ``SyncResult`` to a stale-base reason token (#407)."""
    if result.conflicts:
        return stale_base.REASON_MERGE_CONFLICT
    for ev in result.events:
        if ev.event == SyncEventType.UNTRACKED_CONFLICT:
            return stale_base.REASON_UNTRACKED_CONFLICT
        if ev.event == SyncEventType.CONFLICT:
            return stale_base.REASON_MERGE_CONFLICT
    return stale_base.REASON_UNKNOWN


def _commits_behind_base(repo_root: Path, base: str, current: str) -> int | None:
    """Count commits ``current`` is behind its base, reusing the ref catchup already fetched.

    Prefers ``origin/<base>`` (fetched by catchup on every reachable path before it could
    fail) and falls back to the local ``<base>`` — never triggering a *second* fetch on
    session start (#407). Returns ``None`` when neither ref resolves (lag unknown), which is
    NOT the same as 0 (verified up to date): an unknown lag must never clear a marker an
    earlier startup wrote (#408 review).
    """
    refs: list[str] = []
    try:
        if git_repo.has_remote(repo_root):
            refs.append(f"origin/{base}")
    except GitError:
        pass
    refs.append(base)
    for ref in refs:
        try:
            # Inverted args on purpose: commits_ahead(repo, X, Y) = `rev-list --count Y..X`
            # = commits on X not on Y. With X=base-ref, Y=current that is commits the base
            # has that `current` lacks — i.e. how far `current` is BEHIND the base.
            return git_branch.commits_ahead(repo_root, ref, current)
        except GitError:
            continue
    return None


def _surface_stale_base_if_behind(
    *,
    repo_root: Path,
    worktree_path: Path,
    base: str,
    current: str,
    reason: str,
) -> str | None:
    """Loudly surface a stale base after startup catchup, returning the prompt banner (#407).

    On commits-behind > 0: escalate to prominent error-level output, persist the
    ``.wade/stale_base`` marker (count + reason), and return the warning text to inject
    into the initial prompt. On == 0 (branch caught up): clear any stale marker and return
    ``None``. On unknown lag (neither base ref resolves): leave any existing marker
    untouched and return ``None`` — never erasing an earlier startup's warning (#408 review).
    """
    behind = _commits_behind_base(repo_root, base, current)
    if behind is None:
        # Lag unknown (neither base ref resolved) — never clear a marker an earlier startup
        # wrote, and do not emit a count-less banner. A verified 0 still clears below (#408).
        return None
    if behind <= 0:
        stale_base.clear_stale_base(worktree_path)
        return None

    stale_base.write_stale_base(worktree_path, behind, reason)

    plural = "S" if behind != 1 else ""
    label = _STALE_REASON_LABELS.get(reason, _STALE_REASON_LABELS[stale_base.REASON_UNKNOWN])
    warning = (
        f"⚠️ BRANCH IS {behind} COMMIT{plural} BEHIND {base} — {label}. "
        "Do NOT start work until you sync: run `wade implementation-session sync` "
        "(resolve any conflicts it reports), then re-check with `wade status`. "
        "You are building against an outdated base until this is resolved."
    )
    console.panel(
        warning,
        title="⚠ Stale base — startup catchup did not advance",
        border_style="error",
    )
    return warning


def _catchup_and_surface_staleness(
    *,
    repo_root: Path,
    worktree_path: Path,
    branch_name: str,
    effective_base: str,
) -> str | None:
    """Run startup catchup and surface any staleness, returning the prompt banner (#407).

    Catchup is documented as non-blocking: whatever it does (succeed, fail cleanly, or
    raise), this always computes commits-behind and surfaces a stale base LOUDLY — a
    boxed panel plus a ``.wade/stale_base`` marker the AI prompt and every subsequent
    session-start hook re-inject. Neither the catchup call nor the staleness surfacing
    itself may ever propagate an exception out of session start (#408 review) — that
    would be a strictly worse regression than the silent-stale-base bug this exists to
    fix, since it would abort the session launch entirely instead of just proceeding
    on a base whose staleness is now at least visible.
    """
    catchup_result: SyncResult | None = None
    catchup_reason = stale_base.REASON_UNKNOWN
    catchup_raised = False
    try:
        catchup_result = catchup(project_root=worktree_path)
        if not catchup_result.success:
            catchup_reason = _classify_catchup_failure(catchup_result)
    except Exception as exc:
        logger.debug("start.catchup_failed", exc_info=True)
        catchup_raised = True
        # No result to inspect — a non-conflict GitError naming a file "would be
        # overwritten by merge" is the skip-worktree blocker; anything else is
        # unknown. Either way the staleness surfacing below still fires loudly.
        catchup_reason = (
            stale_base.REASON_SKIP_WORKTREE
            if "overwritten by merge" in str(exc)
            else stale_base.REASON_UNKNOWN
        )

    resolved_base = (
        catchup_result.main_branch
        if catchup_result and catchup_result.main_branch
        else effective_base
    )
    stale_warning: str | None = None
    try:
        stale_warning = _surface_stale_base_if_behind(
            repo_root=repo_root,
            worktree_path=worktree_path,
            base=resolved_base,
            current=branch_name,
            reason=catchup_reason,
        )
    except Exception:
        logger.debug("start.stale_base_surface_failed", exc_info=True)
    if catchup_raised and stale_warning is None:
        # catchup() itself raised and nothing else was surfaced (lag unresolved, or the
        # surfacing step above also failed) — restore the pre-#407 fallback warning so
        # this never looks identical to a verified up-to-date branch (#408 review).
        console.warn("Startup catchup failed — proceeding anyway.")
    return stale_warning


# ---------------------------------------------------------------------------
# Implementation start
# ---------------------------------------------------------------------------


def start(
    target: str,
    ai_tool: str | None = None,
    model: str | None = None,
    project_root: Path | None = None,
    detach: bool = False,
    cd_only: bool = False,
    *,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    effort: str | None = None,
    effort_explicit: bool = False,
    resume_session_id: str | None = None,
    resume_ai_tool: str | None = None,
    yolo: bool | None = None,
    permission_mode: str | None = None,
    permission_mode_explicit: bool = False,
    sandbox: bool | None = None,
    base_branch: str | None = None,
    work_skills: list[str] | None = None,
    review_skills: list[str] | None = None,
    refresh_skills: bool = False,
    plan_handoff: bool = False,
) -> ImplementResult:
    """Start an implementation session on an issue.

    Steps:
    1. Read the issue from the provider
    2. Create worktree and branch
    3. Bootstrap worktree (copy files, hooks, issue context)
    4. Resolve model from complexity
    5. Build implementation prompt and pass it as initial message to the AI tool
    6. Launch AI tool (or print path if cd_only / detach)
    7. Post-exit processing

    Args:
        target: Issue number or plan file path.
        ai_tool: AI tool to use (overrides config).
        model: Model to use (overrides config + complexity mapping).
        project_root: Repository root (defaults to CWD).
        detach: If True, launch AI in a new terminal tab.
        cd_only: If True, create worktree and print path only (no AI launch).
        plan_handoff: Internal marker for an accepted ``wade plan`` handoff.
            An unrestricted implementation from a known sandboxed parent fails
            closed with a host-terminal command, because any child it launches
            still inherits that runtime's OS sandbox.

    Returns:
        ImplementResult with success/merged status.
    """
    config = load_config(project_root)
    provider = get_provider(config)

    # Resolve repo root
    cwd = project_root or Path.cwd()
    try:
        repo_root = git_repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return ImplementResult(success=False)

    # Validate an explicit base for well-formedness, symmetric with the
    # plan-declared path (validated at plan-done via is_valid_git_ref). A
    # chain-derived base is a generated branch name and always passes; a hand-typed
    # `--base` with spaces or invalid ref characters fails fast here with a clear
    # message instead of a later, murkier "does not exist" from ref resolution.
    # Check ``is not None`` (not truthiness) so an explicit empty ``--base ""`` is
    # rejected rather than silently inheriting the PR/main base (#376 review).
    if base_branch is not None and not is_valid_git_ref(base_branch):
        console.error_with_fix(
            f"Invalid --base value {base_branch!r}",
            "Use a single well-formed git branch name (no spaces or special characters)",
        )
        return ImplementResult(success=False)

    # When cd_only, redirect all status output to stderr so stdout stays
    # clean for the machine-readable worktree path.
    _original_out = console.out
    if cd_only:
        console.out = console.err
    try:
        # Read the issue
        task = _resolve_task_target(target, provider, config)
        if not task:
            return ImplementResult(success=False)

        # Tracking issue detection — redirect to batch implementation
        # NOTE: The submodule name "batch" collides with the batch() function
        # re-exported by __init__.py, so we access the module via sys.modules.
        import sys

        _batch_mod = sys.modules["wade.services.implementation_service.batch"]
        batch_result = _batch_mod.check_tracking_issue_and_batch(
            task,
            ai_tool=ai_tool,
            model=model,
            project_root=project_root,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort=effort,
            effort_explicit=effort_explicit,
            yolo=yolo,
            permission_mode=permission_mode,
            sandbox=sandbox,
            cd_only=cd_only,
            work_skills=work_skills,
            review_skills=review_skills,
            refresh_skills=refresh_skills,
        )
        if batch_result is not None:
            return ImplementResult(success=batch_result)

        console.rule(f"implement #{task.id}")
        console.kv("Issue", console.issue_ref(task.id, task.title))

        # Resolve the branch early — needs only config + task, so it can be
        # computed before AI selection to allow the PR/plan check below. Resolve
        # by the stable issue *number* rather than re-slugifying the title: the
        # slug is frozen at the first ``wade implement``, but ``done`` later
        # rewrites the issue title to add the required conventional-commit prefix.
        # A reconstructed name would then drift from the real branch, so the
        # PR/plan lookup below would miss the live draft PR and re-bootstrap a
        # duplicate — leaving the resumed task with "no plan attached".
        branch_name = resolve_task_branch(
            repo_root, task.id, task.title, config.project.branch_prefix
        )

        # Check for existing draft PR (from plan flow) before AI selection so that
        # "Plan first" can short-circuit without ever showing the AI confirmation menu.
        # Only an OPEN PR is resumable — a merged/closed PR must not be treated as a
        # live draft (that would extract a stale plan and skip fresh bootstrap).
        pr_lookup = git_pr.get_pr_for_branch(repo_root, branch_name)
        if pr_lookup.lookup_failed:
            # A failed lookup is NOT "no PR" — bootstrapping now would scaffold a
            # duplicate draft over an existing PR and lose its extracted plan.
            console.error_with_fix(
                f"Could not look up the PR for branch {branch_name}",
                "Transient gh error — try again shortly",
            )
            return ImplementResult(success=False)

        # resolve_task_branch matches by issue number, so it can adopt a branch left
        # behind by a CLOSED/MERGED PR (or one that never got a PR) — and, when an
        # issue has several such branches, a branch-name tiebreak could pick a dead
        # one over a live one. When the resolved branch is not itself an open PR,
        # settle the branch by PR *state* (#428 review):
        #   1. resume the issue's OPEN PR if one exists (on any branch), else
        #   2. start fresh on the current-title name — what bootstrap_draft_pr will
        #      reconstruct — so the session's worktree and the draft PR agree rather
        #      than diverging onto the stale branch.
        if not pr_lookup.is_open:
            try:
                open_pr_branch = find_open_pr_branch_for_issue(repo_root, task.id)
            except git_pr.GhCliError:
                # Could NOT list open PRs — this is not "no open PR". Bootstrapping
                # now could scaffold a duplicate over a live PR that lives on another
                # same-issue branch we simply cannot see. Abort, like a failed PR
                # lookup, rather than treat an unknown listing as absence (#428).
                console.error_with_fix(
                    f"Could not list open PRs for issue #{task.id}",
                    "Transient gh error — try again shortly",
                )
                return ImplementResult(success=False)
            resume_branch = open_pr_branch or git_branch.make_branch_name(
                config.project.branch_prefix, int(task.id), task.title
            )
            if resume_branch != branch_name:
                branch_name = resume_branch
                pr_lookup = git_pr.get_pr_for_branch(repo_root, branch_name)
                if pr_lookup.lookup_failed:
                    console.error_with_fix(
                        f"Could not look up the PR for branch {branch_name}",
                        "Transient gh error — try again shortly",
                    )
                    return ImplementResult(success=False)
        existing_pr = pr_lookup.pr if pr_lookup.is_open else None
        plan_content: str | None = None
        proceed_needs_bootstrap = False

        has_plan = False
        if existing_pr is not None:
            console.info(f"Found existing PR #{existing_pr.number} for this task")
            # Extract plan content from PR body
            pr_body = git_pr.get_pr_body(repo_root, existing_pr.number)
            if pr_body:
                plan_content = extract_plan_from_pr_body(pr_body)
                if plan_content:
                    has_plan = True
                    console.detail("Plan content extracted from draft PR")
        if not has_plan:
            # No plan — warn and prompt (skip prompt when cd_only, consistent with
            # cd_only skipping the AI confirm menu).
            if not cd_only:
                console.warn("This task has no plan attached.")
                if prompts.is_tty():
                    choices = ["Plan first (recommended)", "Proceed without plan"]
                    idx = prompts.select("How would you like to proceed?", choices)
                    if idx == 0:
                        from wade.services.plan_service import plan as do_plan

                        plan_ok = do_plan(issue_id=task.id, project_root=project_root)
                        return ImplementResult(success=plan_ok)
            # Only bootstrap when there is no PR yet.
            proceed_needs_bootstrap = existing_pr is None

        if task.complexity:
            console.kv("Complexity", task.complexity.value)

        # Resolve AI tool and model
        resolved_tool = resolve_ai_tool(ai_tool, config, "implement")
        resolved_model = resolve_model(
            model,
            config,
            "implement",
            tool=resolved_tool,
            complexity=task.complexity.value if task.complexity else None,
        )

        # Resolve effort level (per-tier when complexity is known)
        resolved_effort = resolve_effort(
            effort,
            config,
            "implement",
            tool=resolved_tool,
            complexity=task.complexity.value if task.complexity else None,
        )

        # Resolve autonomy / permission mode (yolo is a back-compat alias)
        resolved_permission_mode = resolve_permission_mode(
            permission_mode, yolo, config, "implement"
        )

        # Resolve the AI-runtime sandbox profile. Always pinned explicitly at
        # launch so ambient tool config can never silently move the boundary of a
        # wade-managed session; only toggle-capable tools act on it. The value is
        # resolved here (bootstrap's guard hooks need it) but the capability check
        # is deferred to the launch site — `--cd` and the nested-AI-session guard
        # both exit before launching, and must not fail on a runtime they were
        # never going to start.
        resolved_sandbox = resolve_sandbox(sandbox, config, "implement")

        # When resuming, override the resolved tool and skip interactive confirmation
        if resume_ai_tool:
            resolved_tool = resume_ai_tool
            ai_explicit = True

        # Offer interactive confirmation (skipped when cd_only or both flags explicit).
        if not cd_only:
            (
                resolved_tool,
                resolved_model,
                resolved_effort,
                resolved_permission_mode,
            ) = confirm_ai_selection(
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
                sandbox=resolved_sandbox,
            )

        # Resolve main branch and the effective base. Precedence (#376):
        #   explicit --base (or chain-derived base_branch)
        #   > existing draft PR base (recorded at plan time)
        #   > config.project.main_branch (or detect_main_branch)
        main_branch = config.project.main_branch or git_repo.detect_main_branch(repo_root)
        resolved_base = base_branch
        current_pr_base: str | None = None
        retarget_requested = False
        if existing_pr is not None:
            # Use the base from the PR lookup already performed above. A failed
            # lookup was aborted earlier (lookup_failed), so this base is reliable
            # — unlike a fresh get_pr_base_branch() call, whose None conflates
            # "no base" with "gh failed" and would silently fall back to main.
            current_pr_base = existing_pr.base_ref_name or None
            if resolved_base is None:
                # No explicit override — inherit the base the plan recorded on the PR.
                if current_pr_base:
                    resolved_base = current_pr_base
            else:
                retarget_requested = current_pr_base != resolved_base
        effective_base = resolved_base or main_branch

        worktrees_dir = _resolve_worktrees_dir(config, repo_root)
        repo_name = repo_root.name
        worktree_path = worktrees_dir / repo_name / branch_name.replace("/", "-")

        # Reuse the worktree if the branch already exists (idempotent re-run)
        existing_wt = next(
            (
                Path(wt.path)
                for wt in git_worktree.list_worktrees(repo_root)
                if wt.branch == branch_name
            ),
            None,
        )

        if existing_wt:
            worktree_path = existing_wt
            console.info(f"Reusing existing worktree: {worktree_path}")
        elif existing_pr is not None:
            # Draft PR exists → branch already exists remotely, check it out
            try:
                # Ensure local branch tracks remote
                if not git_branch.branch_exists(repo_root, branch_name):
                    git_repo.fetch_ref(repo_root, "origin", f"{branch_name}:{branch_name}")
                with console.status("Creating worktree..."):
                    git_worktree.checkout_existing_branch_worktree(
                        repo_root=repo_root,
                        branch_name=branch_name,
                        worktree_dir=worktree_path,
                    )
                console.kv("Worktree", str(branch_name))
                console.kv("Path", str(worktree_path))
            except GitError as e:
                console.error(f"Failed to create worktree: {e}")
                return ImplementResult(success=False)
        else:
            try:
                with console.status("Creating worktree..."):
                    if git_branch.branch_exists(repo_root, branch_name):
                        # Branch exists locally but no worktree — reuse it
                        git_worktree.checkout_existing_branch_worktree(
                            repo_root=repo_root,
                            branch_name=branch_name,
                            worktree_dir=worktree_path,
                        )
                    else:
                        git_worktree.create_worktree(
                            repo_root=repo_root,
                            branch_name=branch_name,
                            worktree_dir=worktree_path,
                            base_branch=effective_base,
                        )
                console.kv("Worktree", str(branch_name))
                console.kv("Path", str(worktree_path))
            except GitError as e:
                console.error(f"Failed to create worktree: {e}")
                return ImplementResult(success=False)

        # Resolve the immutable session bundle before any provider-side mutation.
        # Discovery must happen against the actual target worktree (not merely the
        # caller's checkout) so branch-specific project skills retain precedence.
        # The later full bootstrap reuses this frozen bundle.
        from wade.services.session_composition_service import SessionCompositionError

        try:
            bootstrap_worktree(
                worktree_path,
                config,
                repo_root,
                session_phase=SessionPhase.IMPLEMENT,
                session_kind=SessionKind.IMPLEMENTATION,
                task_id=task.id,
                work_skills=work_skills,
                review_skills=review_skills,
                refresh_skills=refresh_skills,
                compose_session_only=True,
            )
        except SessionCompositionError as exc:
            console.error(f"Cannot start implementation session: {exc}")
            return ImplementResult(success=False)

        if retarget_requested and existing_pr is not None and resolved_base is not None:
            # Explicit override differs from the PR's base — retarget so the worktree,
            # PR, and merge target stay consistent. This intentionally runs only after
            # skill preflight, so an invalid active ref cannot mutate the remote branch
            # or PR.
            console.step(
                f"Retargeting PR #{existing_pr.number} base "
                f"{current_pr_base or '(unknown)'} -> {resolved_base}..."
            )
            # Editing the PR base alone leaves the head branch rooted on the old
            # base, so that base's commits would merge into the new one. A
            # scaffold-only branch is re-rooted below; but a branch with in-flight
            # work cannot be rewritten without discarding it, so guard that case
            # (confirm/abort) rather than silently polluting the PR — mirrors the
            # plan flow's _base_retarget_is_safe (#376 review).
            #
            # `_branch_has_real_work(None)` returns False (skip the confirmation) when
            # the base is unknown; that stays safe only because reroot's `if not
            # old_base` is the *authoritative* guard for an unknown base and aborts
            # below. Keep the two in step if either changes (#376 review).
            if _branch_has_real_work(repo_root, branch_name, current_pr_base):
                console.error(
                    f"PR #{existing_pr.number}'s branch already has in-flight work, so "
                    f"retargeting its base to '{resolved_base}' cannot rewrite history — "
                    f"'{current_pr_base}'s commits would then merge into '{resolved_base}'."
                )
                if not (
                    prompts.is_tty()
                    and not yolo
                    and prompts.confirm(
                        f"Retarget PR #{existing_pr.number} base to '{resolved_base}' anyway?",
                        default=False,
                    )
                ):
                    console.info(
                        f"Left PR #{existing_pr.number} targeting '{current_pr_base}'. "
                        "Merge or finish the in-flight work first, then retarget."
                    )
                    return ImplementResult(success=False)
            # Re-root a scaffold-only branch on the new base first (#376 review).
            # The reroot force-pushes the rewritten head *before* the base edit, so
            # capture the pre-reroot head: a failed edit would otherwise leave the
            # remote branch (new base) and the PR (old base) divergent. Roll the head
            # back to keep them consistent — mirroring bootstrap_draft_pr (#376 review).
            head_ref = git_branch.resolve_start_point(repo_root, branch_name)
            pre_reroot_sha = _resolve_head_sha(repo_root, head_ref) if head_ref else None
            if not reroot_scaffold_branch_for_retarget(
                repo_root, branch_name, current_pr_base, resolved_base, task.id
            ):
                return ImplementResult(success=False)
            if not git_pr.update_pr_base(repo_root, existing_pr.number, resolved_base):
                console.error(f"Failed to retarget PR #{existing_pr.number} to {resolved_base}.")
                # Only roll back when the reroot actually rewrote the head (SHA
                # changed). A real-work branch is left untouched by the reroot, so a
                # restore would be a needless hard reset that could discard uncommitted
                # work (#376 review).
                post_reroot_sha = _resolve_head_sha(repo_root, branch_name)
                if pre_reroot_sha and post_reroot_sha and pre_reroot_sha != post_reroot_sha:
                    _restore_scaffold_head(
                        repo_root, branch_name, pre_reroot_sha, existing_pr.number
                    )
                return ImplementResult(success=False)

        # Bootstrap the draft PR only after the session skills are known-good.
        if proceed_needs_bootstrap:
            console.step("Bootstrapping draft PR...")
            pr_info = bootstrap_draft_pr(
                issue_number=task.id,
                issue_title=task.title,
                plan_body=task.body or f"Implements #{task.id}: {task.title}",
                config=config,
                repo_root=repo_root,
                base_branch=base_branch,
            )
            if pr_info:
                console.success(f"Draft PR #{pr_info.get('number')}: {pr_info.get('url')}")
            else:
                console.warn("Could not create draft PR — proceeding anyway")

        console.empty()

        # Bootstrap
        from wade.skills.installer import support_skills_for_session

        write_plan_md(worktree_path, task, plan_content=plan_content)
        bootstrap_worktree(
            worktree_path,
            config,
            repo_root,
            skills=support_skills_for_session(SessionKind.IMPLEMENTATION),
            selected_ai_tool=resolved_tool,
            sandbox=resolved_sandbox,
            session_phase=SessionPhase.IMPLEMENT,
            session_kind=SessionKind.IMPLEMENTATION,
            task_id=task.id,
            # Bindings were resolved and frozen by the preflight above. Supplying
            # overrides again would correctly look like an illegal resume-time
            # override to the composition service.
            work_skills=None,
            review_skills=None,
            # The preflight above already applied an explicit refresh and froze the
            # result. Re-resolving after a PR mutation would break the fail-fast
            # guarantee and could observe a different main-checkout inventory.
            refresh_skills=False,
        )

        # Persist the resolved base so catchup/sync/done merge into the correct
        # branch — written whenever the effective base differs from main, not only
        # when --base was passed (e.g. a base inherited from the draft PR). This is
        # the core fix for the wrong-merge-target gap (#376).
        wade_dir = worktree_path / ".wade"
        base_file = wade_dir / "base_branch"
        if effective_base != main_branch:
            wade_dir.mkdir(exist_ok=True)
            base_file.write_text(effective_base + "\n")
            console.detail(f"Base branch: {effective_base}")
        elif base_file.exists():
            # The effective base resolved back to main (e.g. `--base main` retargeted
            # a reused worktree's PR that was previously pinned to a non-main base).
            # Leaving the old pin in place would make catchup/sync/done keep merging
            # into the stale base and defeat the override — delete it (#376).
            base_file.unlink()
            console.detail(f"Base branch reset to {main_branch}")

        # Catchup: sync worktree with base branch before AI launch (non-blocking).
        # Whatever the outcome, compute commits-behind and surface a stale base LOUDLY
        # and in-session (marker + prompt banner) so no session ever proceeds silently
        # on an outdated base — regardless of *why* catchup did not advance (#407).
        stale_warning = _catchup_and_surface_staleness(
            repo_root=repo_root,
            worktree_path=worktree_path,
            branch_name=branch_name,
            effective_base=effective_base,
        )

        # Add in-progress label and move to in-progress on project board (both non-critical)
        with contextlib.suppress(Exception):
            add_in_progress_label(provider, task.id)
        with contextlib.suppress(Exception):
            provider.move_to_in_progress(task.id)

        # Build implementation prompt (skipped when resuming a session)
        prompt: str | None = None
        if not resume_session_id:
            prompt = build_implementation_prompt(
                task,
                resolved_tool,
                has_plan=bool(plan_content),
                stale_warning=stale_warning,
            )
            snippet = "\n".join(prompt.splitlines()[:5]) + "\n…"
            console.panel(snippet, title="Implementation Prompt (preview)")
        else:
            console.info(f"Resuming session: {resume_session_id[:40]}…")

        # cd_only mode: just print the worktree path and return (no title, no AI)
        if cd_only:
            print(str(worktree_path))
            return ImplementResult(success=True)

        # AI-initiated start guard: if we're inside an AI CLI session, don't
        # launch another AI tool — just print the worktree path. An accepted plan
        # handoff out of a *sandboxed* parent into an *unrestricted*
        # implementation is stricter: an inner process cannot prove a terminal
        # it opens escaped the parent's OS sandbox, so it must fail closed and
        # tell the user to relaunch from a real host terminal.
        #
        # This is the restated network-pin predicate (#478) generalised from
        # Codex to any *known* sandboxed parent (#480). The tool-identity
        # conjuncts it used to carry — parent is Codex, target is Codex — were
        # proxies for "this process is confined", and they under-fire on exactly
        # the cases that matter: a sandboxed Codex handing off to Claude inherits
        # the sandbox just as surely, and used to get the silent nested-launch
        # guard instead of the host-terminal remediation it needs. The profile
        # mismatch is now stated directly, once, in ``requires_unsandboxed_relaunch``.
        #
        # The accepted handoff runs in the original ``wade plan`` process *after*
        # the planner child has exited. Its requested profile therefore describes
        # a former child, not the runtime enclosing this implementation command;
        # it cannot establish that this process inherited a boundary. Assess only
        # the enclosing runtime's actual signal.
        detected_env = _detect_ai_cli_env()
        parent = parent_runtime(detected_env)
        profile_mismatch = requires_unsandboxed_relaunch(
            resolved_sandbox=resolved_sandbox,
            parent=parent,
        )
        # The published sandbox signal is the boundary evidence. Its tool
        # identity may have been stripped from the handoff environment, but a
        # trusted signal still proves that an inline child cannot become
        # unrestricted. A plan handoff must therefore fail closed on the
        # profile mismatch without requiring an identity marker.
        requires_fresh_runtime = plan_handoff and profile_mismatch
        if requires_fresh_runtime:
            relaunch_command = build_relaunch_command(
                ["wade", "implement", task.id, "--base", effective_base],
                ai_tool=resolved_tool,
                model=resolved_model,
                effort=resolved_effort.value if isinstance(resolved_effort, EffortLevel) else None,
                permission_mode=resolved_permission_mode,
            )
            logger.warning(
                "implementation.unrestricted_handoff_requires_host_terminal",
                parent=parent.env_var,
                signal=parent.signal,
            )
            console.error(
                f"{parent.label} is sandboxed, and wade cannot verify that a terminal "
                "opened from inside it runs unrestricted."
            )
            console.hint(INHERITED_SANDBOX_HINT)
            console.detail(relaunch_command, markup=False)
            return ImplementResult(success=False)
        # Identity controls the nested-launch guard, but a published sandbox
        # signal is sufficient diagnostic evidence on its own. Warn before an
        # actual launch even if the runtime's identity marker was stripped.
        if profile_mismatch:
            relaunch_command = build_relaunch_command(
                ["wade", "implement", task.id, "--base", effective_base],
                ai_tool=resolved_tool,
                model=resolved_model,
                effort=resolved_effort.value if isinstance(resolved_effort, EffortLevel) else None,
                permission_mode=resolved_permission_mode,
                skills=work_skills,
                review_skills=review_skills,
            )
            announce_inherited_sandbox(
                parent,
                resolved_sandbox=resolved_sandbox,
                operation="the implementation session",
                relaunch_command=relaunch_command,
            )
        if detected_env:
            logger.info(
                "implementation.ai_launch_skipped",
                reason="inside_ai_cli",
                env_var=detected_env,
            )
            console.info(
                f"Skipping AI launch: already inside AI session (detected via {detected_env})."
            )
            # Nothing is launched here, so there is no failure to diagnose — but
            # the requested profile is still undeliverable, and staying silent is
            # what let a user believe this worktree had host access it cannot
            # have. A published sandbox signal earns the claim even when its
            # runtime identity is unavailable; an unknown assessment says nothing.
            console.detail(f"Worktree ready at: {worktree_path}")
            print(str(worktree_path))
            return ImplementResult(success=True)

        # A runtime is definitely starting now, so the sandbox profile has to be
        # deliverable. Deferred to here (rather than to resolution) so the two
        # non-launching exits above never fail on a capability they don't use.
        if resolved_tool:
            try:
                enforce_sandbox_capability(resolved_tool, resolved_sandbox)
            except SandboxCapabilityError as e:
                console.error(str(e))
                return ImplementResult(success=False)

        # Set terminal title
        work_title = compose_implement_title(task.id, task.title)
        set_terminal_title(work_title)
        start_title_keeper(work_title)

        # Set up transcript capture
        transcript_path: Path | None = None
        try:
            transcript_dir = tempfile.mkdtemp(prefix="wade-implement-")
            transcript_path = Path(transcript_dir) / f"transcript-{task.id}.log"
            console.hint(f"Transcript: {transcript_path}")
        except OSError:
            logger.warning("implementation.transcript_dir_failed")

        # Detach mode: launch AI tool in a new terminal, don't block. This is a
        # convenience mode only; it is not evidence that a child escaped an OS
        # sandbox (that case returned above with a host-terminal command).
        if detach and resolved_tool:
            cmd: list[str] | None = None
            try:
                detach_adapter = AbstractAITool.get(AIToolID(resolved_tool))
                if resume_session_id:
                    # Re-resolve the sandbox context (profile, worktree
                    # writable roots, network pin) fresh at resume — it is a
                    # launch-time OS concern, not persisted session state.
                    cmd = detach_adapter.build_resume_command(
                        resume_session_id,
                        working_dir=worktree_path,
                        network_access=LAUNCH_NETWORK_ACCESS,
                        sandbox=resolved_sandbox,
                    )
                    if cmd is None:
                        console.warn(
                            f"{resolved_tool} does not support resume — starting new session"
                        )
                        resume_session_id = None  # fall back to new session
                    else:
                        # build_resume_command() omits the autonomy flags a fresh
                        # launch applies, so append them — otherwise a resumed session
                        # ignores the resolved permission mode and runs at the tool's
                        # default tier (see _resume_autonomy_args).
                        cmd += _resume_autonomy_args(detach_adapter, resolved_permission_mode)
                if not resume_session_id:
                    if prompt:
                        deliver_prompt_if_needed(detach_adapter, prompt)
                    cmd = detach_adapter.build_launch_command(
                        model=resolved_model,
                        trusted_dirs=[str(worktree_path), tempfile.gettempdir()],
                        initial_message=prompt,
                        effort=resolved_effort,
                        allowed_commands=config.permissions.allowed_commands,
                        working_dir=worktree_path,
                        network_access=LAUNCH_NETWORK_ACCESS,
                        sandbox=resolved_sandbox,
                        **permission_mode_launch_kwargs(resolved_permission_mode),
                    )
            except (ValueError, KeyError):
                cmd = [resolved_tool]

            if cmd is not None:
                console.step(f"Launching {resolved_tool} in new terminal...")
                if launch_in_new_terminal(
                    cmd,
                    cwd=str(worktree_path),
                    title=work_title,
                ):
                    console.success(f"Detached AI session for #{task.id}")
                    stop_title_keeper()
                    return ImplementResult(success=True)
            console.warn("Could not launch in new terminal — falling back to inline")
            detach = False
            # Fall through to inline launch below

        # Launch AI tool (inline)
        merge_status = MergeStatus.NOT_MERGED
        if not detach and resolved_tool:
            resume_label = " (resuming)" if resume_session_id else ""
            console.step(f"Launching {resolved_tool}{resume_label}...")

            adapter: AbstractAITool | None = None
            launch_completed = False
            detected_model: str | None = None
            try:
                adapter = AbstractAITool.get(AIToolID(resolved_tool))

                # Resume path: use build_resume_command() instead of launch()
                resume_cmd: list[str] | None = None
                if resume_session_id:
                    from wade.utils.process import run_with_transcript

                    # Re-resolve the sandbox context fresh at resume (launch-time
                    # OS concern, not persisted session state).
                    resume_cmd = adapter.build_resume_command(
                        resume_session_id,
                        working_dir=worktree_path,
                        network_access=LAUNCH_NETWORK_ACCESS,
                        sandbox=resolved_sandbox,
                    )
                    if resume_cmd is None:
                        console.warn(
                            f"{resolved_tool} does not support resume — starting new session"
                        )
                        resume_session_id = None  # fall back below
                    else:
                        # Match fresh launch: build_resume_command() drops the autonomy
                        # flags, so without this a resumed session would ignore the
                        # resolved permission mode and run at the default tier.
                        resume_cmd += _resume_autonomy_args(adapter, resolved_permission_mode)

                if resume_session_id and resume_cmd is not None:
                    logger.info(
                        "ai_tool.resume",
                        tool=str(adapter.TOOL_ID),
                        session_id=resume_session_id,
                        cwd=str(worktree_path),
                    )
                    exit_code = run_with_transcript(
                        resume_cmd,
                        transcript_path,
                        cwd=worktree_path,
                    )
                else:
                    if prompt:
                        deliver_prompt_if_needed(adapter, prompt)
                    exit_code = adapter.launch(
                        working_dir=worktree_path,
                        model=resolved_model,
                        prompt=prompt,
                        transcript_path=transcript_path,
                        trusted_dirs=[str(worktree_path), tempfile.gettempdir()],
                        effort=resolved_effort,
                        allowed_commands=config.permissions.allowed_commands,
                        network_access=LAUNCH_NETWORK_ACCESS,
                        sandbox=resolved_sandbox,
                        **permission_mode_launch_kwargs(resolved_permission_mode),
                    )

                launch_completed = True
                logger.info("implementation.ai_exited", exit_code=exit_code, tool=resolved_tool)

                # Non-blocking tools (VS Code, Antigravity) return immediately.
                # Wait for the user to confirm they're done before post-session steps.
                if not adapter.capabilities().blocks_until_exit:
                    console.empty()
                    if not prompts.confirm("Have you finished the session?", default=True):
                        console.info(
                            "Worktree preserved — run"
                            " 'wade implementation-session done' when ready."
                        )
                        launch_completed = False
            except (ValueError, KeyError):
                console.warn(f"Unknown AI tool: {resolved_tool}")
                merge_status = MergeStatus.MERGE_FAILED
            except Exception as e:
                console.warn(f"AI tool launch failed: {e}")
                merge_status = MergeStatus.MERGE_FAILED
            finally:
                stop_title_keeper()

                # Capture token usage BEFORE lifecycle (merge/cleanup) to ensure
                # the PR is still open and the branch still exists.
                # Skip for non-blocking tools — they don't produce transcripts.
                if (
                    adapter is not None
                    and launch_completed
                    and adapter.capabilities().blocks_until_exit
                ):
                    detected_model = _capture_post_session_usage(
                        transcript_path=transcript_path,
                        adapter=adapter,
                        repo_root=repo_root,
                        branch=branch_name,
                        ai_tool=resolved_tool,
                        model=resolved_model,
                        issue_number=task.id,
                        provider=provider,
                    )

                if launch_completed:
                    effective_model = resolved_model or detected_model
                    try:
                        merge_status = _post_implementation_lifecycle(
                            repo_root=repo_root,
                            branch=branch_name,
                            issue_number=task.id,
                            worktree_path=worktree_path,
                            provider=provider,
                            ai_tool=resolved_tool,
                            model=effective_model,
                            detach=detach,
                            ai_explicit=ai_explicit,
                            model_explicit=model_explicit,
                            permission_mode=resolved_permission_mode.value,
                            permission_mode_explicit=(
                                permission_mode_explicit
                                or permission_mode is not None
                                or yolo is not None
                            ),
                            sandbox=sandbox,
                        )
                    except Exception:
                        logger.exception("post_implementation_lifecycle.failed")
                        merge_status = MergeStatus.MERGE_FAILED

            # Use CLI-resolved model, falling back to transcript-detected model.
            effective_model = resolved_model or detected_model
            try:
                add_implemented_by_labels(provider, task.id, resolved_tool, effective_model)
            except Exception as e:
                console.warn(f"Could not apply implemented-by labels: {e}")
                logger.warning("implementation.implemented_by_labels_failed", error=str(e))
        elif not resolved_tool:
            console.info("No AI tool configured. Worktree ready for manual work.")
            console.detail(f"cd {worktree_path}")
            stop_title_keeper()

        lines = []
        lines.append(f"  Worktree   {console.git_ref(branch_name)}")
        lines.append(f"  Issue      {console.issue_ref(task.id, task.title)}")
        console.panel("\n".join(lines), title="Implementation session complete")

        return ImplementResult(
            success=merge_status != MergeStatus.MERGE_FAILED,
            merged=merge_status == MergeStatus.MERGED,
            branch_name=branch_name,
        )
    finally:
        console.out = _original_out


def _resolve_task_target(
    target: str,
    provider: AbstractTaskProvider,
    config: ProjectConfig,
) -> Task | None:
    """Resolve a target (issue number or plan file) to a Task.

    If the target is a path to a plan file, create the issue first.
    """
    # Check if target is a file path
    target_path = Path(target).expanduser()
    if target_path.is_file():
        from wade.services.task_service import create_from_plan_file
        from wade.utils.conventional import ConventionalTitleError

        console.info(f"Creating issue from plan file: {target}")
        try:
            task = create_from_plan_file(target_path, config=config, provider=provider)
        except ConventionalTitleError as e:
            # Title comes from the plan file — disable Rich markup so bracket
            # tokens in it are shown literally rather than parsed as markup.
            console.error(str(e), markup=False)
            console.hint(
                f"Fix the plan file's `# Title` heading in {target} to a "
                "conventional-commit title, then re-run."
            )
            return None
        return task

    # Treat as issue number — strip leading "#" so "#123" and "123" both work
    issue_id = target.lstrip("#")
    try:
        task = provider.read_task(issue_id)
        return task
    except Exception as e:
        console.error(f"Could not read issue #{issue_id}: {e}")
        return None


def _resolve_worktree_from_plan(
    plan_file: Path,
    project_root: Path | None = None,
) -> tuple[Path, str, str | None]:
    if not plan_file.is_file():
        raise ValueError(f"Plan file '{plan_file}' not found.")

    first_line = plan_file.read_text(encoding="utf-8").split("\n", 1)[0].strip()
    match = re.match(r"^#\s+(.+)", first_line)
    if not match:
        raise ValueError(
            "Plan file must start with a '# Title' heading to derive the worktree name."
        )
    title = match.group(1).strip()

    from wade.utils.slug import slugify

    slug = slugify(title, max_length=50)

    wt_path = find_worktree_path(slug, project_root=project_root)
    if not wt_path:
        raise ValueError(
            f"No worktree found matching plan title '{title}' (slug: '{slug}'). "
            "Check active worktrees with: wade worktree list"
        )

    branch = git_repo.get_current_branch(wt_path)
    issue_number = extract_issue_from_branch(branch)

    return wt_path, branch, issue_number
