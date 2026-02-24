"""Subprocess helpers with timeout and structured logging."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import structlog

logger = structlog.get_logger()


class CommandError(Exception):
    """Raised when a subprocess command fails."""

    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"Command {command[0]} failed (exit {returncode}): {stderr}")


def run(
    command: list[str],
    cwd: Path | str | None = None,
    timeout: int = 120,
    check: bool = True,
    capture: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command with logging and error handling.

    Args:
        command: Command and arguments.
        cwd: Working directory.
        timeout: Timeout in seconds.
        check: If True, raise CommandError on non-zero exit.
        capture: If True, capture stdout/stderr.
        input_text: Optional stdin text.

    Returns:
        CompletedProcess with text stdout/stderr.

    Raises:
        CommandError: If check=True and the command returns non-zero.
    """
    logger.debug("subprocess.run", command=command, cwd=str(cwd) if cwd else None)

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            timeout=timeout,
            capture_output=capture,
            text=True,
            input=input_text,
        )
    except subprocess.TimeoutExpired:
        logger.error("subprocess.timeout", command=command, timeout=timeout)
        raise
    except FileNotFoundError as err:
        logger.error("subprocess.not_found", command=command[0])
        raise CommandError(command, 127, f"Command not found: {command[0]}") from err

    if check and result.returncode != 0:
        stderr = result.stderr.strip() if capture else ""
        logger.error(
            "subprocess.failed",
            command=command,
            returncode=result.returncode,
            stderr=stderr[:200],
        )
        raise CommandError(command, result.returncode, stderr)

    return result


def run_with_transcript(
    cmd: list[str],
    transcript_path: Path | None,
    cwd: Path | str | None = None,
) -> int:
    """Run a command, capturing terminal output to transcript_path via `script`.

    Uses the `script` utility (BSD on macOS, GNU on Linux) to record the
    interactive session. Falls back to plain subprocess.run when transcript_path
    is None or `script` is not available.

    Behavioral reference: lib/task/tokens.sh:_task_run_with_transcript()
    """
    if transcript_path is None or not shutil.which("script"):
        result = subprocess.run(cmd, cwd=cwd)
        return result.returncode

    # Detect GNU vs BSD script: GNU accepts --version; BSD does not.
    version_check = subprocess.run(
        ["script", "--version"],
        capture_output=True,
    )

    if version_check.returncode == 0:
        # GNU script (Linux): script -q -c "cmd" transcript_file
        cmd_str = " ".join(shlex.quote(c) for c in cmd)
        full_cmd = ["script", "-q", "-c", cmd_str, str(transcript_path)]
    else:
        # BSD script (macOS): script -q transcript_file cmd...
        full_cmd = ["script", "-q", str(transcript_path), *cmd]

    logger.debug(
        "subprocess.run_with_transcript",
        cmd=cmd,
        transcript=str(transcript_path),
        cwd=str(cwd) if cwd else None,
    )

    result = subprocess.run(full_cmd, cwd=cwd)
    return result.returncode


def run_silent(
    command: list[str],
    cwd: Path | str | None = None,
    timeout: int = 120,
) -> bool:
    """Run a command silently, returning True on success, False on failure."""
    try:
        run(command, cwd=cwd, timeout=timeout, check=True, capture=True)
        return True
    except (CommandError, subprocess.TimeoutExpired):
        return False
