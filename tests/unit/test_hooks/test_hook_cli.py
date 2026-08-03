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


class TestGuardNameValidation:
    """E.1 — an unrecognized guard must deny on writes but never trap a Stop.

    The write path used to gate its ``--root`` deny on the *known* guard set, so an
    unknown guard fell through to the "empty payload → allow" branch and exited 0:
    a hook that looked installed and enforced nothing.
    """

    def test_unknown_guard_denies_on_empty_stdin(self) -> None:
        r = _run_lean("pre_tool_use", "nonexistent", "claude", "")
        assert r.returncode == 2
        assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_unknown_guard_denies_without_root(self) -> None:
        r = _run_lean("pre_tool_use", "nonexistent", "claude", "", root=None)
        assert r.returncode == 2

    def test_unknown_guard_denies_on_whitespace_stdin(self) -> None:
        r = _run_lean("pre_tool_use", "nonexistent", "claude", "   \n  ")
        assert r.returncode == 2

    def test_unknown_guard_on_stop_fails_open(self, tmp_path: Path) -> None:
        """A guard typo must never leave an agent unable to end its turn."""
        r = _run_lean("stop", "nonexistent", "claude", "", str(tmp_path))
        assert r.returncode == 0
        assert json.loads(r.stdout) == {"continue": True}

    def test_unknown_guard_on_stop_fails_open_without_root(self) -> None:
        r = _run_lean("stop", "nonexistent", "claude", "", root=None)
        assert r.returncode == 0


class TestShellGuardCLI:
    """Part B — shell writes routed to ``shell_containment`` via ``ev.command``."""

    def _bash(self, command: str) -> str:
        return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})

    def test_shell_write_outside_worktree_denied(self) -> None:
        r = _run_lean("pre_tool_use", "worktree", "claude", self._bash("printf x > /etc/pwn"))
        assert r.returncode == 2
        assert "outside the worktree" in r.stdout

    def test_shell_write_inside_worktree_allowed(self) -> None:
        r = _run_lean("pre_tool_use", "worktree", "claude", self._bash(f"printf x > {WT}/a.txt"))
        assert r.returncode == 0

    def test_unparseable_shell_command_fails_closed(self) -> None:
        r = _run_lean("pre_tool_use", "worktree", "claude", self._bash('printf "unterminated'))
        assert r.returncode == 2

    def test_plan_mode_denies_shell_write_to_source(self) -> None:
        r = _run_lean("pre_tool_use", "plan", "claude", self._bash(f"printf x > {WT}/src/a.py"))
        assert r.returncode == 2

    def test_plan_mode_allows_shell_write_to_artifact(self) -> None:
        r = _run_lean("pre_tool_use", "plan", "claude", self._bash(f"printf x > {WT}/PLAN.md"))
        assert r.returncode == 0

    def test_cursor_shell_event_payload_shape(self) -> None:
        """Cursor's beforeShellExecution puts ``command`` at the payload top level."""
        r = _run_lean("pre_tool_use", "worktree", "cursor", json.dumps({"command": "cp a /etc/x"}))
        assert r.returncode == 2
        assert json.loads(r.stdout)["permission"] == "deny"

    def test_agy_shell_payload_shape(self) -> None:
        """agy nests shell args under ``toolCall.args``."""
        r = _run_lean(
            "pre_tool_use",
            "worktree",
            "antigravity-cli",
            json.dumps({"toolCall": {"name": "run_command", "args": {"command": "cp a /etc/x"}}}),
        )
        assert r.returncode == 2
        assert json.loads(r.stdout)["decision"] == "deny"


class TestPerToolDialectsMatchCrossby:
    """The static dialect maps are copies — assert they still match crossby."""

    def test_output_dialects_match_adapters(self) -> None:
        from crossby.ai_tools import AbstractAITool
        from crossby.models.ai import AIToolID

        from wade.hooks.cli import _TOOL_DIALECTS

        for tool_id, dialect in _TOOL_DIALECTS.items():
            caps = AbstractAITool.get(AIToolID(tool_id)).capabilities()
            assert caps.hook_output_dialect == dialect, tool_id

    def test_stop_dialects_match_adapters(self) -> None:
        from crossby.ai_tools import AbstractAITool
        from crossby.models.ai import AIToolID

        from wade.hooks.cli import _TOOL_STOP_DIALECTS

        for tool_id, dialect in _TOOL_STOP_DIALECTS.items():
            caps = AbstractAITool.get(AIToolID(tool_id)).capabilities()
            assert caps.hook_stop_dialect == dialect, tool_id

    def test_copilot_stop_hook_blocks(self, tmp_path: Path) -> None:
        """Copilot gained supports_stop_hook in 0.13 — its Stop must actually block."""
        r = _run_lean("stop", "session-complete", "copilot", "{}", str(tmp_path))
        assert r.returncode == 0
        assert json.loads(r.stdout)["decision"] == "block"


class TestChannelRoutingRegressions:
    """Guard-band regressions found in review of the shell-channel routing."""

    def test_write_tool_carrying_a_command_still_gets_the_file_guard(self) -> None:
        """A write tool with a command but no path must still deny.

        Routing on "has a command" alone skipped the file-path guard here, losing
        the "deny a write we cannot locate" invariant.
        """
        stdin = json.dumps({"tool_name": "Write", "tool_input": {"command": "ls"}})
        assert _run_lean("pre_tool_use", "worktree", "claude", stdin).returncode == 2

    def test_write_tool_with_blank_command_still_denies(self) -> None:
        stdin = json.dumps({"tool_name": "Write", "tool_input": {"command": "   "}})
        assert _run_lean("pre_tool_use", "worktree", "claude", stdin).returncode == 2

    def test_genuine_shell_call_skips_the_file_guard(self) -> None:
        stdin = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        assert _run_lean("pre_tool_use", "worktree", "claude", stdin).returncode == 0

    def test_cursor_shell_event_without_tool_name_skips_the_file_guard(self) -> None:
        """Cursor's beforeShellExecution sends a command and no tool_name at all."""
        assert (
            _run_lean(
                "pre_tool_use", "worktree", "cursor", json.dumps({"command": "ls"})
            ).returncode
            == 0
        )


class TestStopNeverTrapsOnUsageError:
    """A malformed *invocation* must not block session completion either."""

    def _raw(self, *args: str):
        return subprocess.run(
            [sys.executable, "-m", "wade.hooks.cli", *args],
            input="{}",
            capture_output=True,
            text=True,
        )

    def test_stop_with_word_split_root_fails_open(self) -> None:
        # A worktree path containing a space, word-split by the tool's runner.
        r = self._raw(
            "stop", "--guard", "session-complete", "--tool", "claude", "--root", "/a", "b"
        )
        assert r.returncode == 0

    def test_stop_missing_required_flag_fails_open(self) -> None:
        r = self._raw("stop", "--guard", "session-complete", "--root", WT)
        assert r.returncode == 0

    def test_pre_tool_use_usage_error_still_fails_closed(self) -> None:
        r = self._raw("pre_tool_use", "--guard", "worktree", "--root", WT)
        assert r.returncode == 2
