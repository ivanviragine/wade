"""Tests for the #352 PostToolUse lint-feedback branch of ``wade-hook``.

Strictly fail-open: the linter runs file-scoped to the edited path, injects any
findings as ``additionalContext`` on context-capable tools, and NEVER blocks
(always exit 0). Driven through the lean ``wade.hooks.cli`` entry point as a real
subprocess, mirroring ``test_hook_cli.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _script(path: Path, body: str) -> str:
    path.write_text(body)
    path.chmod(0o755)
    return str(path)


def _fail_linter(tmp_path: Path) -> str:
    """A linter that prints a finding and exits non-zero."""
    return _script(
        tmp_path / "lint-fail.sh",
        '#!/usr/bin/env bash\necho "LINT-FINDING: $*"\nexit 1\n',
    )


def _pass_linter(tmp_path: Path) -> str:
    return _script(tmp_path / "lint-pass.sh", "#!/usr/bin/env bash\nexit 0\n")


def _run_ptu(
    tool: str,
    root: str,
    lint_cmd: str,
    stdin: str,
    *,
    timeout: int = 5,
    unscoped: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        "-m",
        "wade.hooks.cli",
        "post_tool_use",
        "--tool",
        tool,
        "--root",
        root,
        "--lint-cmd",
        lint_cmd,
        "--timeout",
        str(timeout),
    ]
    if unscoped:
        cmd.append("--unscoped")
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=30)


def _write_payload(wt: Path, filename: str = "edited.py", tool_name: str = "Write") -> str:
    (wt / filename).write_text("x = 1\n")
    return json.dumps(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
            "tool_input": {"file_path": str(wt / filename)},
        }
    )


class TestContextInjection:
    def test_claude_returns_findings_as_hook_specific_output(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        r = _run_ptu("claude", str(wt), _fail_linter(tmp_path), _write_payload(wt))
        assert r.returncode == 0
        payload = json.loads(r.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert "LINT-FINDING" in payload["hookSpecificOutput"]["additionalContext"]

    def test_codex_uses_hook_specific_output(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        r = _run_ptu("codex", str(wt), _fail_linter(tmp_path), _write_payload(wt))
        assert r.returncode == 0
        assert "additionalContext" in json.loads(r.stdout)["hookSpecificOutput"]

    def test_copilot_uses_flat_additional_context(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        r = _run_ptu("copilot", str(wt), _fail_linter(tmp_path), _write_payload(wt))
        assert r.returncode == 0
        payload = json.loads(r.stdout)
        assert "LINT-FINDING" in payload["additionalContext"]
        assert "hookSpecificOutput" not in payload

    def test_cursor_uses_snake_case_additional_context(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        r = _run_ptu("cursor", str(wt), _fail_linter(tmp_path), _write_payload(wt))
        assert r.returncode == 0
        assert "LINT-FINDING" in json.loads(r.stdout)["additional_context"]

    def test_antigravity_cli_noops(self, tmp_path: Path) -> None:
        # agy's DECISION dialect has no context channel — must degrade to a no-op
        # proceed ({}), never a per-edit subprocess that discards the result.
        wt = tmp_path / "wt"
        wt.mkdir()
        r = _run_ptu("antigravity-cli", str(wt), _fail_linter(tmp_path), _write_payload(wt))
        assert r.returncode == 0
        assert json.loads(r.stdout) == {}


class TestNoOpConditions:
    def test_clean_lint_is_noop(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        r = _run_ptu("claude", str(wt), _pass_linter(tmp_path), _write_payload(wt))
        assert r.returncode == 0
        assert r.stdout == ""

    def test_missing_file_path_is_noop(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Write"})
        r = _run_ptu("claude", str(wt), _fail_linter(tmp_path), payload)
        assert r.returncode == 0
        assert r.stdout == ""

    def test_non_write_tool_is_noop(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        payload = _write_payload(wt, tool_name="Read")
        r = _run_ptu("claude", str(wt), _fail_linter(tmp_path), payload)
        assert r.returncode == 0
        assert r.stdout == ""

    def test_path_outside_worktree_is_noop(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        payload = json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": "/etc/passwd"},
            }
        )
        r = _run_ptu("claude", str(wt), _fail_linter(tmp_path), payload)
        assert r.returncode == 0
        assert r.stdout == ""

    def test_empty_stdin_is_noop(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        r = _run_ptu("claude", str(wt), _fail_linter(tmp_path), "")
        assert r.returncode == 0
        assert r.stdout == ""


class TestNeverBlocksOrHangs:
    def test_skips_on_timeout_without_hanging(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        slow = _script(tmp_path / "slow.sh", "#!/usr/bin/env bash\nsleep 30\nexit 1\n")
        # timeout=1: the outer subprocess.run(timeout=30) must return well before,
        # proving the linter was abandoned rather than hung.
        r = _run_ptu("claude", str(wt), slow, _write_payload(wt), timeout=1)
        assert r.returncode == 0
        assert r.stdout == ""

    def test_lint_failure_still_exits_zero(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        r = _run_ptu("claude", str(wt), _fail_linter(tmp_path), _write_payload(wt))
        # Findings are surfaced, but the process exit code never blocks.
        assert r.returncode == 0

    def test_malformed_lint_cmd_is_noop_not_crash(self, tmp_path: Path) -> None:
        # An unbalanced-quote lint_cmd passes config validation (non-empty string)
        # but breaks shlex.split — the hook must fail open, not crash non-zero.
        wt = tmp_path / "wt"
        wt.mkdir()
        r = _run_ptu("claude", str(wt), "ruff 'check", _write_payload(wt))
        assert r.returncode == 0
        assert r.stdout == ""


class TestScoping:
    def test_scoped_appends_edited_path(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        # Linter echoes its args; the edited path must be appended as one arg.
        echo = _script(tmp_path / "echo.sh", '#!/usr/bin/env bash\necho "$@"\nexit 1\n')
        r = _run_ptu("claude", str(wt), echo, _write_payload(wt, "target.py"))
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert str(wt / "target.py") in ctx

    def test_unscoped_does_not_append_path(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        echo = _script(tmp_path / "echo.sh", '#!/usr/bin/env bash\necho "args=[$*]"\nexit 1\n')
        r = _run_ptu("claude", str(wt), echo, _write_payload(wt), unscoped=True)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "args=[]" in ctx


class TestInjectionSafety:
    def test_tool_emitted_path_is_not_shell_interpreted(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        # A pass-through linter (exit 0). The tool-emitted file_path carries shell
        # metacharacters; because the argv is built with shell=False, the embedded
        # `touch` must NEVER run.
        pwned = wt / "PWNED.txt"
        payload = json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": f"{wt}/ok.py; touch {pwned}"},
            }
        )
        r = _run_ptu("claude", str(wt), _pass_linter(tmp_path), payload)
        assert r.returncode == 0
        assert not pwned.exists()
