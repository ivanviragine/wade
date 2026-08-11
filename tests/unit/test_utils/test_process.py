"""Tests for subprocess utilities."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.utils.process import CommandError, run, run_silent, run_with_transcript


class TestRun:
    def test_success(self) -> None:
        result = run(["echo", "hello"])
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_failure_raises(self) -> None:
        with pytest.raises(CommandError) as exc_info:
            run(["false"])
        assert exc_info.value.returncode != 0

    def test_failure_no_check(self) -> None:
        result = run(["false"], check=False)
        assert result.returncode != 0

    def test_command_not_found(self) -> None:
        with pytest.raises(CommandError) as exc_info:
            run(["nonexistent_command_xyz"])
        assert exc_info.value.returncode == 127


class TestRunDebugLog:
    def test_debug_log_omits_full_command(self) -> None:
        """The per-call debug log must not carry full args either — every run()

        call logs it, including headless AI invocations that embed prompt text
        as command-line arguments (#366 review, same gap as the timeout log).
        """
        secret_arg = "DIFF CONTENTS: super-secret-issue-body"
        with (
            patch("wade.utils.process.subprocess.run") as mock_run,
            patch("wade.utils.process.logger") as mock_logger,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            run(["claude", secret_arg])

        debug_call = mock_logger.debug.call_args
        assert debug_call.args[0] == "subprocess.run"
        assert "command" not in debug_call.kwargs
        assert debug_call.kwargs["executable"] == "claude"
        assert debug_call.kwargs["argument_count"] == 1
        assert secret_arg not in str(debug_call)


class TestRunTimeoutPartialOutput:
    """On timeout, run() preserves partial output as decoded str (#366)."""

    def test_timeout_reattaches_decoded_partial_stdout(self) -> None:
        """TimeoutExpired.stdout is bytes even under text=True — run() decodes it."""
        with patch("wade.utils.process.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["claude"], timeout=5, output=b"partial output", stderr=b"partial err"
            )
            with pytest.raises(subprocess.TimeoutExpired) as exc_info:
                run(["claude"], timeout=5)
        assert exc_info.value.stdout == "partial output"
        assert isinstance(exc_info.value.stdout, str)
        assert exc_info.value.stderr == "partial err"
        assert isinstance(exc_info.value.stderr, str)

    def test_timeout_leaves_str_output_untouched(self) -> None:
        """A str/None stdout is passed through unchanged (no double-decode)."""
        with patch("wade.utils.process.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["x"], timeout=5, output="already text", stderr=None
            )
            with pytest.raises(subprocess.TimeoutExpired) as exc_info:
                run(["x"], timeout=5)
        assert exc_info.value.stdout == "already text"
        assert exc_info.value.stderr is None

    def test_timeout_invalid_utf8_uses_replacement(self) -> None:
        """Undecodable bytes decode with errors='replace' rather than crashing."""
        with patch("wade.utils.process.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["x"], timeout=5, output=b"ok\xff", stderr=None
            )
            with pytest.raises(subprocess.TimeoutExpired) as exc_info:
                run(["x"], timeout=5)
        assert exc_info.value.stdout == "ok�"

    def test_timeout_log_omits_full_command(self) -> None:
        """The timeout log must not carry full args — they can embed prompt

        text (diffs, issue bodies, user input) that headless AI delegation
        passes as command-line arguments, which would otherwise land in
        production error logs (#366 review).
        """
        secret_arg = "DIFF CONTENTS: super-secret-issue-body"
        with (
            patch("wade.utils.process.subprocess.run") as mock_run,
            patch("wade.utils.process.logger") as mock_logger,
        ):
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["claude", secret_arg], timeout=5, output=b"partial"
            )
            with pytest.raises(subprocess.TimeoutExpired):
                run(["claude", secret_arg], timeout=5)

        error_call = mock_logger.error.call_args
        assert error_call.args[0] == "subprocess.timeout"
        assert "command" not in error_call.kwargs
        assert error_call.kwargs["executable"] == "claude"
        assert error_call.kwargs["argument_count"] == 1
        assert secret_arg not in str(error_call)


class TestRunRetries:
    def test_retries_on_failure_then_succeeds(self) -> None:
        """Retries after failure and returns result on eventual success."""
        with (
            patch("wade.utils.process.subprocess.run") as mock_run,
            patch("wade.utils.process.time.sleep") as mock_sleep,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1, stderr="TLS handshake timeout", stdout=""),
                MagicMock(returncode=0, stderr="", stdout="ok"),
            ]
            result = run(["gh", "issue", "edit", "1"], retries=2)
        assert result.returncode == 0
        assert mock_run.call_count == 2
        mock_sleep.assert_called_once_with(1)

    def test_raises_after_all_retries_exhausted(self) -> None:
        """Raises CommandError after all retries fail."""
        with (
            patch("wade.utils.process.subprocess.run") as mock_run,
            patch("wade.utils.process.time.sleep"),
        ):
            mock_run.return_value = MagicMock(returncode=1, stderr="network error", stdout="")
            with pytest.raises(CommandError):
                run(["gh", "issue", "edit", "1"], retries=2)
        assert mock_run.call_count == 3  # 1 initial + 2 retries

    def test_no_retry_without_retries_param(self) -> None:
        """Default behavior (retries=0) raises immediately on failure."""
        with patch("wade.utils.process.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="error", stdout="")
            with pytest.raises(CommandError):
                run(["false"])
        assert mock_run.call_count == 1


class TestRunSilent:
    def test_success(self) -> None:
        assert run_silent(["true"]) is True

    def test_failure(self) -> None:
        assert run_silent(["false"]) is False


class TestRunWithTranscript:
    def test_no_transcript_path_runs_cmd_directly(self, tmp_path: Path) -> None:
        """When transcript_path is None, run the command without script."""
        with patch("wade.utils.process.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = run_with_transcript(["echo", "hi"], transcript_path=None)
        assert result == 0
        mock_run.assert_called_once_with(["echo", "hi"], cwd=None)

    def test_script_not_found_falls_back(self, tmp_path: Path) -> None:
        """When `script` binary is missing, fall back to plain subprocess.run."""
        transcript = tmp_path / ".transcript"
        with (
            patch("wade.utils.process.shutil.which", return_value=None),
            patch("wade.utils.process.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = run_with_transcript(["echo", "hi"], transcript_path=transcript)
        assert result == 0
        mock_run.assert_called_once_with(["echo", "hi"], cwd=None)

    def test_gnu_script_linux_syntax(self, tmp_path: Path) -> None:
        """When script --version succeeds (GNU), use: script -q -c 'cmd' transcript."""
        transcript = tmp_path / ".transcript"
        with (
            patch("wade.utils.process.shutil.which", return_value="/usr/bin/script"),
            patch("wade.utils.process.subprocess.run") as mock_run,
        ):
            # First call: script --version (returncode=0 → GNU)
            # Second call: actual script invocation
            mock_run.side_effect = [
                MagicMock(returncode=0),  # script --version
                MagicMock(returncode=0),  # script -q -c ... transcript
            ]
            result = run_with_transcript(
                ["claude", "--permission-mode", "plan"],
                transcript_path=transcript,
            )
        assert result == 0
        actual_cmd = mock_run.call_args[0][0]
        assert actual_cmd[0] == "script"
        assert actual_cmd[1] == "-q"
        assert actual_cmd[2] == "-e"
        assert actual_cmd[3] == "-c"
        # The quoted command string should contain all parts
        assert "claude" in actual_cmd[4]
        assert "--permission-mode" in actual_cmd[4]
        assert actual_cmd[5] == str(transcript)

    def test_bsd_script_macos_syntax(self, tmp_path: Path) -> None:
        """When script --version fails (BSD), use: script -q transcript cmd..."""
        transcript = tmp_path / ".transcript"
        with (
            patch("wade.utils.process.shutil.which", return_value="/usr/bin/script"),
            patch("wade.utils.process.subprocess.run") as mock_run,
        ):
            # First call: script --version (returncode=1 → BSD)
            # Second call: actual script invocation
            mock_run.side_effect = [
                MagicMock(returncode=1),  # script --version (BSD returns non-zero)
                MagicMock(returncode=0),  # script -q transcript cmd...
            ]
            result = run_with_transcript(
                ["claude", "--permission-mode", "plan"],
                transcript_path=transcript,
            )
        assert result == 0
        actual_cmd = mock_run.call_args[0][0]
        assert actual_cmd == [
            "script",
            "-q",
            str(transcript),
            "claude",
            "--permission-mode",
            "plan",
        ]  # BSD: script -q transcript cmd...

    def test_cwd_is_passed_through(self, tmp_path: Path) -> None:
        """cwd is forwarded to the subprocess call."""
        cwd = tmp_path / "work"
        cwd.mkdir()
        with (
            patch("wade.utils.process.shutil.which", return_value=None),
            patch("wade.utils.process.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            run_with_transcript(["true"], transcript_path=None, cwd=cwd)
        mock_run.assert_called_once_with(["true"], cwd=cwd)

    def test_returns_script_exit_code(self, tmp_path: Path) -> None:
        """The exit code from the script invocation is returned."""
        transcript = tmp_path / ".transcript"
        with (
            patch("wade.utils.process.shutil.which", return_value="/usr/bin/script"),
            patch("wade.utils.process.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1),  # BSD script --version
                MagicMock(returncode=42),  # actual run
            ]
            result = run_with_transcript(["somecommand"], transcript_path=transcript)
        assert result == 42
