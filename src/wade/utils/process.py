"""Subprocess helpers with timeout and structured logging."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import time
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


def _decode_stream(value: str | bytes | None) -> str | None:
    """Decode subprocess output bytes to ``str`` (``errors="replace"``); pass str/None through.

    ``subprocess.TimeoutExpired.stdout``/``.stderr`` carry the partial output the
    process emitted before the budget elapsed — but as **bytes** even when the
    call ran under ``text=True`` (the buffer is collected before the decode step).
    Decoding here keeps that partial output text-consistent for callers.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run(
    command: list[str],
    cwd: Path | str | None = None,
    timeout: int = 120,
    check: bool = True,
    capture: bool = True,
    input_text: str | None = None,
    retries: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command with logging and error handling.

    Args:
        command: Command and arguments.
        cwd: Working directory.
        timeout: Timeout in seconds.
        check: If True, raise CommandError on non-zero exit.
        capture: If True, capture stdout/stderr.
        input_text: Optional stdin text.
        retries: Number of times to retry on non-zero exit (default 0).

    Returns:
        CompletedProcess with text stdout/stderr.

    Raises:
        CommandError: If check=True and the command returns non-zero after all retries.
    """
    logger.debug("subprocess.run", command=command, cwd=str(cwd) if cwd else None)

    attempt = 0
    while True:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                timeout=timeout,
                capture_output=capture,
                text=True,
                input=input_text,
            )
        except subprocess.TimeoutExpired as e:
            # Preserve any partial output produced before the budget elapsed.
            # TimeoutExpired carries it on .stdout/.stderr but as bytes (collected
            # pre-decode), so decode and reattach to keep it text-consistent with
            # this text=True call, then re-raise. No current caller reads these off
            # the exception (verified), so reattaching is safe.
            # typeshed types .stdout/.stderr as bytes|None, but the runtime
            # setters accept anything and we deliberately store decoded str.
            e.stdout = _decode_stream(e.stdout)  # type: ignore[assignment]
            e.stderr = _decode_stream(e.stderr)  # type: ignore[assignment]
            captured = len(e.stdout) if isinstance(e.stdout, str) else 0
            # Don't log the full command: callers (e.g. headless AI delegation)
            # embed prompt text — diffs, issue bodies, user input — as args, and
            # that would land in production error logs (#366 review).
            logger.error(
                "subprocess.timeout",
                executable=command[0],
                argument_count=len(command) - 1,
                timeout=timeout,
                captured_chars=captured,
            )
            raise
        except FileNotFoundError as err:
            logger.error("subprocess.not_found", command=command[0])
            raise CommandError(command, 127, f"Command not found: {command[0]}") from err

        if check and result.returncode != 0:
            stderr = result.stderr.strip() if capture else ""
            if attempt < retries:
                attempt += 1
                logger.warning(
                    "subprocess.retrying",
                    command=command,
                    returncode=result.returncode,
                    attempt=attempt,
                    retries=retries,
                    stderr=stderr[:200],
                )
                time.sleep(attempt)
                continue
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
        full_cmd = ["script", "-q", "-e", "-c", cmd_str, str(transcript_path)]
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
