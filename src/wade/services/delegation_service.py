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
from pathlib import Path

import structlog
from crossby.ai_tools import AbstractAITool
from crossby.models.ai import AIToolID, EffortLevel

from wade.models.config import AICommandConfig
from wade.models.delegation import DelegationMode, DelegationRequest, DelegationResult
from wade.models.permission import PermissionMode, permission_mode_launch_kwargs
from wade.services.prompt_delivery import deliver_prompt_if_needed
from wade.ui import prompts
from wade.ui.console import console
from wade.utils.process import CommandError, run

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


def delegate(request: DelegationRequest) -> DelegationResult:
    """Dispatch a delegation request to the appropriate mode runner."""
    if request.mode == DelegationMode.PROMPT:
        return _delegate_prompt(request)
    if request.mode == DelegationMode.HEADLESS:
        return _delegate_headless(request)
    if request.mode == DelegationMode.INTERACTIVE:
        return _delegate_interactive(request)

    return DelegationResult(
        success=False,
        feedback=f"Unknown delegation mode: {request.mode}",
        mode=request.mode,
        exit_code=1,
    )


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


def _crash_result(exc: CommandError) -> DelegationResult:
    """A non-success result for a headless crash — ``timed_out`` stays False (never retried)."""
    return DelegationResult(
        success=False,
        feedback=f"Headless session failed: {exc}",
        mode=DelegationMode.HEADLESS,
        exit_code=1,
    )


def _run_headless_once(cmd: list[str], timeout: int, session_cwd: Path) -> DelegationResult:
    """Run the headless subprocess once.

    Returns success/non-zero results directly; lets ``TimeoutExpired`` and
    ``CommandError`` propagate so the caller can decide whether to retry.
    """
    result = run(cmd, check=False, timeout=timeout, cwd=session_cwd)
    stdout = result.stdout.strip() if result.stdout else ""
    if result.returncode == 0:
        return DelegationResult(
            success=True,
            feedback=stdout,
            mode=DelegationMode.HEADLESS,
            exit_code=0,
        )
    return DelegationResult(
        success=False,
        feedback=stdout or "Headless session failed with no output",
        mode=DelegationMode.HEADLESS,
        exit_code=result.returncode,
    )


def _delegate_headless(request: DelegationRequest) -> DelegationResult:
    """Run AI non-interactively and capture stdout."""
    session_cwd = request.cwd or Path.cwd()

    if not request.ai_tool:
        return DelegationResult(
            success=False,
            feedback="No AI tool specified for headless mode",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
        )

    try:
        adapter = AbstractAITool.get(AIToolID(request.ai_tool))
    except (ValueError, KeyError):
        return DelegationResult(
            success=False,
            feedback=f"Unknown AI tool: {request.ai_tool}",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
        )

    caps = adapter.capabilities()
    if not caps.supports_headless or not caps.headless_flag:
        return DelegationResult(
            success=False,
            feedback=f"AI tool {request.ai_tool} does not support headless mode",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
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
        # deps/review run can do local git work; network stays off (these
        # headless commands are read/analytical and never fetch/push). Inert for
        # a main checkout and for every non-Codex tool.
        working_dir=session_cwd,
        network_access=False,
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

    partial = ""
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
            return _run_headless_once(cmd, budget, session_cwd)
        except subprocess.TimeoutExpired as exc:
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
            return _crash_result(e)

    return _timeout_result(partial)


def _delegate_interactive(request: DelegationRequest) -> DelegationResult:
    """Launch AI interactively; block until done; read output from file."""
    session_cwd = request.cwd or Path.cwd()

    if not request.ai_tool:
        return DelegationResult(
            success=False,
            feedback="No AI tool specified for interactive mode",
            mode=DelegationMode.INTERACTIVE,
            exit_code=1,
        )

    try:
        adapter = AbstractAITool.get(AIToolID(request.ai_tool))
    except (ValueError, KeyError):
        return DelegationResult(
            success=False,
            feedback=f"Unknown AI tool: {request.ai_tool}",
            mode=DelegationMode.INTERACTIVE,
            exit_code=1,
        )

    # Set up output file for the AI to write results to
    output_file = request.output_file
    created_tmp = output_file is None
    if output_file is None:
        tmp_dir = tempfile.mkdtemp(prefix="wade-delegation-")
        output_file = Path(tmp_dir) / "delegation-output.txt"
    else:
        tmp_dir = None
        if not output_file.is_absolute():
            output_file = session_cwd / output_file
        output_file.parent.mkdir(parents=True, exist_ok=True)

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

    try:
        deliver_prompt_if_needed(adapter, interactive_prompt)
        adapter.launch(
            working_dir=session_cwd,
            model=request.model,
            prompt=interactive_prompt,
            trusted_dirs=trusted,
            allowed_commands=request.allowed_commands or None,
            effort=_parse_effort(request.effort),
            # Same as the headless path: grant a linked worktree's git metadata
            # for sandboxed git writes; network off (inert for non-Codex).
            network_access=False,
            **permission_mode_launch_kwargs(request.permission_mode),
        )

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
        return DelegationResult(
            success=False,
            feedback=f"AI tool launch failed: {e}",
            mode=DelegationMode.INTERACTIVE,
            exit_code=1,
        )
    finally:
        if created_tmp and tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
