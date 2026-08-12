"""Review delegation service — plan review and implementation review via delegation."""

from __future__ import annotations

from pathlib import Path

import structlog
from crossby.models.ai import EffortLevel
from rich.markup import escape

from wade.config.loader import load_config
from wade.git import repo as git_repo
from wade.git.repo import GitError
from wade.models.config import AICommandConfig, ProjectConfig
from wade.models.delegation import DelegationMode, DelegationRequest, DelegationResult
from wade.models.permission import PermissionMode
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
from wade.skills.installer import load_prompt_template
from wade.ui.console import console
from wade.utils.markers import count_review_passes, record_review_pass, write_marker

logger = structlog.get_logger()


def _mark_reviewed() -> None:
    """Record that ``wade review implementation`` ran for the current commit.

    Writes a sha-keyed ``.wade/reviewed@<HEAD>`` marker (best-effort) that the
    ``done`` review-ran gate later checks. Because it is keyed to the HEAD sha,
    any commit made while addressing findings invalidates it — forcing a
    re-review. This records that the command *ran for this sha*, not that
    findings were addressed (documented honestly; #355 relaxes the phrasing).
    """
    try:
        repo_root = git_repo.get_repo_root(Path.cwd())
        head = git_repo.rev_parse(repo_root, "HEAD")
    except GitError:
        logger.debug("review.reviewed_marker_skipped", exc_info=True)
        return
    write_marker(repo_root, "reviewed", head)


def _record_review_pass() -> int | None:
    """Count one delegation-backed implementation-review pass for the cap (#384).

    Writes a ``.wade/review-pass@<HEAD>`` marker **independent of the review's
    success** — even a headless timeout (which exits non-zero and writes no
    ``reviewed`` marker) still consumed a real review→fix cycle, so it must
    advance the pass count that the ``done`` gate's review-pass cap reads. Per-sha and
    idempotent, so re-running review on the same HEAD does not inflate the count.

    Returns the resulting distinct-pass count, or ``None`` if the marker could not
    be recorded (best-effort: a git failure is logged and skipped).
    """
    try:
        repo_root = git_repo.get_repo_root(Path.cwd())
        head = git_repo.rev_parse(repo_root, "HEAD")
    except GitError:
        logger.debug("review.review_pass_marker_skipped", exc_info=True)
        return None
    record_review_pass(repo_root, head)
    return count_review_passes(repo_root)


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


def _run_review_delegation(
    prompt: str,
    command: str,
    *,
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
) -> DelegationResult:
    """Shared pipeline: config load → mode resolve → AI resolve → confirm → delegate → display."""
    if config is None or cmd_config is None:
        config, cmd_config = _load_review_config(command)

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

    if delegation_mode != DelegationMode.PROMPT:
        resolved_tool = resolve_ai_tool(ai_tool, config, command=command)
        resolved_model = resolve_model(model, config, command=command, tool=resolved_tool)
        resolved_effort = resolve_effort(effort, config, command=command, tool=resolved_tool)
        resolved_permission_mode = resolve_permission_mode(
            permission_mode, yolo, config, command=command
        )

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
            )
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
    request = DelegationRequest(
        mode=delegation_mode,
        prompt=prompt,
        ai_tool=resolved_tool,
        model=resolved_model,
        effort=effort_str,
        permission_mode=effective_permission_mode,
        timeout=effective_timeout(prompt, cmd_config.timeout, effort_str),
        explicit_timeout=explicit_timeout,
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
    template = load_prompt_template("review-plan.md")
    prompt = template.replace("{plan_content}", plan_content)

    return _run_review_delegation(
        prompt,
        "review_plan",
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
    )


def _committed_diff_fallback() -> str:
    """Return branch diff against the base branch when working tree is clean.

    Uses ``git diff <base>...HEAD`` (three-dot syntax) to show changes
    committed on the current branch since it diverged from the base branch.
    The base branch is the configured ``main_branch`` or auto-detected
    ``main``/``master``.

    Returns empty string if on the base branch, if the repo root cannot be
    resolved, or on any GitError (graceful degradation).
    """
    try:
        repo_root = git_repo.get_repo_root(Path.cwd())
        current_branch = git_repo.get_current_branch(repo_root)
        config = load_config()
        base_branch = config.project.main_branch or git_repo.detect_main_branch(repo_root)
        if current_branch == base_branch:
            return ""
        return git_repo.diff_between(repo_root, base_branch, "HEAD")
    except GitError:
        return ""


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
) -> DelegationResult:
    """Review implementation changes via the delegation infrastructure."""
    config, cmd_config = _load_review_config("review_implementation")
    skip = _check_review_enabled("review_implementation", cmd_config)
    if skip is not None:
        return skip

    try:
        repo_root = git_repo.get_repo_root(Path.cwd())
        diff_content = git_repo.diff_worktree(repo_root, staged=staged).strip()
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

    if not diff_content and not staged:
        diff_content = _committed_diff_fallback()

    if not diff_content:
        label = "staged changes" if staged else "changes"
        console.warn(f"No {label} to review.")
        # "No diff to review" still counts as review having run for this sha —
        # there is nothing to critique, so record the marker so `done` isn't
        # falsely blocked on a review that had no work to do.
        _mark_reviewed()
        return DelegationResult(
            success=True,
            feedback=f"No {label} to review.",
            mode=DelegationMode.PROMPT,
            skipped=True,
        )

    template = load_prompt_template("review-code.md")
    prompt = template.replace("{diff_content}", diff_content)

    result = _run_review_delegation(
        prompt,
        "review_implementation",
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
    )
    # Count this delegation-backed pass toward the `done` review cap regardless
    # of success — a headless timeout still consumed a review→fix cycle, so it
    # must advance the count (#384). This runs only past the no-diff early return
    # above, so an empty review never spends a cap slot. Per-sha and idempotent.
    passes = _record_review_pass()
    # Surface the running budget from the command itself so the caller sees how
    # many passes remain before `done` stops requiring re-review — no need to rely
    # on the "run at most N times" rule buried in the skill/prompt. Guarded by an
    # int check so a mocked `_record_review_pass` in tests never triggers it.
    if isinstance(passes, int):
        _announce_review_pass_budget(passes, config.done.max_review_passes)
    # Record the review-ran marker on any non-hard-failure result (success),
    # keyed to the current HEAD sha. The `done` review-ran gate reads it.
    if result.success:
        _mark_reviewed()
    return result
