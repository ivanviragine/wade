"""Tests for subprocess utilities."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghaiw.utils.process import CommandError, run, run_silent, run_with_transcript


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


class TestRunSilent:
    def test_success(self) -> None:
        assert run_silent(["true"]) is True

    def test_failure(self) -> None:
        assert run_silent(["false"]) is False


class TestRunWithTranscript:
    def test_no_transcript_path_runs_cmd_directly(self, tmp_path: Path) -> None:
        """When transcript_path is None, run the command without script."""
        with patch("ghaiw.utils.process.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = run_with_transcript(["echo", "hi"], transcript_path=None)
        assert result == 0
        mock_run.assert_called_once_with(["echo", "hi"], cwd=None)

    def test_script_not_found_falls_back(self, tmp_path: Path) -> None:
        """When `script` binary is missing, fall back to plain subprocess.run."""
        transcript = tmp_path / ".transcript"
        with (
            patch("ghaiw.utils.process.shutil.which", return_value=None),
            patch("ghaiw.utils.process.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = run_with_transcript(["echo", "hi"], transcript_path=transcript)
        assert result == 0
        mock_run.assert_called_once_with(["echo", "hi"], cwd=None)

    def test_gnu_script_linux_syntax(self, tmp_path: Path) -> None:
        """When script --version succeeds (GNU), use: script -q -c 'cmd' transcript."""
        transcript = tmp_path / ".transcript"
        with (
            patch("ghaiw.utils.process.shutil.which", return_value="/usr/bin/script"),
            patch("ghaiw.utils.process.subprocess.run") as mock_run,
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
        assert actual_cmd[2] == "-c"
        # The quoted command string should contain all parts
        assert "claude" in actual_cmd[3]
        assert "--permission-mode" in actual_cmd[3]
        assert actual_cmd[4] == str(transcript)

    def test_bsd_script_macos_syntax(self, tmp_path: Path) -> None:
        """When script --version fails (BSD), use: script -q transcript cmd..."""
        transcript = tmp_path / ".transcript"
        with (
            patch("ghaiw.utils.process.shutil.which", return_value="/usr/bin/script"),
            patch("ghaiw.utils.process.subprocess.run") as mock_run,
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
            patch("ghaiw.utils.process.shutil.which", return_value=None),
            patch("ghaiw.utils.process.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            run_with_transcript(["true"], transcript_path=None, cwd=cwd)
        mock_run.assert_called_once_with(["true"], cwd=cwd)

    def test_returns_script_exit_code(self, tmp_path: Path) -> None:
        """The exit code from the script invocation is returned."""
        transcript = tmp_path / ".transcript"
        with (
            patch("ghaiw.utils.process.shutil.which", return_value="/usr/bin/script"),
            patch("ghaiw.utils.process.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1),  # BSD script --version
                MagicMock(returncode=42),  # actual run
            ]
            result = run_with_transcript(["somecommand"], transcript_path=transcript)
        assert result == 42
