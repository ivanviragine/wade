"""Tests for plan_write_guard.py standalone guard script."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


def _get_guard_script_path() -> Path:
    spec = importlib.util.find_spec("wade.hooks.plan_write_guard")
    if spec and spec.origin:
        return Path(spec.origin)
    # Fallback for development
    return Path(__file__).resolve().parents[3] / "src" / "wade" / "hooks" / "plan_write_guard.py"


GUARD_SCRIPT = _get_guard_script_path()


def _run_guard(stdin_data: str) -> subprocess.CompletedProcess[str]:
    """Run the guard script with the given stdin and return the result."""
    return subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestAllowedFiles:
    """Files that should be allowed (exit 0)."""

    @pytest.mark.parametrize(
        "file_path",
        [
            "PLAN.md",
            "/tmp/worktree/PLAN.md",
            "PLAN-architecture.md",
            "/tmp/worktree/PLAN-deep-dive.md",
            "prompt.txt",
            "/tmp/worktree/prompt.txt",
            ".transcript",
            "/tmp/worktree/.transcript",
            ".commit-msg",
            "PR-SUMMARY.md",
            ".claude/plans/some-plan.md",
            "/tmp/worktree/.claude/plans/design.md",
            ".claude\\plans\\windows-path.md",
            ".wade/plans/some-plan.md",
            "/tmp/worktree/.wade/plans/design.md",
            ".wade\\plans\\windows-path.md",
        ],
    )
    def test_allowed_basenames(self, file_path: str) -> None:
        data = json.dumps({"tool_input": {"file_path": file_path}})
        result = _run_guard(data)
        assert result.returncode == 0

    def test_allowed_claude_plans_subdir(self) -> None:
        data = json.dumps({"tool_input": {"file_path": ".claude/plans/sub/nested.md"}})
        result = _run_guard(data)
        assert result.returncode == 0


class TestBlockedFiles:
    """Files that should be blocked (exit 2)."""

    @pytest.mark.parametrize(
        "file_path",
        [
            "src/foo.py",
            "/tmp/worktree/src/wade/services/plan_service.py",
            "README.md",
            "setup.py",
            "tests/test_something.py",
            ".gitignore",
            "pyproject.toml",
            ".claude/plans/../../src/foo.py",  # path traversal escape
            ".claude/plans/../../../etc/passwd",  # deeper traversal
        ],
    )
    def test_blocked_codebase_files(self, file_path: str) -> None:
        data = json.dumps({"tool_input": {"file_path": file_path}})
        result = _run_guard(data)
        assert result.returncode == 2
        assert "BLOCKED" in result.stderr
        # Verify stdout JSON has deny fields for all supported tools
        stdout_json = json.loads(result.stdout)
        # Claude Code format (nested hookSpecificOutput)
        hook_output = stdout_json["hookSpecificOutput"]
        assert hook_output["permissionDecision"] == "block"
        assert hook_output["hookEventName"] == "PreToolUse"
        assert "BLOCKED" in hook_output["permissionDecisionReason"]
        # Copilot/Cursor format (top-level)
        assert stdout_json["permission"] == "deny"
        assert stdout_json["permissionDecision"] == "deny"
        # Gemini/Claude Code top-level decision
        assert stdout_json["decision"] == "block"
        assert "BLOCKED" in stdout_json["reason"]


class TestToolStdinFormats:
    """Test all supported AI tool stdin formats."""

    def test_claude_format(self) -> None:
        data = json.dumps({"tool_input": {"file_path": "src/foo.py"}})
        result = _run_guard(data)
        assert result.returncode == 2

    def test_cursor_format_file_path_key(self) -> None:
        data = json.dumps({"tool_input": {"filePath": "src/foo.py"}})
        result = _run_guard(data)
        assert result.returncode == 2

    def test_cursor_format_tool_input_key(self) -> None:
        data = json.dumps({"toolInput": {"file_path": "src/foo.py"}})
        result = _run_guard(data)
        assert result.returncode == 2

    def test_copilot_format(self) -> None:
        args = json.dumps({"file": "src/foo.py"})
        data = json.dumps({"toolArgs": args})
        result = _run_guard(data)
        assert result.returncode == 2

    def test_copilot_format_path_key(self) -> None:
        args = json.dumps({"path": "src/foo.py"})
        data = json.dumps({"toolArgs": args})
        result = _run_guard(data)
        assert result.returncode == 2

    def test_gemini_format(self) -> None:
        data = json.dumps({"tool_input": {"path": "src/foo.py"}})
        result = _run_guard(data)
        assert result.returncode == 2

    def test_allowed_via_copilot_format(self) -> None:
        args = json.dumps({"file": "PLAN.md"})
        data = json.dumps({"toolArgs": args})
        result = _run_guard(data)
        assert result.returncode == 0


class TestToolNameFiltering:
    """Non-write tools should be allowed regardless of file path."""

    def test_read_tool_allowed(self) -> None:
        data = json.dumps({"toolName": "read", "tool_input": {"file_path": "src/foo.py"}})
        result = _run_guard(data)
        assert result.returncode == 0

    def test_view_tool_allowed(self) -> None:
        data = json.dumps({"toolName": "view", "tool_input": {"file_path": "src/foo.py"}})
        result = _run_guard(data)
        assert result.returncode == 0

    def test_write_tool_blocked(self) -> None:
        data = json.dumps({"toolName": "write", "tool_input": {"file_path": "src/foo.py"}})
        result = _run_guard(data)
        assert result.returncode == 2

    def test_edit_tool_blocked(self) -> None:
        data = json.dumps({"toolName": "edit", "tool_input": {"file_path": "src/foo.py"}})
        result = _run_guard(data)
        assert result.returncode == 2

    def test_no_tool_name_still_checks(self) -> None:
        """When toolName is absent, guard should still check file path."""
        data = json.dumps({"tool_input": {"file_path": "src/foo.py"}})
        result = _run_guard(data)
        assert result.returncode == 2

    def test_write_tool_allowed_for_plan_files(self) -> None:
        data = json.dumps({"toolName": "write", "tool_input": {"file_path": "PLAN.md"}})
        result = _run_guard(data)
        assert result.returncode == 0


class TestFailOpen:
    """Malformed/missing input should fail open (exit 0)."""

    def test_empty_stdin(self) -> None:
        result = _run_guard("")
        assert result.returncode == 0

    def test_invalid_json(self) -> None:
        result = _run_guard("not json at all")
        assert result.returncode == 0

    def test_non_dict_json(self) -> None:
        result = _run_guard(json.dumps([1, 2, 3]))
        assert result.returncode == 0

    def test_no_file_path_in_input(self) -> None:
        data = json.dumps({"tool_input": {"content": "hello"}})
        result = _run_guard(data)
        assert result.returncode == 0

    def test_copilot_malformed_toolargs(self) -> None:
        data = json.dumps({"toolArgs": "not valid json"})
        result = _run_guard(data)
        assert result.returncode == 0

    def test_empty_dict(self) -> None:
        result = _run_guard(json.dumps({}))
        assert result.returncode == 0


class TestFailClosed:
    """Unhandled exceptions and guard errors should fail closed (exit 2, not exit 0 or 1)."""

    def test_exception_in_main_exits_2_not_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If sys.stdin.read() raises an exception, the outer wrapper should catch it and exit 2."""
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import Mock

        # Load the guard module fresh
        spec = importlib.util.spec_from_file_location("test_guard_main", GUARD_SCRIPT)
        assert spec and spec.loader
        guard_module = importlib.util.module_from_spec(spec)

        # Create a mock stdin that raises OSError (simulating I/O failure)
        mock_stdin = Mock()
        mock_stdin.read.side_effect = OSError("Simulated stdin I/O failure")

        # Patch sys.stdin before executing the module
        monkeypatch.setattr("sys.stdin", mock_stdin, raising=False)

        # Execute the module with patched stdin
        spec.loader.exec_module(guard_module)

        # Capture output and call _main_with_wrapper()
        stderr_capture = io.StringIO()
        stdout_capture = io.StringIO()

        with (
            redirect_stderr(stderr_capture),
            redirect_stdout(stdout_capture),
            pytest.raises(SystemExit) as exc_info,
        ):
            guard_module._main_with_wrapper()

        assert exc_info.value.code == 2, "Should exit 2 (fail-closed) on stdin I/O error"
        stderr_text = stderr_capture.getvalue()
        assert "Guard error" in stderr_text or "OSError" in stderr_text

        stdout_text = stdout_capture.getvalue()
        output_json = json.loads(stdout_text)
        assert output_json["hookSpecificOutput"]["permissionDecision"] == "block"
        assert output_json["permission"] == "deny"

    def test_guard_error_outputs_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Guard errors should output JSON in all tool formats when an exception occurs."""
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import Mock

        spec = importlib.util.spec_from_file_location("test_guard_error", GUARD_SCRIPT)
        assert spec and spec.loader
        guard_module = importlib.util.module_from_spec(spec)

        # Create mock stdin that raises a different error
        mock_stdin = Mock()
        mock_stdin.read.side_effect = RuntimeError("Unexpected guard processing error")

        monkeypatch.setattr("sys.stdin", mock_stdin, raising=False)
        spec.loader.exec_module(guard_module)

        stderr_capture = io.StringIO()
        stdout_capture = io.StringIO()

        with (
            redirect_stderr(stderr_capture),
            redirect_stdout(stdout_capture),
            pytest.raises(SystemExit) as exc_info,
        ):
            guard_module._main_with_wrapper()

        assert exc_info.value.code == 2
        stdout_text = stdout_capture.getvalue()
        stdout_json = json.loads(stdout_text)

        # Verify stdout is valid JSON with deny fields for all tool formats
        assert stdout_json["hookSpecificOutput"]["permissionDecision"] == "block"
        assert stdout_json["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert stdout_json["permission"] == "deny"
        assert stdout_json["permissionDecision"] == "deny"
        assert stdout_json["decision"] == "block"
