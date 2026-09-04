"""Generic AI delegation service — delegate and wait.

Three modes share the same caller contract (call → block → get feedback):

- **prompt**      — returns the prompt text as feedback; caller self-reviews
- **headless**    — runs AI non-interactively; captures stdout
- **interactive** — launches AI in a new terminal; blocks until done; reads output file
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import structlog
from crossby.ai_tools import AbstractAITool
from crossby.models.ai import AIToolID, EffortLevel

from wade.models.config import AICommandConfig
from wade.models.delegation import DelegationMode, DelegationRequest, DelegationResult
from wade.models.permission import PermissionMode, permission_mode_launch_kwargs
from wade.services.ai_resolution import LAUNCH_NETWORK_ACCESS, announce_inherited_sandbox
from wade.services.prompt_delivery import deliver_prompt_if_needed
from wade.ui import prompts
from wade.ui.console import console
from wade.utils.process import CommandError, run
from wade.utils.runtime_env import detect_parent_runtime, requires_unsandboxed_relaunch

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Headless timeout scaling + budget-aware retry
# ---------------------------------------------------------------------------
#
# A headless review/deps subprocess embeds the whole payload (diff or issue
# context) in its prompt, so cost is ~proportional to prompt size *and* reasoning
# effort. A flat budget over-serves a tiny diff and starves a big one (the #363
# repro timed out twice at 300s / effort:high, then finished in one run at 900s).
# Scale the first-attempt budget from both signals, bounded floor..ceiling, and
# retry once at a longer budget on timeout — bounding the *sum* of both attempts
# so the worst case is predictable. These constants are tuned against the #363
# repro; treat them as a starting point to measure, not as sacred.
TIMEOUT_FLOOR = 600  # landed default (#385): CLI cold-start + a small high-effort run
TIMEOUT_CEILING = 1500  # single-attempt upper bound
# ~0.0075 s/byte: an ~800-line (~40 KB) prompt adds ~300s over the floor.
_TIMEOUT_SECONDS_PER_BYTE = 0.0075
_EFFORT_MULTIPLIER = {"high": 1.5, "xhigh": 1.75, "max": 1.75}  # else 1.0

TIMEOUT_RETRY_MULTIPLIER = 1.5
# Sized so a ceiling-length first attempt still gets the *full* multiplier on
# retry: TIMEOUT_CEILING + TIMEOUT_CEILING * TIMEOUT_RETRY_MULTIPLIER. A flat
# cap here (e.g. 2400) would clip the retry below the first attempt for large
# first-attempt budgets (#366 review) — exactly the big-prompt case a retry
# needs to help most. ~3750s (62.5 min) at current constants.
TOTAL_TIMEOUT_CAP = TIMEOUT_CEILING + round(TIMEOUT_CEILING * TIMEOUT_RETRY_MULTIPLIER)

MAX_HEADLESS_STDERR_LINES = 20
MAX_HEADLESS_STDERR_CHARS = 4_000


def scaled_timeout(payload_bytes: int, effort: str | None = None) -> int:
    """Scale a headless budget from payload size and effort, bounded floor..ceiling."""
    base = TIMEOUT_FLOOR + round(_TIMEOUT_SECONDS_PER_BYTE * payload_bytes)
    base = round(base * _EFFORT_MULTIPLIER.get(effort or "", 1.0))
    return max(TIMEOUT_FLOOR, min(TIMEOUT_CEILING, base))


def effective_timeout(prompt: str, configured: int | None, effort: str | None = None) -> int:
    """Resolve the first-attempt headless budget for ``prompt``.

    An explicit ``ai.<cmd>.timeout`` is a deliberate override: honor it verbatim
    and bypass scaling. This preserves the per-project ``.wade.yml`` workaround
    semantics and is the escape hatch for orchestrators with a hard tool-timeout
    (set it below the harness limit). Otherwise scale from payload bytes + effort.
    """
    if configured is not None:
        return configured
    return scaled_timeout(len(prompt.encode("utf-8")), effort)


def extended_timeout(t: int) -> int:
    """Retry budget for a timed-out first attempt of length ``t`` seconds.

    A meaningful extension (``t * TIMEOUT_RETRY_MULTIPLIER``) that never lets the
    sum ``t + extended_timeout(t)`` exceed ``TOTAL_TIMEOUT_CAP``. ``TOTAL_TIMEOUT_CAP``
    is sized so the full multiplier always applies for ``t`` up to
    ``TIMEOUT_CEILING`` — the retry is always strictly longer than the attempt
    that just timed out, never clipped to it (#366 review). Returns 0 (no
    retry) when ``t`` is already at/over the cap.
    """
    return max(0, min(round(t * TIMEOUT_RETRY_MULTIPLIER), TOTAL_TIMEOUT_CAP - t))


def resolve_mode(
    cmd_config: AICommandConfig,
    default: DelegationMode = DelegationMode.PROMPT,
) -> DelegationMode:
    """Resolve the delegation mode from config, falling back to ``default``."""
    if cmd_config.mode:
        try:
            return DelegationMode(cmd_config.mode)
        except ValueError:
            logger.warning("delegation.invalid_mode", mode=cmd_config.mode, fallback=default.value)
    return default


def _parse_effort(raw: str | None) -> EffortLevel | None:
    """Convert a raw effort string to EffortLevel, or None."""
    if not raw:
        return None
    try:
        return EffortLevel(raw)
    except ValueError:
        return None


def _warn_on_inherited_sandbox(request: DelegationRequest) -> bool:
    """Warn immediately before a validated delegated runtime launches.

    Deps, standalone plan/code review and batch review all funnel through
    ``delegate()``, so this cross-cutting launch concern belongs here **once**
    rather than in each service — three copies would drift. Batch implementation
    is separate: it diagnoses once in ``batch.py`` before terminal brokers can
    drop the enclosing process's runtime markers.

    Warns and proceeds rather than blocking. wade cannot prove the delegated tool
    will fail — a runtime with credentials reachable from inside the sandbox may
    well succeed — so refusing to try would break sessions that work today. If it
    does fail, ``never_launched`` carries the classification onward.
    """
    return announce_inherited_sandbox(
        detect_parent_runtime(),
        resolved_sandbox=request.sandbox,
        operation=request.operation or "this delegated run",
        relaunch_command=request.relaunch_command,
    )


def _has_inherited_sandbox_profile_mismatch(request: DelegationRequest) -> bool:
    """Assess the requested profile without emitting remediation prematurely."""
    return requires_unsandboxed_relaunch(
        resolved_sandbox=request.sandbox,
        parent=detect_parent_runtime(),
    )


def delegate(request: DelegationRequest) -> DelegationResult:
    """Dispatch a delegation request to the appropriate mode runner."""
    if request.mode == DelegationMode.PROMPT:
        return _delegate_prompt(request)

    # Preserve the resolved mismatch for the post-launch diagnostic, but do not
    # announce it until the mode/tool capability guards prove a runtime can
    # actually launch. A missing tool or headless-capability refusal cannot be
    # repaired by relaunching the enclosing session.
    profile_mismatch = _has_inherited_sandbox_profile_mismatch(request)

    # ``never_launched`` includes preflight refusals such as an unknown tool.
    # Record the narrower spawn boundary separately, so a user-controlled error
    # mentioning ``seatbelt`` cannot turn that configuration error into a false
    # inherited-sandbox diagnosis downstream.
    launch_attempted = False

    def before_launch() -> bool:
        nonlocal launch_attempted
        launch_attempted = True
        return _warn_on_inherited_sandbox(request)

    if request.mode == DelegationMode.HEADLESS:
        result = _delegate_headless(request, before_launch=before_launch)
    elif request.mode == DelegationMode.INTERACTIVE:
        result = _delegate_interactive(request, before_launch=before_launch)
    else:
        result = DelegationResult(
            success=False,
            feedback=f"Unknown delegation mode: {request.mode}",
            mode=request.mode,
            exit_code=1,
            never_launched=True,
        )
    result.launch_attempted = launch_attempted
    result.inherited_sandbox_profile_mismatch = profile_mismatch and launch_attempted
    result.relaunch_command = request.relaunch_command
    return result


def _delegate_prompt(request: DelegationRequest) -> DelegationResult:
    """Return the raw prompt text directly — no user-facing wrapper."""
    return DelegationResult(
        success=True,
        feedback=request.prompt,
        mode=DelegationMode.PROMPT,
    )


def _partial_from_timeout(exc: subprocess.TimeoutExpired) -> str:
    """Decoded, stripped partial stdout from a timed-out headless run.

    ``utils.process.run`` already decodes and reattaches ``exc.stdout`` (bytes →
    str), but guard against bytes defensively: ``TimeoutExpired.stdout`` is bytes
    even under ``text=True`` (the buffer is collected before the decode step), so
    a caller that bypasses the process layer would still hand us bytes here.
    """
    raw: str | bytes | None = exc.stdout
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return (raw or "").strip()


def _timeout_result(partial: str) -> DelegationResult:
    """A non-success result flagged as a timeout (not a crash), carrying any partial output."""
    return DelegationResult(
        success=False,
        timed_out=True,
        feedback=partial or "<no output before the budget elapsed>",
        mode=DelegationMode.HEADLESS,
        exit_code=1,
    )


def _format_headless_failure(stdout: str, stderr: str) -> str:
    """Combine failed headless output while bounding stderr diagnostics."""
    stdout = stdout.strip()
    stderr_lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    stderr_truncated = len(stderr_lines) > MAX_HEADLESS_STDERR_LINES
    stderr_lines = stderr_lines[-MAX_HEADLESS_STDERR_LINES:]
    stderr_output = "\n".join(stderr_lines)

    if len(stderr_output) > MAX_HEADLESS_STDERR_CHARS:
        stderr_output = stderr_output[-MAX_HEADLESS_STDERR_CHARS:]
        stderr_truncated = True

    if not stderr_output:
        return stdout or "Headless session failed with no output"

    stderr_label = "Headless session stderr"
    if stderr_truncated:
        stderr_label += " (truncated)"
    stderr_feedback = f"{stderr_label}:\n{stderr_output}"
    return f"{stdout}\n\n{stderr_feedback}" if stdout else stderr_feedback


def _crash_result(exc: CommandError) -> DelegationResult:
    """A non-success result for a headless crash — ``timed_out`` stays False (never retried).

    ``CommandError`` here means the spawn itself failed (a missing or
    non-executable binary, a denied exec), so nothing ran: ``never_launched``.
    A process that started and exited non-zero comes back through
    ``_run_headless_once`` instead and keeps the flag False.
    """
    return DelegationResult(
        success=False,
        feedback=f"Headless session failed: {exc}",
        mode=DelegationMode.HEADLESS,
        exit_code=1,
        never_launched=True,
    )


def _run_headless_once(cmd: list[str], timeout: int, session_cwd: Path) -> DelegationResult:
    """Run the headless subprocess once.

    Returns success/non-zero results directly; lets ``TimeoutExpired`` and
    ``CommandError`` propagate so the caller can decide whether to retry.
    """
    result = run(cmd, check=False, timeout=timeout, cwd=session_cwd)
    stdout = result.stdout.strip() if isinstance(result.stdout, str) else ""
    if result.returncode == 0:
        return DelegationResult(
            success=True,
            feedback=stdout,
            mode=DelegationMode.HEADLESS,
            exit_code=0,
        )
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    return DelegationResult(
        success=False,
        feedback=_format_headless_failure(stdout, stderr),
        mode=DelegationMode.HEADLESS,
        exit_code=result.returncode,
    )


def _delegate_headless(
    request: DelegationRequest,
    *,
    before_launch: Callable[[], bool] | None = None,
) -> DelegationResult:
    """Run AI non-interactively and capture stdout."""
    session_cwd = request.cwd or Path.cwd()

    # The three guards below all return *before* a process exists, so each is
    # unambiguously "never launched" — no interpretation required.
    if not request.ai_tool:
        return DelegationResult(
            success=False,
            feedback="No AI tool specified for headless mode",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
            never_launched=True,
        )

    try:
        adapter = AbstractAITool.get(AIToolID(request.ai_tool))
    except (ValueError, KeyError):
        return DelegationResult(
            success=False,
            feedback=f"Unknown AI tool: {request.ai_tool}",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
            never_launched=True,
        )

    caps = adapter.capabilities()
    if not caps.supports_headless or not caps.headless_flag:
        return DelegationResult(
            success=False,
            feedback=f"AI tool {request.ai_tool} does not support headless mode",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
            never_launched=True,
        )

    defaults = [str(session_cwd), tempfile.gettempdir()]
    trusted = defaults + [d for d in request.trusted_dirs if d not in defaults]

    # Headless commands (deps, review_*) are read/analytical and never need an
    # autonomy grant, so the headless path always runs at ``default`` — any
    # configured permission mode is intentionally ignored here (see
    # KNOWLEDGE.md: headless-vs-autonomy). Forcing the DEFAULT tier keeps
    # yolo/auto/accept_edits all off, symmetric with the interactive path.
    cmd = adapter.build_launch_command(
        model=request.model,
        prompt=request.prompt,
        trusted_dirs=trusted,
        allowed_commands=request.allowed_commands or None,
        effort=_parse_effort(request.effort),
        # Grant a linked worktree's out-of-root git metadata so a sandboxed Codex
        # deps/review run can do local git work. Inert for a main checkout, for
        # every non-Codex tool, and under an unrestricted profile. The profile
        # itself comes from the caller rather than being hardcoded, so a delegated
        # run inherits the session's runtime boundary (#478).
        working_dir=session_cwd,
        network_access=LAUNCH_NETWORK_ACCESS,
        sandbox=request.sandbox,
        **permission_mode_launch_kwargs(PermissionMode.DEFAULT),
    )

    # First attempt at request.timeout; on timeout (never on a crash) retry once
    # at a longer budget. extended_timeout bounds the *sum* of both legs, so the
    # worst case is predictable (<= TOTAL_TIMEOUT_CAP) and matches the pre-run
    # advisory. A crash or non-zero exit returns immediately — no retry. An
    # explicit ai.<cmd>.timeout is honored verbatim with NO retry: it is the
    # escape hatch for a hard tool-timeout, so a retry that overshoots it would
    # defeat the purpose (and get killed by the harness anyway).
    attempts = [request.timeout]
    if not request.explicit_timeout:
        retry_timeout = extended_timeout(request.timeout)
        if retry_timeout > 0:
            attempts.append(retry_timeout)

    if before_launch is not None:
        before_launch()

    partial = ""
    # A retry is only eligible after a real child timed out.  Keep that fact
    # through a later spawn/non-zero failure: the final failure is not an
    # unattempted delegation, and review receipts must continue to account for
    # the earlier timed-out pass.
    timed_out_before_final_attempt = False
    for index, budget in enumerate(attempts):
        is_last = index == len(attempts) - 1
        if index > 0:
            # Announce the retry so the orchestrator driving wade does not kill it
            # early — the pre-launch advisory already reserved this headroom.
            logger.warning(
                "delegation.headless_timeout_retry",
                tool=request.ai_tool,
                first_timeout=request.timeout,
                retry_timeout=budget,
            )
            console.warn(
                f"Headless session timed out after {request.timeout}s — retrying "
                f"once with a longer budget ({budget}s; worst-case total "
                f"{request.timeout + budget}s). Keep it in the foreground."
            )
        try:
            result = _run_headless_once(cmd, budget, session_cwd)
            if timed_out_before_final_attempt and not result.success:
                return result.model_copy(update={"timed_out": True, "never_launched": False})
            return result
        except subprocess.TimeoutExpired as exc:
            timed_out_before_final_attempt = True
            # Prefer this attempt's partial output; fall back to a prior attempt's.
            partial = _partial_from_timeout(exc) or partial
            if is_last:
                logger.warning(
                    "delegation.headless_timeout",
                    tool=request.ai_tool,
                    timeout=budget,
                    retried=index > 0,
                )
        except CommandError as e:
            logger.warning("delegation.headless_failed", tool=request.ai_tool, error=str(e))
            result = _crash_result(e)
            if timed_out_before_final_attempt:
                return result.model_copy(update={"timed_out": True, "never_launched": False})
            return result
        except OSError as e:
            # ``run`` maps only ``FileNotFoundError`` to ``CommandError``; a
            # binary that resolves on PATH but cannot be *executed* raises
            # ``PermissionError`` straight through and used to abort the whole
            # command with a traceback. That is one of the shapes an inherited
            # sandbox produces, so it has to arrive as a classified
            # never-launched result instead (#480).
            logger.warning("delegation.headless_spawn_failed", tool=request.ai_tool, error=str(e))
            result = DelegationResult(
                success=False,
                feedback=f"Headless session failed to start: {e}",
                mode=DelegationMode.HEADLESS,
                exit_code=1,
                never_launched=True,
            )
            if timed_out_before_final_attempt:
                return result.model_copy(update={"timed_out": True, "never_launched": False})
            return result

    return _timeout_result(partial)


def _interactive_failure(exc: Exception, *, launched: bool) -> DelegationResult:
    """A failed interactive delegation, classified by whether a child ever existed.

    ``launched`` is the caller's judgement about the *launch boundary*, not about
    whether the AI session succeeded: a session that started and then failed is
    still an attempted one, and must never be reported as never-launched (that
    would record an ``UNATTEMPTED`` review outcome and offer sandbox-relaunch
    advice for a reviewer that actually ran).
    """
    return DelegationResult(
        success=False,
        feedback=(
            f"Interactive session failed after launch: {exc}"
            if launched
            else f"AI tool launch failed: {exc}"
        ),
        mode=DelegationMode.INTERACTIVE,
        exit_code=1,
        never_launched=not launched,
    )


def _delegate_interactive(
    request: DelegationRequest,
    *,
    before_launch: Callable[[], bool] | None = None,
) -> DelegationResult:
    """Launch AI interactively; block until done; read output from file."""
    session_cwd = request.cwd or Path.cwd()

    if not request.ai_tool:
        return DelegationResult(
            success=False,
            feedback="No AI tool specified for interactive mode",
            mode=DelegationMode.INTERACTIVE,
            exit_code=1,
            never_launched=True,
        )

    try:
        adapter = AbstractAITool.get(AIToolID(request.ai_tool))
    except (ValueError, KeyError):
        return DelegationResult(
            success=False,
            feedback=f"Unknown AI tool: {request.ai_tool}",
            mode=DelegationMode.INTERACTIVE,
            exit_code=1,
            never_launched=True,
        )

    # Set up output file for the AI to write results to. These are pre-launch
    # filesystem operations too: a read-only temp directory must not escape as a
    # traceback or be mistaken for an error from a child that never existed.
    output_file = request.output_file
    created_tmp = output_file is None
    tmp_dir: str | None = None
    try:
        if output_file is None:
            tmp_dir = tempfile.mkdtemp(prefix="wade-delegation-")
            output_file = Path(tmp_dir) / "delegation-output.txt"
        else:
            if not output_file.is_absolute():
                output_file = session_cwd / output_file
            output_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _interactive_failure(exc, launched=False)
    assert output_file is not None

    # Append output instruction to prompt
    output_instruction = (
        f"\n\n---\nWrite your output to: {output_file}\nWhen done, exit the session."
    )
    interactive_prompt = request.prompt + output_instruction

    defaults = [str(session_cwd), tempfile.gettempdir()]
    trusted = defaults + [d for d in request.trusted_dirs if d not in defaults]
    if tmp_dir:
        trusted.append(tmp_dir)
    elif str(output_file.parent) not in trusted:
        trusted.append(str(output_file.parent))

    # Only a failure *before the child process exists* is a launch failure, and
    # the distinction decides the remediation: a reviewer that never started
    # earns "restore the runtime", one that ran and then failed must not. Two
    # things separate them here — where the failing call sits, and the shape of
    # the exception:
    #
    # - Anything up to and including the spawn that raises ``OSError`` (missing
    #   binary, denied exec, unwritable artefact) started nothing.
    # - ``adapter.launch`` *blocks until the child exits* for most runtimes, so
    #   an adapter that checks the exit status raises ``CalledProcessError`` —
    #   and a bounded one ``TimeoutExpired`` — from a session that very much ran.
    #   Every ``subprocess.SubprocessError`` variant carries a child that was
    #   created, so it is classified as launched even though the call never
    #   returned (#481 review).
    # - Everything after the adapter returns (the confirm prompt, reading the
    #   output file) is post-launch by position, whatever it raises.
    try:
        try:
            deliver_prompt_if_needed(adapter, interactive_prompt)
        except (OSError, subprocess.SubprocessError) as e:
            # Clipboard fallback for tools without initial-message support; it
            # runs entirely before the spawn, so nothing has started yet.
            return _interactive_failure(e, launched=False)

        try:
            if before_launch is not None:
                before_launch()
            adapter.launch(
                working_dir=session_cwd,
                model=request.model,
                prompt=interactive_prompt,
                trusted_dirs=trusted,
                allowed_commands=request.allowed_commands or None,
                effort=_parse_effort(request.effort),
                # Same as the headless path: grant a linked worktree's git metadata
                # for sandboxed git writes, and inherit the caller's profile.
                network_access=LAUNCH_NETWORK_ACCESS,
                sandbox=request.sandbox,
                **permission_mode_launch_kwargs(request.permission_mode),
            )
        except OSError as e:
            return _interactive_failure(e, launched=False)
        except subprocess.SubprocessError as e:
            return _interactive_failure(e, launched=True)

        # Non-blocking tools return immediately — wait for user
        if not adapter.capabilities().blocks_until_exit:
            console.empty()
            if not prompts.confirm("Have you finished the session?", default=True):
                return DelegationResult(
                    success=False,
                    feedback="Interactive session cancelled by user",
                    mode=DelegationMode.INTERACTIVE,
                    exit_code=1,
                )

        # Read output file after AI exits (must happen before temp dir cleanup)
        if output_file.is_file():
            text = output_file.read_text(encoding="utf-8").strip()
            if text:
                return DelegationResult(
                    success=True,
                    feedback=text,
                    mode=DelegationMode.INTERACTIVE,
                    exit_code=0,
                )

        return DelegationResult(
            success=False,
            feedback="No output file found after interactive session",
            mode=DelegationMode.INTERACTIVE,
            exit_code=1,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return _interactive_failure(e, launched=True)
    finally:
        if created_tmp and tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
