"""End-to-end tests for the ``wade hook`` guard entry point (real subprocess).

Exercised through the ``wade hook`` Typer alias; :class:`TestLeanEntryParity`
additionally drives the dedicated lean ``wade-hook`` console entry point
(``wade.hooks.cli``) to prove both paths behave identically.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

WT = "/repo/wt"


def _run(event: str, guard: str, tool: str, stdin: str, root: str | None = WT):
    cmd = [sys.executable, "-m", "wade", "hook", event, "--guard", guard, "--tool", tool]
    if root is not None:
        cmd += ["--root", root]
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


def _run_lean(event: str, guard: str, tool: str, stdin: str, root: str | None = WT):
    """Invoke the dedicated ``wade-hook`` entry point (``wade.hooks.cli``)."""
    cmd = [sys.executable, "-m", "wade.hooks.cli", event, "--guard", guard, "--tool", tool]
    if root is not None:
        cmd += ["--root", root]
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


class TestWorktreeGuardCLI:
    def test_inside_allows_exit_zero_clean_stdout(self) -> None:
        r = _run(
            "pre_tool_use",
            "worktree",
            "claude",
            json.dumps({"tool_name": "Write", "tool_input": {"file_path": f"{WT}/a.py"}}),
        )
        assert r.returncode == 0
        assert r.stdout == ""

    def test_outside_denies_claude_dialect(self) -> None:
        r = _run(
            "pre_tool_use",
            "worktree",
            "claude",
            json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}}),
        )
        assert r.returncode == 2
        payload = json.loads(r.stdout)
        assert list(payload.keys()) == ["hookSpecificOutput"]
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    def test_outside_denies_cursor_permission_dialect(self) -> None:
        r = _run(
            "pre_tool_use",
            "worktree",
            "cursor",
            json.dumps({"toolName": "Write", "toolInput": {"filePath": "/etc/passwd"}}),
        )
        assert r.returncode == 2
        assert json.loads(r.stdout)["permission"] == "deny"

    def test_outside_denies_antigravity_decision_dialect(self) -> None:
        r = _run(
            "pre_tool_use",
            "worktree",
            "antigravity-cli",
            json.dumps({"tool_name": "Write", "tool_input": {"path": "/etc/passwd"}}),
        )
        assert r.returncode == 2
        # DECISION dialect: agy blocks via a top-level {"decision": "deny"}.
        assert json.loads(r.stdout)["decision"] == "deny"
        assert "BLOCKED" in r.stderr


class TestPlanGuardCLI:
    def _plan(self, file_path: str, root: str):
        stdin = json.dumps({"tool_name": "Write", "tool_input": {"file_path": file_path}})
        return _run("pre_tool_use", "plan", "claude", stdin, root=root)

    def test_source_inside_worktree_denied(self, tmp_path: Path) -> None:
        r = self._plan(str(tmp_path / "src" / "foo.py"), str(tmp_path))
        assert r.returncode == 2
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_plan_md_inside_worktree_allowed(self, tmp_path: Path) -> None:
        r = self._plan(str(tmp_path / "PLAN.md"), str(tmp_path))
        assert r.returncode == 0
        assert r.stdout == ""

    def test_artifact_named_outside_worktree_denied(self, tmp_path: Path) -> None:
        # /etc/PLAN.md has an allowed basename but lives outside the worktree —
        # containment (which plan mode also enforces) must block it.
        r = self._plan("/etc/PLAN.md", str(tmp_path))
        assert r.returncode == 2


class TestGuardRobustness:
    def test_read_tool_allowed(self) -> None:
        r = _run(
            "pre_tool_use",
            "worktree",
            "claude",
            json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}),
        )
        assert r.returncode == 0

    def test_empty_stdin_fails_open(self) -> None:
        # An empty payload describes no write target — nothing can escape the
        # worktree, so allowing it is safe (and avoids trapping the agent).
        r = _run("pre_tool_use", "worktree", "claude", "")
        assert r.returncode == 0

    def test_malformed_json_fails_closed(self) -> None:
        # A non-empty payload that won't parse may hide a write target we failed
        # to read, so a write guard denies rather than defaulting to allow.
        r = _run("pre_tool_use", "worktree", "claude", "{not json")
        assert r.returncode == 2
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_malformed_json_stop_guard_still_fails_open(self, tmp_path: Path) -> None:
        # The Stop guard must never trap the agent, even on a garbage payload.
        r = _run("stop", "session-complete", "claude", "{not json", root=str(tmp_path))
        assert r.returncode == 0

    def test_pathless_write_fails_closed(self) -> None:
        # A recognized write tool with no target path can't be verified as
        # contained, so a write guard denies it.
        r = _run("pre_tool_use", "worktree", "claude", json.dumps({"tool_name": "Write"}))
        assert r.returncode == 2
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_notebook_edit_target_is_contained(self) -> None:
        # crossby >= 0.5.0 extracts notebook_path, so an in-worktree NotebookEdit
        # is allowed (and an out-of-worktree one would be denied).
        inside = json.dumps(
            {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": f"{WT}/nb.ipynb"}}
        )
        assert _run("pre_tool_use", "worktree", "claude", inside).returncode == 0
        outside = json.dumps(
            {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "/etc/nb.ipynb"}}
        )
        assert _run("pre_tool_use", "worktree", "claude", outside).returncode == 2

    def test_unknown_guard_fails_closed(self) -> None:
        # An unknown guard on a write event is a misconfiguration — the write
        # guard family fails CLOSED (deny) rather than silently allowing.
        r = _run(
            "pre_tool_use",
            "nonexistent",
            "claude",
            json.dumps({"tool_name": "Write", "tool_input": {"file_path": f"{WT}/a.py"}}),
        )
        assert r.returncode == 2
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_missing_root_fails_closed(self) -> None:
        # Without --root a worktree/plan guard cannot make a containment decision,
        # so it denies rather than defaulting to the CWD and possibly leaking.
        r = _run(
            "pre_tool_use",
            "worktree",
            "claude",
            json.dumps({"tool_name": "Write", "tool_input": {"file_path": f"{WT}/a.py"}}),
            root=None,
        )
        assert r.returncode == 2
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_unknown_tool_falls_back_to_universal_dialect(self) -> None:
        r = _run(
            "pre_tool_use",
            "worktree",
            "not-a-tool",
            json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}}),
        )
        assert r.returncode == 2
        # Falls back to hookSpecificOutput shape.
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestStopGuardCLI:
    def _run_stop(self, tool: str, stdin: str, root: str):
        return _run("stop", "session-complete", tool, stdin, root=root)

    def test_blocks_when_pr_summary_missing(self, tmp_path: Path) -> None:
        r = self._run_stop("claude", json.dumps({"stop_hook_active": False}), str(tmp_path))
        assert r.returncode == 0  # Stop blocks via the JSON decision, not exit code
        payload = json.loads(r.stdout)
        assert payload["decision"] == "block"
        assert "PR-SUMMARY.md" in payload["reason"]

    def test_cursor_uses_followup_message(self, tmp_path: Path) -> None:
        r = self._run_stop("cursor", json.dumps({"stop_hook_active": False}), str(tmp_path))
        assert r.returncode == 0
        assert "followup_message" in json.loads(r.stdout)

    def test_single_shot_allows(self, tmp_path: Path) -> None:
        r = self._run_stop("claude", json.dumps({"stop_hook_active": True}), str(tmp_path))
        assert r.returncode == 0
        # A Stop no-op emits the universal {"continue": true} (Codex rejects an
        # empty-stdout Stop hook), not blank output.
        assert json.loads(r.stdout) == {"continue": True}

    def test_missing_root_fails_open(self) -> None:
        # Without --root the Stop guard must fail open (no block), not fall back
        # to the CWD — inspecting the wrong dir could spuriously block completion.
        r = _run("stop", "session-complete", "claude", json.dumps({}), root=None)
        assert r.returncode == 0
        assert json.loads(r.stdout) == {"continue": True}

    def test_marker_makes_nudge_single_shot_across_tools(self, tmp_path: Path) -> None:
        # First Stop (no summary, no marker) blocks and writes the .wade marker...
        r1 = self._run_stop("claude", json.dumps({}), str(tmp_path))
        assert json.loads(r1.stdout)["decision"] == "block"
        assert (tmp_path / ".wade" / "stop-nudged").is_file()
        # ...so a second Stop is allowed even on a tool that never sends
        # stop_hook_active (Codex/Cursor) — the tool-agnostic single-shot.
        r2 = self._run_stop("codex", json.dumps({}), str(tmp_path))
        assert r2.returncode == 0
        assert json.loads(r2.stdout) == {"continue": True}

    def test_symlinked_wade_dir_not_written_through(self, tmp_path: Path) -> None:
        # If .wade is a symlink out of the worktree, the marker write must refuse
        # rather than truncate a file outside the worktree.
        wt = tmp_path / "wt"
        wt.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (wt / ".wade").symlink_to(outside)
        r = self._run_stop("claude", json.dumps({}), str(wt))
        assert json.loads(r.stdout)["decision"] == "block"  # still nudges
        assert not (outside / "stop-nudged").exists()  # but never wrote outside

    def test_allows_when_pr_summary_present(self, tmp_path: Path) -> None:
        (tmp_path / "PR-SUMMARY.md").write_text("done")
        r = self._run_stop("claude", json.dumps({"stop_hook_active": False}), str(tmp_path))
        assert r.returncode == 0
        assert json.loads(r.stdout) == {"continue": True}

    def test_codex_noop_emits_valid_json_not_empty(self, tmp_path: Path) -> None:
        # Codex rejects an empty-stdout Stop hook ("invalid stop hook JSON
        # output"), so a clean stop on Codex must emit {"continue": true}.
        (tmp_path / "PR-SUMMARY.md").write_text("done")
        r = self._run_stop("codex", json.dumps({}), str(tmp_path))
        assert r.returncode == 0
        assert r.stdout != ""
        assert json.loads(r.stdout) == {"continue": True}


class TestStopReadErrorFailsOpen:
    def test_unreadable_stdin_fails_open(self, monkeypatch, tmp_path: Path) -> None:
        # A stdin read error on the Stop guard must fail open, not block — the
        # guard evaluates in-process here so we can force the OSError.
        from wade.hooks import cli as hook_cli

        class _BadStdin:
            def read(self) -> str:
                raise OSError("stdin unreadable")

        monkeypatch.setattr(hook_cli.sys, "stdin", _BadStdin())
        em = hook_cli._run("stop", "session-complete", "claude", str(tmp_path))
        assert em.exit_code == 0
        assert json.loads(em.stdout) == {"continue": True}


class TestLeanEntryParity:
    """The dedicated ``wade-hook`` entry point must match the ``wade hook`` alias."""

    def test_inside_worktree_allows(self) -> None:
        stdin = json.dumps({"tool_name": "Write", "tool_input": {"file_path": f"{WT}/a.py"}})
        alias = _run("pre_tool_use", "worktree", "claude", stdin)
        lean = _run_lean("pre_tool_use", "worktree", "claude", stdin)
        assert alias.returncode == lean.returncode == 0
        assert alias.stdout == lean.stdout == ""

    def test_outside_worktree_denies_same_payload(self) -> None:
        stdin = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}})
        alias = _run("pre_tool_use", "worktree", "claude", stdin)
        lean = _run_lean("pre_tool_use", "worktree", "claude", stdin)
        assert alias.returncode == lean.returncode == 2
        assert json.loads(alias.stdout) == json.loads(lean.stdout)

    def test_cursor_permission_dialect_via_static_map(self) -> None:
        # The lean path resolves the dialect from its static map, not by importing
        # crossby adapters — the Cursor permission shape must still come through.
        stdin = json.dumps({"toolName": "Write", "toolInput": {"filePath": "/etc/passwd"}})
        lean = _run_lean("pre_tool_use", "worktree", "cursor", stdin)
        assert lean.returncode == 2
        assert json.loads(lean.stdout)["permission"] == "deny"

    def test_stop_block_via_lean_entry(self, tmp_path: Path) -> None:
        r = _run_lean(
            "stop",
            "session-complete",
            "claude",
            json.dumps({"stop_hook_active": False}),
            str(tmp_path),
        )
        assert r.returncode == 0
        assert json.loads(r.stdout)["decision"] == "block"
