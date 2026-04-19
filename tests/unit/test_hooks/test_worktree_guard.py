"""Tests for worktree_guard.py standalone guard script."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _get_guard_script_source() -> Path:
    spec = importlib.util.find_spec("wade.hooks.worktree_guard")
    if spec and spec.origin:
        return Path(spec.origin)
    # Fallback for development
    return Path(__file__).resolve().parents[3] / "src" / "wade" / "hooks" / "worktree_guard.py"


def _install_guard(worktree_root: Path) -> Path:
    """Copy worktree_guard.py to .claude/hooks/ as it would be in real use.

    The installed location makes Path(__file__).parent.parent.parent == worktree_root.
    """
    hooks_dir = worktree_root / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / "worktree_guard.py"
    shutil.copy2(_get_guard_script_source(), dest)
    return dest


def _run_guard(
    script: Path, stdin_data: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the guard script with the given stdin and return the result."""
    return subprocess.run(
        [sys.executable, str(script)],
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=str(cwd) if cwd else None,
    )


class TestInsideWorktree:
    """Paths inside the worktree root should be allowed (exit 0)."""

    def test_absolute_path_inside(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        inside = tmp_path / "src" / "foo.py"
        data = json.dumps({"tool_input": {"file_path": str(inside)}})
        result = _run_guard(guard, data)
        assert result.returncode == 0

    def test_absolute_path_at_worktree_root(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        inside = tmp_path / "README.md"
        data = json.dumps({"tool_input": {"file_path": str(inside)}})
        result = _run_guard(guard, data)
        assert result.returncode == 0

    def test_relative_path_inside(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"tool_input": {"file_path": "src/foo.py"}})
        result = _run_guard(guard, data, cwd=tmp_path)
        assert result.returncode == 0

    def test_relative_path_nested(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"tool_input": {"file_path": "deep/nested/dir/file.txt"}})
        result = _run_guard(guard, data, cwd=tmp_path)
        assert result.returncode == 0


class TestOutsideWorktree:
    """Paths outside the worktree root should be blocked (exit 2)."""

    def test_absolute_path_outside(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        outside = "/tmp/some_other_path/file.py"
        data = json.dumps({"tool_input": {"file_path": outside}})
        result = _run_guard(guard, data)
        assert result.returncode == 2
        assert "BLOCKED" in result.stderr

    def test_deny_output_format(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        outside = "/tmp/some_other_path/file.py"
        data = json.dumps({"tool_input": {"file_path": outside}})
        result = _run_guard(guard, data)
        assert result.returncode == 2
        # Verify structured JSON output
        stdout_json = json.loads(result.stdout)
        hook_output = stdout_json["hookSpecificOutput"]
        assert hook_output["permissionDecision"] == "block"
        assert hook_output["hookEventName"] == "PreToolUse"
        assert "BLOCKED" in hook_output["permissionDecisionReason"]
        assert stdout_json["permission"] == "deny"
        assert stdout_json["permissionDecision"] == "deny"
        assert stdout_json["decision"] == "block"
        assert "BLOCKED" in stdout_json["reason"]

    def test_deny_message_contains_worktree_root(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        outside = "/tmp/some_other_path/file.py"
        data = json.dumps({"tool_input": {"file_path": outside}})
        result = _run_guard(guard, data)
        assert str(tmp_path.resolve()) in result.stderr

    @pytest.mark.parametrize(
        "outside_path",
        [
            "/etc/passwd",
            "/usr/local/bin/wade",
            "/home/user/other_project/file.py",
        ],
    )
    def test_various_outside_paths(self, tmp_path: Path, outside_path: str) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"tool_input": {"file_path": outside_path}})
        result = _run_guard(guard, data)
        assert result.returncode == 2


class TestToolStdinFormats:
    """Test all supported AI tool stdin formats."""

    def test_claude_format(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"tool_input": {"file_path": "/etc/passwd"}})
        result = _run_guard(guard, data)
        assert result.returncode == 2

    def test_cursor_file_path_key(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"tool_input": {"filePath": "/etc/passwd"}})
        result = _run_guard(guard, data)
        assert result.returncode == 2

    def test_cursor_tool_input_key(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"toolInput": {"file_path": "/etc/passwd"}})
        result = _run_guard(guard, data)
        assert result.returncode == 2

    def test_copilot_format(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        args = json.dumps({"file": "/etc/passwd"})
        data = json.dumps({"toolArgs": args})
        result = _run_guard(guard, data)
        assert result.returncode == 2

    def test_copilot_path_key(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        args = json.dumps({"path": "/etc/passwd"})
        data = json.dumps({"toolArgs": args})
        result = _run_guard(guard, data)
        assert result.returncode == 2

    def test_gemini_path_key(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"tool_input": {"path": "/etc/passwd"}})
        result = _run_guard(guard, data)
        assert result.returncode == 2

    def test_inside_via_copilot_format(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        args = json.dumps({"file": str(tmp_path / "src" / "foo.py")})
        data = json.dumps({"toolArgs": args})
        result = _run_guard(guard, data)
        assert result.returncode == 0


class TestToolNameFiltering:
    """Non-write tools should be allowed regardless of file path."""

    def test_read_tool_allowed(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"toolName": "read", "tool_input": {"file_path": "/etc/passwd"}})
        result = _run_guard(guard, data)
        assert result.returncode == 0

    def test_view_tool_allowed(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"toolName": "view", "tool_input": {"file_path": "/etc/passwd"}})
        result = _run_guard(guard, data)
        assert result.returncode == 0

    def test_write_tool_blocked_outside(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"toolName": "write", "tool_input": {"file_path": "/etc/passwd"}})
        result = _run_guard(guard, data)
        assert result.returncode == 2

    def test_edit_tool_blocked_outside(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"toolName": "edit", "tool_input": {"file_path": "/etc/passwd"}})
        result = _run_guard(guard, data)
        assert result.returncode == 2

    def test_no_tool_name_checks_path(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"tool_input": {"file_path": "/etc/passwd"}})
        result = _run_guard(guard, data)
        assert result.returncode == 2

    def test_write_tool_allowed_inside(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps(
            {"toolName": "write", "tool_input": {"file_path": str(tmp_path / "foo.py")}}
        )
        result = _run_guard(guard, data)
        assert result.returncode == 0


class TestFailOpen:
    """Malformed/missing input should fail open (exit 0)."""

    def test_empty_stdin(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        result = _run_guard(guard, "")
        assert result.returncode == 0

    def test_invalid_json(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        result = _run_guard(guard, "not json at all")
        assert result.returncode == 0

    def test_non_dict_json(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        result = _run_guard(guard, json.dumps([1, 2, 3]))
        assert result.returncode == 0

    def test_no_file_path_in_input(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"tool_input": {"content": "hello"}})
        result = _run_guard(guard, data)
        assert result.returncode == 0

    def test_copilot_malformed_toolargs(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        data = json.dumps({"toolArgs": "not valid json"})
        result = _run_guard(guard, data)
        assert result.returncode == 0

    def test_empty_dict(self, tmp_path: Path) -> None:
        guard = _install_guard(tmp_path)
        result = _run_guard(guard, json.dumps({}))
        assert result.returncode == 0
