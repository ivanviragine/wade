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

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import AIToolID, EffortLevel
from wade.models.config import AICommandConfig
from wade.models.delegation import DelegationMode, DelegationRequest, DelegationResult
from wade.services.prompt_delivery import deliver_prompt_if_needed
from wade.ui import prompts
from wade.ui.console import console
from wade.utils.process import CommandError, run

logger = structlog.get_logger()


def resolve_mode(cmd_config: AICommandConfig) -> DelegationMode:
    """Resolve the delegation mode from config, defaulting to prompt."""
    if cmd_config.mode:
        try:
            return DelegationMode(cmd_config.mode)
        except ValueError:
            pass
    return DelegationMode.PROMPT


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
    """Return the prompt text directly — caller copies into their AI tool."""
    header = "Copy the prompt below into your AI tool:\n\n---\n"
    return DelegationResult(
        success=True,
        feedback=header + request.prompt,
        mode=DelegationMode.PROMPT,
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
    cmd = adapter.build_launch_command(
        model=request.model,
        prompt=request.prompt,
        trusted_dirs=trusted,
        allowed_commands=request.allowed_commands or None,
        effort=_parse_effort(request.effort),
    )

    try:
        result = run(cmd, check=False, timeout=request.timeout, cwd=session_cwd)
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
    except subprocess.TimeoutExpired:
        logger.warning("delegation.headless_timeout", tool=request.ai_tool)
        return DelegationResult(
            success=False,
            feedback="Headless session timed out",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
        )
    except CommandError as e:
        logger.warning("delegation.headless_failed", tool=request.ai_tool, error=str(e))
        return DelegationResult(
            success=False,
            feedback=f"Headless session failed: {e}",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
        )


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

    # Set up output file for the AI to write results to
    output_file = request.output_file
    created_tmp = output_file is None
    if output_file is None:
        tmp_dir = tempfile.mkdtemp(prefix="wade-delegation-")
        output_file = Path(tmp_dir) / "delegation-output.txt"
    else:
        tmp_dir = None

    # Append output instruction to prompt
    output_instruction = (
        f"\n\n---\nWrite your output to: {output_file}\nWhen done, exit the session."
    )
    interactive_prompt = request.prompt + output_instruction

    defaults = [str(session_cwd), tempfile.gettempdir()]
    trusted = defaults + [d for d in request.trusted_dirs if d not in defaults]
    if tmp_dir:
        trusted.append(tmp_dir)

    try:
        try:
            adapter = AbstractAITool.get(AIToolID(request.ai_tool))
        except (ValueError, KeyError):
            return DelegationResult(
                success=False,
                feedback=f"Unknown AI tool: {request.ai_tool}",
                mode=DelegationMode.INTERACTIVE,
                exit_code=1,
            )

        try:
            deliver_prompt_if_needed(adapter, interactive_prompt)
            adapter.launch(
                worktree_path=session_cwd,
                model=request.model,
                prompt=interactive_prompt,
                trusted_dirs=trusted,
                allowed_commands=request.allowed_commands or None,
                effort=_parse_effort(request.effort),
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
        except (OSError, subprocess.SubprocessError) as e:
            return DelegationResult(
                success=False,
                feedback=f"AI tool launch failed: {e}",
                mode=DelegationMode.INTERACTIVE,
                exit_code=1,
            )

        # Read output file after AI exits
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
    finally:
        if created_tmp and tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
