"""Subprocess helpers with timeout and structured logging."""

from __future__ import annotations

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
