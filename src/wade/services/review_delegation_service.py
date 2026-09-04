"""Review delegation service — plan review and implementation review via delegation."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

import structlog
from crossby.models.ai import EffortLevel
from rich.markup import escape

from wade.config.loader import load_config
from wade.git import repo as git_repo
from wade.git.repo import GitError
from wade.models.config import DEFAULT_SANDBOX, AICommandConfig, ProjectConfig
from wade.models.delegation import DelegationMode, DelegationRequest, DelegationResult
from wade.models.permission import PermissionMode
from wade.models.session_manifest import ReviewOutcome
from wade.models.workflow import DelegationKind
from wade.services.ai_resolution import (
    SandboxCapabilityError,
    build_relaunch_command,
    confirm_ai_selection,
    enforce_sandbox_capability,
    resolve_ai_tool,
    resolve_effort,
    resolve_model,
    resolve_permission_mode,
    resolve_sandbox,
)
from wade.services.delegation_service import (
    delegate,
    effective_timeout,
    extended_timeout,
    resolve_mode,
)
from wade.services.review_record_service import (
    count_binding_passes,
    read_review_record,
    write_review_record,
)
from wade.services.skill_invocation_service import (
    PreparedDelegationMethod,
    SkillInvocationError,
    cleanup_delegation_bundle,
    compose_delegation_prompt,
    prepare_delegation_method,
)
from wade.skills.installer import load_prompt_template
from wade.ui.console import console
from wade.utils.runtime_env import (
    INHERITED_SANDBOX_HINT,
    detect_parent_runtime,
    has_explicit_sandbox_denial,
    looks_like_sandbox_denial,
    possible_inherited_sandbox_cause,
)

logger = structlog.get_logger()


def _announce_review_pass_budget(passes: int, limit: int) -> None:
    """Surface the implementation-session review-pass budget from the command (#384).

    The ``done`` cap stops requiring re-review of new commits after
    ``done.max_review_passes`` passes. Printing the running count here means an
    agent sees its remaining budget directly from ``wade review implementation``
    rather than from buried skill/prompt prose. Informational only — the cap is
    enforced (and scoped to implementation sessions) at ``done`` time.
    """
    remaining = max(0, limit - passes)
    if remaining > 0:
        plural = "" if remaining == 1 else "es"
        console.info(
            f"Review pass {passes} of {limit} recorded — {remaining} pass{plural} left "
            "before `done` stops requiring re-review of new commits in an "
            "implementation session. Configure the cap with `done.max_review_passes`."
        )
    else:
        console.warn(
            f"Review pass {passes} of {limit} recorded — the implementation-session "
            "review-pass cap is now reached. `done` will complete without requiring "
            "re-review of further commits. Configure with `done.max_review_passes`."
        )


def _load_review_config(
    command: str,
    project_root: Path | None = None,
) -> tuple[ProjectConfig, AICommandConfig]:
    """Load project config and extract the per-command review configuration."""
    config = load_config(project_root)
    cmd_config: AICommandConfig = getattr(config.ai, command)
    return config, cmd_config


def _check_review_enabled(command: str, cmd_config: AICommandConfig) -> DelegationResult | None:
    """Return a skip result if the review command is disabled, else None."""
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


def _review_budget_line(mode: DelegationMode, timeout: int) -> str:
    """Deadline wording substituted for ``{review_budget}`` in a review prompt (#450).

    Only ``HEADLESS`` kills the subprocess at ``timeout`` seconds
    (``_run_headless_once``) — the reviewer needs that number to prioritize and
    wrap up before being cut off. ``INTERACTIVE``/``PROMPT`` reviews have no
    subprocess kill, so stating a deadline there would fabricate one and could
    cut a review short for no reason.
    """
    if mode == DelegationMode.HEADLESS:
        return (
            f"You have roughly **{timeout}s** to complete this review before it "
            "is stopped; prioritize the most important findings and return "
            "before then. Partial output is still used, so lead with the "
            "highest-severity issues."
        )
    return "No hard deadline — take the time you need."


# Per-operation remediation for the shared sandbox check in ``delegate()``. The
# dispatcher is one function for every delegated operation, so it cannot infer
# which command the user should re-run — these supply it. An unmapped command
# yields ``None`` rather than a guessed command line: a wrong command to type is
# worse than no command, and the finding itself is still reported.
_OPERATION_LABELS = {
    "review_plan": "the plan review",
    "review_implementation": "the implementation review",
    "review_batch": "the batch review",
}
_RELAUNCH_COMMANDS = {
    "review_plan": "wade review plan",
    "review_implementation": "wade review implementation",
    "review_batch": "wade review batch",
}
#: Commands whose CLI signature takes a **required** positional operand — the
#: plan path for ``wade review plan``, the tracking issue for ``wade review
#: batch`` (see ``cli/review.py``). The base command alone is rejected by Typer,
#: so it is never offered as remediation: the caller must supply the operand that
#: re-runs *this* operation, or the hint is withheld under the same rule as an
#: unmapped command (#481 review).
_RELAUNCH_OPERANDS_REQUIRED = frozenset({"review_plan", "review_batch"})


def _relaunch_command(
    command: str,
    operand: str | None,
    *,
    ai_tool: str | None = None,
    model: str | None = None,
    mode: DelegationMode | None = None,
    effort: str | None = None,
    permission_mode: PermissionMode | None = None,
    skills: list[str] | None = None,
    staged: bool = False,
) -> str | None:
    """Build the exact host-terminal retry for this resolved review launch.

    A retry hint must repeat the launch that failed, not merely name its review
    operation: omitting ``--mode headless`` can turn a noninteractive operation
    into a prompt, and omitting an explicit tool/model/skill can select a
    different reviewer and binding. ``--no-sandbox`` intentionally supersedes
    the original profile; every other value is the resolved value used here.
    """
    base = _RELAUNCH_COMMANDS.get(command)
    if base is None:
        return None
    if command in _RELAUNCH_OPERANDS_REQUIRED and not operand:
        return None

    return build_relaunch_command(
        shlex.split(base),
        operands=[operand] if operand else None,
        ai_tool=ai_tool,
        model=model,
        mode=mode,
        effort=effort,
        permission_mode=permission_mode,
        skills=skills,
        staged=staged,
    )


def _run_review_delegation(
    template: str,
    command: str,
    *,
    content_placeholder: str = "",
    content: str = "",
    config: ProjectConfig | None = None,
    cmd_config: AICommandConfig | None = None,
    ai_tool: str | None = None,
    model: str | None = None,
    mode: str | None = None,
    effort: str | None = None,
    ai_explicit: bool = False,
    model_explicit: bool = False,
    effort_explicit: bool = False,
    permission_mode: str | None = None,
    yolo: bool | None = None,
    permission_mode_explicit: bool = False,
    sandbox: bool | None = None,
    delegation_kind: DelegationKind | None = None,
    method_section: str = "",
    input_label: str = "Operation input",
    cwd: Path | None = None,
    trusted_dirs: list[str] | None = None,
    relaunch_operand: str | None = None,
    relaunch_skills: list[str] | None = None,
    relaunch_staged: bool = False,
) -> DelegationResult:
    """Shared pipeline: config load → mode resolve → AI resolve → confirm → delegate → display.

    ``template`` is substituted here, not by the caller: ``{review_budget}`` is
    replaced **in the bare template**, once ``delegation_mode`` and the
    per-attempt ``request.timeout`` are known (#450) — a headless review gets a
    concrete deadline naming that timeout, everything else gets "no hard
    deadline" wording. ``content_placeholder`` (e.g. ``{diff_content}``) is
    replaced with ``content`` **last**, after that. Order matters: ``content``
    is untrusted, caller-supplied text (a diff, a plan file, batch context) that
    may itself contain the literal substring ``{review_budget}`` — reviewing a
    diff that touches this very prompt template is exactly that case. Replacing
    ``{review_budget}`` first, before ``content`` is ever merged in, means that
    later find-and-replace can never accidentally rewrite a matching literal
    sitting inside the reviewed content.
    """
    if config is None or cmd_config is None:
        config, cmd_config = _load_review_config(command)

    def _merge_content(base: str, budget_line: str | None = None) -> str:
        if delegation_kind is not None:
            return compose_delegation_prompt(
                delegation_kind,
                contract=base,
                method_section=method_section,
                input_label=input_label,
                input_content=content,
                budget_line=budget_line,
            )
        trusted = base.replace(
            "{review_budget}", budget_line or "No hard deadline — take the time you need."
        )
        return trusted.replace(content_placeholder, content) if content_placeholder else trusted

    try:
        default_mode = (
            DelegationMode.INTERACTIVE if command == "review_batch" else DelegationMode.PROMPT
        )
        delegation_mode = (
            DelegationMode(mode) if mode else resolve_mode(cmd_config, default=default_mode)
        )
    except ValueError:
        console.error(f"Invalid delegation mode: {mode}")
        return DelegationResult(
            success=False,
            feedback=f"Invalid delegation mode: {mode}",
            mode=DelegationMode.PROMPT,
            exit_code=1,
        )

    resolved_tool: str | None = None
    resolved_model: str | None = None
    resolved_effort: EffortLevel | None = None
    effective_permission_mode = PermissionMode.DEFAULT
    # Prompt mode never launches a runtime, so the profile stays at its default
    # and is only meaningful on the branch below.
    resolved_sandbox = DEFAULT_SANDBOX

    if delegation_mode != DelegationMode.PROMPT:
        resolved_tool = resolve_ai_tool(ai_tool, config, command=command)
        resolved_model = resolve_model(model, config, command=command, tool=resolved_tool)
        resolved_effort = resolve_effort(effort, config, command=command, tool=resolved_tool)
        resolved_permission_mode = resolve_permission_mode(
            permission_mode, yolo, config, command=command
        )
        # The capability check waits until after the confirmation UI below,
        # which can still change the tool.
        resolved_sandbox = resolve_sandbox(sandbox, config, command)

        # Effective mode enforces the read-only headless *safety* rule
        # (delegation_service.py:126 forces DEFAULT for headless launches) — this
        # is NOT confirm_ai_selection's DelegationMode.HEADLESS display guard,
        # which is an orthogonal UI concern. Forcing DEFAULT here for the display
        # value keeps what is shown equal to what is applied (no yolo is ever sent
        # to a headless review).
        display_permission_mode = (
            PermissionMode.DEFAULT
            if delegation_mode == DelegationMode.HEADLESS
            else resolved_permission_mode
        )

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
                sandbox=resolved_sandbox,
            )
        )

        # Checked against the *confirmed* tool: the menu above may have switched
        # to a runtime that cannot honor the requested profile.
        if resolved_tool:
            try:
                enforce_sandbox_capability(resolved_tool, resolved_sandbox)
            except SandboxCapabilityError as e:
                console.error(str(e))
                return DelegationResult(
                    success=False,
                    feedback=str(e),
                    mode=delegation_mode,
                    exit_code=1,
                    never_launched=True,
                )

        # Re-apply the headless safety rule after confirm: interactive changes are
        # honored, but a headless launch always stays DEFAULT regardless.
        effective_permission_mode = (
            PermissionMode.DEFAULT
            if delegation_mode == DelegationMode.HEADLESS
            else confirmed_permission_mode
        )

    effort_str = resolved_effort.value if isinstance(resolved_effort, EffortLevel) else None

    # An explicit ``ai.<command>.timeout`` is honored verbatim: it bypasses
    # scaling and — since it is the escape hatch for a hard tool-timeout — the
    # retry too (see ``_delegate_headless``).
    explicit_timeout = cmd_config.timeout is not None
    # Size the budget off the real payload (content merged in), matching
    # pre-#450 behavior — but only for *sizing*; the {review_budget} token is
    # still raw here, so this throwaway string is never sent anywhere.
    timeout = effective_timeout(
        _merge_content(template, "{review_budget}"), cmd_config.timeout, effort_str
    )
    # Substitute the reviewer's own deadline now that mode + the per-attempt
    # budget are both known. This is the *first-attempt* budget, never the
    # worst-case retry sum: the headless prompt is built once and reused across
    # both attempts (``_delegate_headless``), so stating the worst case here
    # would tell the reviewer it has more time than the attempt that is
    # actually running it will allow — reproducing the silent-kill bug this
    # placeholder exists to fix. Understating on a retry fails safe (wraps up
    # early) rather than unsafe (runs long, gets killed).
    budget_line = _review_budget_line(delegation_mode, timeout)
    prompt = _merge_content(template, budget_line)
    # Prompt mode never launches a child, so it cannot need sandbox recovery;
    # retain its compact command shape for callers/tests that inspect requests.
    relaunch_command = _relaunch_command(command, relaunch_operand)
    if delegation_mode is not DelegationMode.PROMPT:
        relaunch_command = _relaunch_command(
            command,
            relaunch_operand,
            ai_tool=resolved_tool,
            model=resolved_model,
            mode=delegation_mode,
            effort=effort_str,
            permission_mode=effective_permission_mode,
            skills=relaunch_skills,
            staged=relaunch_staged,
        )
    request = DelegationRequest(
        mode=delegation_mode,
        prompt=prompt,
        ai_tool=resolved_tool,
        model=resolved_model,
        effort=effort_str,
        cwd=cwd,
        trusted_dirs=trusted_dirs or [],
        permission_mode=effective_permission_mode,
        sandbox=resolved_sandbox,
        timeout=timeout,
        explicit_timeout=explicit_timeout,
        operation=_OPERATION_LABELS.get(command),
        relaunch_command=relaunch_command,
    )

    if delegation_mode == DelegationMode.HEADLESS:
        # This spawns an external AI subprocess bounded by ``request.timeout``.
        # Announce a budget the orchestrator driving wade (Claude Code, Cursor,
        # Copilot, …) must wait out — otherwise it kills the call at its own
        # shorter timeout. For a scaled budget wade retries once on timeout, so
        # announce the *worst-case total* (budget + retry); an explicit budget is
        # verbatim with no retry, so announce it as-is.
        if explicit_timeout:
            console.info(
                "Launching headless AI review — this runs an external AI "
                f"subprocess bounded by your configured ai.<command>.timeout of "
                f"{request.timeout}s (no retry). Keep it in the foreground and "
                f"allow more than {request.timeout}s before timing out. Do not "
                "move it to the background."
            )
        else:
            worst_case = request.timeout + extended_timeout(request.timeout)
            console.info(
                "Launching headless AI review — this runs an external AI "
                f"subprocess. wade budgets {request.timeout}s and, on timeout, "
                f"retries once with a longer budget (worst-case total "
                f"{worst_case}s). Keep it in the foreground and allow more than "
                f"{worst_case}s before timing out (raise your shell/tool timeout "
                "if needed). Do not move it to the background."
            )
    elif delegation_mode == DelegationMode.INTERACTIVE:
        console.info(
            "Launching external AI review session — "
            "please wait, do not move this to the background."
        )

    result = delegate(request)
    if result.success:
        # Delegation feedback is untrusted, model-authored text/markdown that
        # routinely quotes source code (e.g. `console.print("[x]done[/]")`).
        # Rich would parse `[/]` as a closing tag with nothing to close and
        # raise MarkupError, so print it literally with markup disabled (#394).
        console.out.print(result.feedback, markup=False)
    elif result.timed_out:
        # A timed-out review may still carry real partial content — surface it as
        # a warning + plain text, never as a hard error (#366). Same markup=False
        # treatment as the success path since the output is untrusted.
        if result.feedback:
            console.warn(
                "Headless review timed out before finishing — the output below is "
                "partial and may be incomplete:"
            )
            console.out.print(result.feedback, markup=False)
        else:
            console.warn("Headless review timed out before producing any output.")
    else:
        # `console.error` interpolates the message into its own `[error]…[/]`
        # wrapper, so `markup=False` can't apply here — escape the feedback so
        # bracketed tokens render literally while the wrapper still styles (#394).
        console.error(escape(result.feedback))
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
    permission_mode: str | None = None,
    yolo: bool | None = None,
    permission_mode_explicit: bool = False,
    sandbox: bool | None = None,
    skills: list[str] | None = None,
) -> DelegationResult:
    """Review a plan file via the delegation infrastructure."""
    config, cmd_config = _load_review_config("review_plan")
    skip = _check_review_enabled("review_plan", cmd_config)
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
    # A PLAN_DIR_ONLY session is not a git worktree, but it still owns a frozen
    # session manifest beside the plan. Walk upward from the reviewed file so a
    # mapped plan-review uses that exact REVIEW binding instead of silently
    # falling back to a fresh standalone operation selected from the caller's
    # current checkout.
    caller_cwd = Path.cwd()
    binding_root = caller_cwd
    for candidate in (plan_path.parent, *plan_path.parent.parents):
        if (candidate / ".wade" / "session").exists():
            binding_root = candidate
            break
    try:
        review_cwd = git_repo.get_repo_root(binding_root)
    except GitError:
        review_cwd = caller_cwd
    template = load_prompt_template("review-plan.md")
    try:
        prepared = prepare_delegation_method(
            config,
            DelegationKind.PLAN_REVIEW,
            cwd=binding_root,
            skills=skills,
        )
        result = _run_review_delegation(
            template,
            "review_plan",
            content=plan_content,
            config=config,
            cmd_config=cmd_config,
            ai_tool=ai_tool,
            model=model,
            mode=mode,
            effort=effort,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort_explicit=effort_explicit,
            permission_mode=permission_mode,
            yolo=yolo,
            permission_mode_explicit=permission_mode_explicit,
            sandbox=sandbox,
            delegation_kind=DelegationKind.PLAN_REVIEW,
            method_section=prepared.method_section,
            input_label="Plan input",
            cwd=review_cwd,
            trusted_dirs=(
                [str(binding_root)] if binding_root.resolve() != review_cwd.resolve() else None
            ),
            # `wade review plan` takes the plan path as a required argument, so
            # the relaunch hint has to carry the file this run was reviewing.
            relaunch_operand=plan_file,
            relaunch_skills=skills,
        )
    except SkillInvocationError as exc:
        console.error(str(exc))
        return DelegationResult(
            success=False,
            feedback=str(exc),
            mode=DelegationMode.PROMPT,
            exit_code=1,
        )
    cleanup_delegation_bundle(prepared, preserve=not result.success)
    return result


def _selected_review_base(repo_root: Path, config: ProjectConfig) -> str:
    """Resolve the base selected when the implementation session was created."""

    base_file = repo_root / ".wade" / "base_branch"
    try:
        if base_file.is_file() and not base_file.is_symlink():
            stored = base_file.read_text(encoding="utf-8").strip()
            if stored:
                return stored
    except OSError:
        pass
    return config.project.main_branch or git_repo.detect_main_branch(repo_root)


def _committed_diff_fallback(
    repo_root: Path | None = None,
    config: ProjectConfig | None = None,
) -> str:
    """Return branch diff against the base branch when working tree is clean.

    Uses ``git diff <base>...HEAD`` (three-dot syntax) to show changes
    committed on the current branch since it diverged from the base branch.
    The base branch is the configured ``main_branch`` or auto-detected
    ``main``/``master``.

    Returns empty string if on the base branch, if the repo root cannot be
    resolved, or on any GitError (graceful degradation).
    """
    try:
        repo_root = repo_root or git_repo.get_repo_root(Path.cwd())
        current_branch = git_repo.get_current_branch(repo_root)
        config = config or load_config()
        base_branch = _selected_review_base(repo_root, config)
        if current_branch == base_branch:
            return ""
        return git_repo.diff_between(repo_root, base_branch, "HEAD")
    except GitError:
        return ""


@dataclass(frozen=True)
class _ReviewDiffs:
    committed: str
    staged: str
    unstaged: str

    @property
    def empty(self) -> bool:
        return not (self.committed or self.staged or self.unstaged)

    def review_input(self, *, staged_only: bool) -> str:
        if staged_only:
            return self.staged
        sections: list[str] = []
        for label, content in (
            ("Committed branch changes", self.committed),
            ("Staged changes", self.staged),
            ("Unstaged changes", self.unstaged),
        ):
            if content:
                sections.append(f"### {label}\n\n{content.strip()}")
        return "\n\n".join(sections)


def _collect_review_diffs(repo_root: Path, config: ProjectConfig) -> _ReviewDiffs:
    """Inspect all change sets before classifying an empty review."""

    current_branch = git_repo.get_current_branch(repo_root)
    base_branch = _selected_review_base(repo_root, config)
    committed = (
        ""
        if current_branch == base_branch
        else git_repo.diff_between_checked(repo_root, base_branch, "HEAD").strip()
    )
    return _ReviewDiffs(
        committed=committed,
        staged=git_repo.diff_worktree(repo_root, staged=True).strip(),
        unstaged=git_repo.diff_worktree(repo_root, staged=False).strip(),
    )


def _record_binding_outcome(
    repo_root: Path,
    head: str,
    prepared: PreparedDelegationMethod,
    outcome: ReviewOutcome,
) -> int | None:
    record = write_review_record(
        repo_root,
        delegation=DelegationKind.CODE_REVIEW,
        commit=head,
        binding=prepared.binding,
        outcome=outcome,
    )
    if record is None:
        if outcome is ReviewOutcome.UNATTEMPTED:
            console.warn("Could not persist the unattempted-review audit record.")
        else:
            console.warn("Review completed, but its binding-aware receipt could not be persisted.")
        return None
    return count_binding_passes(
        repo_root,
        delegation=DelegationKind.CODE_REVIEW,
        binding=prepared.binding,
    )


# Today's wording for a failed review whose cause wade cannot pin down. Kept
# verbatim and used unchanged whenever the parent assessment is ``UNKNOWN``: the
# disjunction is not sloppiness, it is the honest statement of what is known when
# the runtime publishes no sandbox signal (#462 review).
_HEDGED_REVIEW_FAILURE = (
    "Review did not complete, so no review-pass budget was consumed. "
    "Check the reviewer output above for the cause — a launch failure "
    "(missing login/PATH, sandbox denial) or a nonzero exit — fix that, "
    "then re-run `wade review implementation`."
)


def _report_failed_review(
    repo_root: Path,
    head: str,
    prepared: PreparedDelegationMethod,
    result: DelegationResult,
) -> None:
    """Record and explain a review that produced no usable outcome.

    A reviewer that never started is an *unattempted* review: it is recorded so
    the state is auditable, but the record neither satisfies the gate nor
    consumes a review→fix cycle — counting an infrastructure failure would let
    `done` skip a required review (#462), and satisfying the gate with it would
    be worse still.

    The remediation is graded by how much wade actually knows, because the value
    of the diagnosis is that it can be trusted:

    1. a known-sandboxed parent **and** an unrestricted-profile mismatch **and**
       a denial-shaped failure — state the cause and the exact relaunch command;
    2. a denial-shaped failure with no signal from the runtime — offer it as a
       *possible* cause alongside today's hedged wording;
    3. anything else — today's hedged wording alone.

    The denial shape and profile mismatch are both required in case 1. A known
    boundary says the reviewer *could* have been denied, never that it *was*;
    likewise, a compatible ``sandbox=True`` request did not ask wade to deliver
    an unrestricted runtime. Without both, a configuration refusal that never
    touched the OS (``Unknown AI tool``, ``No AI tool specified``) or a genuinely
    uninstalled binary would be blamed on inaccessible host credentials and
    would suppress the more useful generic remediation — a confident wrong
    cause, which is worse than the hedged one it replaced (#481 review).
    """
    current_record = None
    if result.never_launched:
        _record_binding_outcome(repo_root, head, prepared, ReviewOutcome.UNATTEMPTED)
        # The write may have failed or a higher-precedence receipt may have won;
        # inspect what actually remains before describing persistence or gate
        # state to the user.
        current_record = read_review_record(
            repo_root,
            delegation=DelegationKind.CODE_REVIEW,
            commit=head,
            binding=prepared.binding,
        )

    parent = detect_parent_runtime()
    denial_shaped = result.never_launched and looks_like_sandbox_denial(result.feedback)
    if (
        denial_shaped
        and has_explicit_sandbox_denial(result.feedback)
        and result.inherited_sandbox_profile_mismatch
    ):
        logger.warning(
            "review.reviewer_never_launched",
            parent=parent.env_var,
            signal=parent.signal,
        )
        # ``delegate()`` already warned that the profile was undeliverable; this
        # is the outcome, not a repeat of the advisory. Restating the command is
        # deliberate — the pre-launch line has scrolled past the reviewer's own
        # output by now.
        if current_record is not None and current_record.outcome is ReviewOutcome.UNATTEMPTED:
            receipt_state = (
                "The current review record is unattempted, so it does not satisfy the review gate."
            )
        elif current_record is None:
            receipt_state = (
                "wade could not confirm an unattempted review record, so a satisfying "
                "receipt is still required before the review gate can close."
            )
        else:
            receipt_state = (
                f"The existing {current_record.outcome.value} review record was retained; "
                "its gate state is unchanged."
            )
        console.warn(
            f"{parent.label} is sandboxed and the implementation review never started, "
            "so the reviewer could not reach its own host credentials from inside that "
            f"boundary. No review-pass budget was consumed; {receipt_state}"
        )
        console.hint(INHERITED_SANDBOX_HINT)
        console.detail(
            result.relaunch_command
            or f"{_RELAUNCH_COMMANDS['review_implementation']} --no-sandbox",
            markup=False,
        )
        return

    console.warn(_HEDGED_REVIEW_FAILURE)
    if denial_shaped:
        console.hint(possible_inherited_sandbox_cause(parent))


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
    permission_mode: str | None = None,
    yolo: bool | None = None,
    permission_mode_explicit: bool = False,
    sandbox: bool | None = None,
    skills: list[str] | None = None,
    ack_self_review: bool = False,
) -> DelegationResult:
    """Review implementation changes via the delegation infrastructure."""
    config, cmd_config = _load_review_config("review_implementation")
    skip = _check_review_enabled("review_implementation", cmd_config)
    if skip is not None:
        return skip
    if ack_self_review and (
        mode is not None
        or ai_explicit
        or model_explicit
        or effort_explicit
        or permission_mode_explicit
        or yolo is not None
        or sandbox is not None
    ):
        message = (
            "--ack-self-review cannot be combined with AI launch, mode, effort, or "
            "permission or sandbox options"
        )
        console.error(message)
        return DelegationResult(
            success=False,
            feedback=message,
            mode=DelegationMode.PROMPT,
            exit_code=1,
        )
    if mode is not None:
        try:
            DelegationMode(mode)
        except ValueError:
            message = f"Invalid delegation mode: {mode}"
            console.error(message)
            return DelegationResult(
                success=False,
                feedback=message,
                mode=DelegationMode.PROMPT,
                exit_code=1,
            )

    try:
        repo_root = git_repo.get_repo_root(Path.cwd())
        head = git_repo.rev_parse(repo_root, "HEAD")
        diffs = _collect_review_diffs(repo_root, config)
    except GitError as exc:
        # ``GitError`` already names the exact command that failed (e.g.
        # "git diff ... failed (exit N): ..." or "git rev-parse ... failed"),
        # so surface it directly rather than hard-coding "git diff failed",
        # which would mis-attribute a repo-root failure and double-prefix a
        # diff failure.
        message = f"Could not read changes to review: {exc}"
        console.error(message)
        return DelegationResult(
            success=False,
            feedback=message,
            mode=DelegationMode.PROMPT,
            exit_code=1,
        )

    try:
        prepared = prepare_delegation_method(
            config,
            DelegationKind.CODE_REVIEW,
            cwd=repo_root,
            skills=skills,
        )
    except SkillInvocationError as exc:
        console.error(str(exc))
        return DelegationResult(
            success=False,
            feedback=str(exc),
            mode=DelegationMode.PROMPT,
            exit_code=1,
        )

    if diffs.empty:
        message = "No committed, staged, or unstaged changes to review."
        console.warn(message)
        _record_binding_outcome(repo_root, head, prepared, ReviewOutcome.NO_DIFF)
        cleanup_delegation_bundle(prepared, preserve=False)
        return DelegationResult(
            success=True,
            feedback=message,
            mode=DelegationMode.PROMPT,
            skipped=True,
        )

    if staged and not diffs.staged:
        message = (
            "No staged changes to review, but committed or unstaged changes exist. "
            "Stage the intended changes or rerun without --staged."
        )
        console.warn(message)
        _record_binding_outcome(
            repo_root,
            head,
            prepared,
            ReviewOutcome.NOTHING_STAGED,
        )
        cleanup_delegation_bundle(prepared, preserve=False)
        return DelegationResult(
            success=not ack_self_review,
            feedback=message,
            mode=DelegationMode.PROMPT,
            skipped=True,
            exit_code=1 if ack_self_review else 0,
        )

    if ack_self_review:
        passes = _record_binding_outcome(
            repo_root,
            head,
            prepared,
            ReviewOutcome.REVIEWED,
        )
        cleanup_delegation_bundle(prepared, preserve=passes is None)
        if passes is None:
            return DelegationResult(
                success=False,
                feedback="Self-review acknowledgement could not be persisted.",
                mode=DelegationMode.PROMPT,
                exit_code=1,
            )
        _announce_review_pass_budget(passes, config.done.max_review_passes)
        return DelegationResult(
            success=True,
            feedback=("Self-review acknowledged for the current commit and frozen review binding."),
            mode=DelegationMode.PROMPT,
        )

    diff_content = diffs.review_input(staged_only=staged)
    template = load_prompt_template("review-code.md")
    try:
        result = _run_review_delegation(
            template,
            "review_implementation",
            content=diff_content,
            config=config,
            cmd_config=cmd_config,
            ai_tool=ai_tool,
            model=model,
            mode=mode,
            effort=effort,
            ai_explicit=ai_explicit,
            model_explicit=model_explicit,
            effort_explicit=effort_explicit,
            permission_mode=permission_mode,
            yolo=yolo,
            permission_mode_explicit=permission_mode_explicit,
            sandbox=sandbox,
            delegation_kind=DelegationKind.CODE_REVIEW,
            method_section=prepared.method_section,
            input_label="Diff input",
            cwd=repo_root,
            relaunch_skills=skills,
            relaunch_staged=staged,
        )
    except SkillInvocationError as exc:
        console.error(str(exc))
        cleanup_delegation_bundle(prepared, preserve=True)
        return DelegationResult(
            success=False,
            feedback=str(exc),
            mode=DelegationMode.PROMPT,
            exit_code=1,
        )
    cleanup_delegation_bundle(prepared, preserve=not result.success)
    # Count completed reviews and real headless timeouts toward the cap. A
    # reviewer that could not launch (missing login/PATH, sandbox denial, etc.)
    # has not consumed a review→fix cycle; counting it would make `done` skip a
    # required review for an infrastructure failure (#462).
    if (result.success and result.mode is not DelegationMode.PROMPT) or result.timed_out:
        outcome = ReviewOutcome.REVIEWED if result.success else ReviewOutcome.TIMED_OUT
        passes = _record_binding_outcome(repo_root, head, prepared, outcome)
        # Surface the running budget from the command itself so the caller sees
        # how many passes remain before `done` stops requiring re-review — no
        # need to rely on the "run at most N times" rule buried in the
        # skill/prompt. Guarded by an int check so a mocked
        # a failed receipt write never triggers it.
        if isinstance(passes, int):
            _announce_review_pass_budget(passes, config.done.max_review_passes)
    elif result.success:
        console.info(
            "Prompt emitted; no satisfying review receipt was written. Perform the "
            "self-review, then acknowledge it explicitly."
        )
    else:
        _report_failed_review(repo_root, head, prepared, result)
    return result
