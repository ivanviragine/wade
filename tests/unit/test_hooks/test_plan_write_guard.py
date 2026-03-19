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
        ],
    )
    def test_blocked_codebase_files(self, file_path: str) -> None:
        data = json.dumps({"tool_input": {"file_path": file_path}})
        result = _run_guard(data)
        assert result.returncode == 2
        assert "BLOCKED" in result.stderr
        # Verify stdout JSON has deny fields
        stdout_json = json.loads(result.stdout)
        assert stdout_json["permission"] == "deny"
        assert stdout_json["permissionDecision"] == "deny"


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
