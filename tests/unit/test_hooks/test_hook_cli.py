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


def _make_ahead_repo(root: Path) -> None:
    """Init a git repo at *root* on a branch one commit ahead of main.

    The Stop guard now nudges only when the branch has authored work (commits
    ahead of base) and no current ``.wade/done@<HEAD>`` marker — so a real repo
    is required to exercise the block path (a plain temp dir has no git facts and
    fails open).
    """

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)

    root.mkdir(parents=True, exist_ok=True)
    git("init", "-b", "main")
    git("config", "user.email", "t@e.st")
    git("config", "user.name", "T")
    (root / "base.txt").write_text("base\n")
    git("add", "-A")
    git("commit", "-m", "base")
    git("checkout", "-b", "feat/1-x")
    (root / "work.txt").write_text("work\n")
    git("add", "-A")
    git("commit", "-m", "work")


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
        # DECISION dialect: agy reads the block from the top-level
        # {"decision": "deny"} body, not the exit code — so the process exits 0
        # even on a deny (crossby >= 0.24.4; see crossby#147). The decision field,
        # not the exit code, is the contract.
        assert r.returncode == 0
        assert json.loads(r.stdout)["decision"] == "deny"
        assert "BLOCKED" in r.stderr


class TestPlanGuardCLI:
    def _plan(self, file_path: str, root: str):
        stdin = json.dumps({"tool_name": "Write", "tool_input": {"file_path": file_path}})
        return _run("pre_tool_use", "plan", "claude", stdin, root=root)

    def test_source_inside_worktree_denied(self, tmp_path: Path) -> None:
        # pytest's tmp_path lives under the OS temp dir, which the scratch
        # exemption (#409) now matches by prefix — but the write target here is
        # a *child of the worktree root itself* (both derived from tmp_path), so
        # the plan-artifact exemption's root-awareness (_is_scratch_outside_worktree)
        # denies it regardless of the temp-prefix match. No $TMPDIR isolation
        # needed, unlike tests where the target and worktree root diverge.
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


class TestFilePathChannelScratchCLI:
    """File-path channel (Write/Edit tool calls) now shares the shell channel's
    always-allowed scratch exemption (#409) — a temp-dir write is allowed under
    both the ``worktree`` and ``plan`` guards, not just via a shell redirect.
    """

    def _write(self, path: str) -> str:
        return json.dumps({"tool_name": "Write", "tool_input": {"file_path": path}})

    def test_temp_write_allowed_worktree_mode(self) -> None:
        r = _run_lean("pre_tool_use", "worktree", "claude", self._write("/tmp/wade-scratch.log"))
        assert r.returncode == 0
        assert r.stdout == ""

    def test_temp_write_allowed_plan_mode(self) -> None:
        # The exact failure this issue fixes: a plan-mode Write tool call to a
        # system temp dir used to be denied as a non-artifact.
        r = _run_lean("pre_tool_use", "plan", "claude", self._write("/tmp/wade-scratch.log"))
        assert r.returncode == 0
        assert r.stdout == ""

    def test_dev_null_write_allowed_plan_mode(self) -> None:
        r = _run_lean("pre_tool_use", "plan", "claude", self._write("/dev/null"))
        assert r.returncode == 0

    def test_tmpfoo_sibling_still_denied_plan_mode(self) -> None:
        r = _run_lean("pre_tool_use", "plan", "claude", self._write("/tmpfoo/out"))
        assert r.returncode == 2

    def test_dev_shm_still_denied_worktree_mode(self) -> None:
        r = _run_lean("pre_tool_use", "worktree", "claude", self._write("/dev/shm/out"))
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

    def test_blocks_when_work_and_no_done_marker(self, tmp_path: Path) -> None:
        _make_ahead_repo(tmp_path)
        r = self._run_stop("claude", json.dumps({"stop_hook_active": False}), str(tmp_path))
        assert r.returncode == 0  # Stop blocks via the JSON decision, not exit code
        payload = json.loads(r.stdout)
        assert payload["decision"] == "block"
        assert "done" in payload["reason"]

    def test_cursor_uses_followup_message(self, tmp_path: Path) -> None:
        _make_ahead_repo(tmp_path)
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
        _make_ahead_repo(tmp_path)
        # First Stop (work ahead, no done marker) blocks and writes the .wade marker...
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
        _make_ahead_repo(wt)
        outside = tmp_path / "outside"
        outside.mkdir()
        (wt / ".wade").symlink_to(outside)
        r = self._run_stop("claude", json.dumps({}), str(wt))
        assert json.loads(r.stdout)["decision"] == "block"  # still nudges
        assert not (outside / "stop-nudged").exists()  # but never wrote outside

    def test_allows_when_no_commits_ahead(self, tmp_path: Path) -> None:
        # A repo sitting on main with no authored work ahead of base: nothing to
        # finalize, so the Stop guard allows (adopts #318's higher-signal check).
        def git(*args: str) -> None:
            subprocess.run(
                ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
            )

        git("init", "-b", "main")
        git("config", "user.email", "t@e.st")
        git("config", "user.name", "T")
        (tmp_path / "base.txt").write_text("base\n")
        git("add", "-A")
        git("commit", "-m", "base")
        r = self._run_stop("claude", json.dumps({"stop_hook_active": False}), str(tmp_path))
        assert r.returncode == 0
        assert json.loads(r.stdout) == {"continue": True}

    def test_codex_noop_emits_valid_json_not_empty(self, tmp_path: Path) -> None:
        # Codex rejects an empty-stdout Stop hook ("invalid stop hook JSON
        # output"), so a clean stop on Codex must emit {"continue": true}. A plain
        # temp dir has no git facts, so the guard fails open (allow) here.
        r = self._run_stop("codex", json.dumps({}), str(tmp_path))
        assert r.returncode == 0
        assert r.stdout != ""
        assert json.loads(r.stdout) == {"continue": True}


_VALID_PLAN = "# feat: add retry logic\n\n## Complexity\ncomplex\n\n## Tasks\n- Do it\n"


class TestPlanStopGuardCLI:
    """The ``plan-complete`` Stop guard — nudges when a plan session wrote no plan.

    Unlike ``session-complete`` it needs no git facts: it reads ``has_valid_plan``
    on ``<root>/.wade/plans``, so a plain temp dir exercises the block path.
    """

    def _run_stop(self, tool: str, stdin: str, root: str):
        return _run("stop", "plan-complete", tool, stdin, root=root)

    def _write_valid_plan(self, root: Path) -> None:
        plans = root / ".wade" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "PLAN.md").write_text(_VALID_PLAN)

    def test_blocks_when_no_valid_plan_and_writes_marker(self, tmp_path: Path) -> None:
        r = self._run_stop("claude", json.dumps({"stop_hook_active": False}), str(tmp_path))
        assert r.returncode == 0  # Stop blocks via the JSON decision, not exit code
        payload = json.loads(r.stdout)
        assert payload["decision"] == "block"
        assert "PLAN" in payload["reason"]
        assert (tmp_path / ".wade" / "stop-nudged").is_file()

    def test_allows_when_valid_plan_present(self, tmp_path: Path) -> None:
        self._write_valid_plan(tmp_path)
        r = self._run_stop("claude", json.dumps({"stop_hook_active": False}), str(tmp_path))
        assert r.returncode == 0
        assert json.loads(r.stdout) == {"continue": True}

    def test_plan_at_root_not_in_plans_dir_still_blocks(self, tmp_path: Path) -> None:
        # The plan dir is <root>/.wade/plans — a PLAN.md at the worktree root is
        # not where plan() writes, so it must not satisfy the guard.
        (tmp_path / "PLAN.md").write_text(_VALID_PLAN)
        r = self._run_stop("claude", json.dumps({}), str(tmp_path))
        assert json.loads(r.stdout)["decision"] == "block"

    def test_single_shot_across_tools(self, tmp_path: Path) -> None:
        # First Stop (no valid plan) blocks and writes the .wade marker...
        r1 = self._run_stop("claude", json.dumps({}), str(tmp_path))
        assert json.loads(r1.stdout)["decision"] == "block"
        assert (tmp_path / ".wade" / "stop-nudged").is_file()
        # ...so a second Stop is allowed even on a tool that never sends
        # stop_hook_active (Codex) — the tool-agnostic single-shot.
        r2 = self._run_stop("codex", json.dumps({}), str(tmp_path))
        assert r2.returncode == 0
        assert json.loads(r2.stdout) == {"continue": True}

    def test_missing_root_fails_open(self) -> None:
        r = _run("stop", "plan-complete", "claude", json.dumps({}), root=None)
        assert r.returncode == 0
        assert json.loads(r.stdout) == {"continue": True}

    def test_block_via_lean_entry(self, tmp_path: Path) -> None:
        r = _run_lean("stop", "plan-complete", "claude", json.dumps({}), str(tmp_path))
        assert r.returncode == 0
        assert json.loads(r.stdout)["decision"] == "block"


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
        _make_ahead_repo(tmp_path)
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
        # agy signals the block via the decision body, not the exit code (exit 0).
        assert r.returncode == 0
        assert json.loads(r.stdout)["decision"] == "deny"


class TestAntigravityPascalCaseDialect:
    """Pin the agy PascalCase tool-arg dialect at the wade-hook boundary.

    agy nests tool args under ``toolCall.args`` and names them in PascalCase
    (``TargetFile``/``CommandLine``), and it reads the guard verdict from a
    top-level ``{"decision": ...}`` body (never the exit code). Before crossby
    0.24.4 (crossby#147) those args went unread, so every write/command reached
    wade's guards as an empty event — denying an agy plan session its own
    ``PLAN.md`` and silently no-op'ing shell containment. The rest of the unit
    suite feeds already-normalized events, so these are the only tests that
    exercise the raw agy arg names; they guard against a dialect regression.
    """

    def _agy(self, guard: str, payload: dict[str, object]):
        return _run_lean("pre_tool_use", guard, "antigravity-cli", json.dumps(payload))

    def test_plan_write_targetfile_allowed(self) -> None:
        # agy's write arg is ``TargetFile`` — a plan-artifact write must be allowed.
        r = self._agy(
            "plan",
            {
                "toolCall": {
                    "name": "write_to_file",
                    "args": {"TargetFile": f"{WT}/.wade/plans/PLAN.md", "CodeContent": "x"},
                },
                "stepIdx": 1,
            },
        )
        assert r.returncode == 0
        assert json.loads(r.stdout)["decision"] == "allow"

    def test_shell_read_commandline_allowed(self) -> None:
        # agy's shell arg is ``CommandLine`` — a read command must be allowed and
        # emit an explicit {"decision": "allow"} (a bare {} is read as deny by agy).
        r = self._agy(
            "plan",
            {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "wade knowledge get --search tools", "Cwd": WT},
                },
                "stepIdx": 2,
            },
        )
        assert r.returncode == 0
        assert json.loads(r.stdout)["decision"] == "allow"

    def test_shell_escape_commandline_denied(self) -> None:
        # shell_containment must read ``CommandLine`` and block a redirect that
        # resolves outside the worktree (the A2 gap in crossby#147).
        r = self._agy(
            "plan",
            {
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "echo x > /tmp/../etc/escape", "Cwd": WT},
                },
                "stepIdx": 3,
            },
        )
        assert r.returncode == 0
        assert json.loads(r.stdout)["decision"] == "deny"
        assert "BLOCKED" in r.stderr

    def test_plan_write_source_file_denied(self) -> None:
        # In plan mode, an agy write to a source file must be denied by WADE's guard.
        r = self._agy(
            "plan",
            {
                "toolCall": {
                    "name": "write_to_file",
                    "args": {"TargetFile": f"{WT}/src/foo.py", "CodeContent": "x"},
                },
                "stepIdx": 4,
            },
        )
        assert r.returncode == 0
        assert json.loads(r.stdout)["decision"] == "deny"
        assert "BLOCKED" in r.stderr


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

    def test_every_installed_tool_has_both_dialects(self) -> None:
        """The reverse direction: a tool wade installs a hook for must be mapped.

        The two tests above only walk the wade maps, so a tool added to
        ``_hook_writers`` but forgotten here would silently take the fallback
        dialect and emit the wrong shape — passing tests, unenforced guard.
        """
        from wade.hooks.cli import _TOOL_DIALECTS, _TOOL_STOP_DIALECTS
        from wade.services.implementation_service.bootstrap import _hook_writers

        installed = {tool_id.value for tool_id, _ in _hook_writers()}
        assert installed <= set(_TOOL_DIALECTS), installed - set(_TOOL_DIALECTS)
        assert installed <= set(_TOOL_STOP_DIALECTS), installed - set(_TOOL_STOP_DIALECTS)

    def test_copilot_stop_hook_blocks(self, tmp_path: Path) -> None:
        """Copilot gained supports_stop_hook in 0.13 — its Stop must actually block."""
        _make_ahead_repo(tmp_path)
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

    def test_flag_value_named_stop_does_not_flip_write_guard_open(self) -> None:
        """`--root stop` must not look like a Stop event to the fail-open path.

        Scanning all of argv for "stop" turned a PreToolUse usage error from
        fail-closed into fail-open.
        """
        r = self._raw("pre_tool_use", "--guard", "worktree", "--root", "stop")
        assert r.returncode == 2

    def test_lint_cmd_value_named_stop_does_not_flip_write_guard_open(self) -> None:
        """`--lint-cmd stop` (its value) must not be recovered as the event.

        --lint-cmd/--timeout are value-taking flags; if _VALUE_FLAGS omits them,
        their value is mistaken for the event positional in the argparse-error
        fallback, flipping a missing-event write guard from fail-closed (exit 2)
        to fail-open (exit 0).
        """
        # Event positional omitted → argparse errors; the recovered event must
        # not become "stop" via the --lint-cmd value.
        r = self._raw("--lint-cmd", "stop", "--guard", "worktree", "--tool", "claude")
        assert r.returncode == 2
        r = self._raw("--timeout", "stop", "--guard", "worktree", "--tool", "claude")
        assert r.returncode == 2


class TestMemoryAllowPathsResolver:
    """Unit cover for ``_memory_allow_paths`` — per-tool, per-session path composition."""

    def test_claude_resolves_this_sessions_memory_subdir(self) -> None:
        from wade.hooks.cli import _encode_claude_project_path, _memory_allow_paths

        encoded = _encode_claude_project_path(Path(WT))
        expected = (Path.home() / ".claude" / "projects" / encoded / "memory").resolve()
        assert _memory_allow_paths("claude", Path(WT)) == (expected,)

    def test_claude_does_not_allow_the_whole_projects_tree(self) -> None:
        # The old (pre-narrowing) allow-root — must no longer be what's returned.
        from wade.hooks.cli import _memory_allow_paths

        (allowed,) = _memory_allow_paths("claude", Path(WT))
        assert allowed != (Path.home() / ".claude" / "projects").resolve()

    def test_claude_honors_config_dir_override(self, monkeypatch) -> None:
        from wade.hooks.cli import _encode_claude_project_path, _memory_allow_paths

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/isolated/claude-home")
        encoded = _encode_claude_project_path(Path(WT))
        expected = (Path("/isolated/claude-home") / "projects" / encoded / "memory").resolve(
            strict=False
        )
        assert _memory_allow_paths("claude", Path(WT)) == (expected,)

    def test_codex_honors_config_home_override(self, monkeypatch) -> None:
        from wade.hooks.cli import _memory_allow_paths

        monkeypatch.setenv("CODEX_HOME", "/isolated/codex-home")
        expected = (Path("/isolated/codex-home") / "sessions").resolve(strict=False)
        assert _memory_allow_paths("codex", Path(WT)) == (expected,)

    def test_cursor_resolves_this_sessions_project_dir(self) -> None:
        from wade.hooks.cli import _encode_cursor_project_path, _memory_allow_paths

        encoded = _encode_cursor_project_path(Path(WT))
        expected = (Path.home() / ".cursor" / "projects" / encoded).resolve()
        assert _memory_allow_paths("cursor", Path(WT)) == (expected,)

    def test_case_insensitive_and_trimmed(self) -> None:
        from wade.hooks.cli import _encode_claude_project_path, _memory_allow_paths

        encoded = _encode_claude_project_path(Path(WT))
        expected = (Path.home() / ".claude" / "projects" / encoded / "memory").resolve()
        assert _memory_allow_paths("  CLAUDE ", Path(WT)) == (expected,)

    def test_empty_tuple_tools_have_no_bypass(self) -> None:
        # Copilot / Antigravity-CLI keep memory in-repo — intentional empty tuple.
        from wade.hooks.cli import _memory_allow_paths

        assert _memory_allow_paths("copilot", Path(WT)) == ()
        assert _memory_allow_paths("antigravity-cli", Path(WT)) == ()

    def test_unknown_tool_has_no_bypass(self) -> None:
        from wade.hooks.cli import _memory_allow_paths

        assert _memory_allow_paths("nonesuch", Path(WT)) == ()

    def test_home_unresolvable_degrades_to_no_bypass(self, monkeypatch) -> None:
        """HOME unset → ``Path.home()`` raises; the resolver returns () and never raises."""
        from wade.hooks import cli

        def _boom() -> Path:
            raise RuntimeError("home unresolvable")

        monkeypatch.setattr(cli.Path, "home", staticmethod(_boom))
        assert cli._memory_allow_paths("claude", Path(WT)) == ()

    def test_symlinked_worktree_root_is_canonicalized_before_encoding(self, tmp_path) -> None:
        """A ``worktree_root`` reached through a symlink must encode the canonical path.

        Regression for #388: if ``project.worktrees_dir`` is configured through a
        symlink, git worktree creation and the launched tool both observe the
        canonical (resolved) CWD — so the allowlist must encode that same
        canonical path, not the symlink spelling, or the tool's real memory
        writes stay denied.
        """
        from wade.hooks.cli import _encode_claude_project_path, _memory_allow_paths

        real_root = tmp_path / "real-worktree"
        real_root.mkdir()
        link_root = tmp_path / "link-worktree"
        link_root.symlink_to(real_root)

        encoded = _encode_claude_project_path(real_root.resolve())
        expected = (Path.home() / ".claude" / "projects" / encoded / "memory").resolve()
        assert _memory_allow_paths("claude", link_root) == (expected,)

    def test_symlinked_memory_leaf_does_not_widen_the_allowlist(
        self, tmp_path, monkeypatch
    ) -> None:
        """A memory leaf that is itself a symlink to a broader dir must not widen the exception.

        Regression for #388: if the computed leaf (``.../memory``) is a symlink
        to e.g. the tool's whole config home, resolving it here used to widen
        the exception to that entire target — permitting writes to files like
        the tool's own hook settings. The fix resolves only the leaf's parent,
        then reattaches the leaf name literally, so the returned allow-path
        stays the (unresolved) leaf spelling, never its symlink target.
        """
        from wade.hooks.cli import _encode_claude_project_path, _memory_allow_paths

        config_home = tmp_path / "claude-home"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_home))
        encoded = _encode_claude_project_path(Path(WT).resolve())
        project_dir = config_home / "projects" / encoded
        project_dir.mkdir(parents=True)
        broad_target = tmp_path / "broad-target"
        broad_target.mkdir()
        (project_dir / "memory").symlink_to(broad_target)

        (allowed,) = _memory_allow_paths("claude", Path(WT))
        assert allowed == project_dir / "memory"
        assert allowed != broad_target.resolve()

    def test_write_through_symlinked_memory_leaf_stays_denied(self, tmp_path, monkeypatch) -> None:
        """End-to-end: a write that resolves through a symlinked memory leaf is denied.

        Simulates a compromised session that replaced its own ``memory`` dir
        with a symlink to a broader directory. Even though the write target's
        *string* sits under the leaf, ``_resolve_path`` follows the real
        filesystem symlink to the broader target, which no longer falls under
        the (unresolved) allow-path — so the write-guard denies it.
        """
        from wade.hooks.cli import _encode_claude_project_path

        # `/tmp` is unconditionally in the scratch allowlist (#409) and
        # `tempfile.gettempdir()` falls back to it when `$TMPDIR` is unusable, so
        # overriding `$TMPDIR` alone cannot exclude `broad_target` from scratch —
        # use a fixed non-temporary target instead (it need not exist on disk).
        config_home = tmp_path / "claude-home"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_home))
        encoded = _encode_claude_project_path(Path(WT).resolve())
        project_dir = config_home / "projects" / encoded
        project_dir.mkdir(parents=True)
        broad_target = Path("/wade-test-broad-target")
        (project_dir / "memory").symlink_to(broad_target)

        target = project_dir / "memory" / "settings.json"
        stdin = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(target)}})
        r = _run_lean("pre_tool_use", "worktree", "claude", stdin)
        assert r.returncode == 2


class TestPerToolMemoryDirsCoverHookWriters:
    """Mirror of :class:`TestPerToolDialectsMatchCrossby`: the memory map must not drift."""

    def test_memory_dirs_key_set_matches_hook_writers(self) -> None:
        from wade.hooks.cli import _TOOL_DIALECTS, _TOOL_MEMORY_DIRS
        from wade.services.implementation_service.bootstrap import _hook_writers

        installed = {tool_id.value for tool_id, _ in _hook_writers()}
        assert set(_TOOL_MEMORY_DIRS) == installed, set(_TOOL_MEMORY_DIRS) ^ installed
        # ...and stays aligned with the dialect map (the same 5 guarded tools).
        assert set(_TOOL_MEMORY_DIRS) == set(_TOOL_DIALECTS)


class TestMemoryAllowlistCLI:
    """End-to-end through the lean entry point: a claude session may write its own
    memory subtree in both worktree and plan modes; its config/auth files, a sibling
    repo, and a tool with no memory entry all stay denied.
    """

    def _mem_target(self) -> Path:
        from wade.hooks.cli import _encode_claude_project_path

        encoded = _encode_claude_project_path(Path(WT))
        return Path.home() / ".claude" / "projects" / encoded / "memory" / "note.md"

    def _mem_write(self) -> str:
        target = str(self._mem_target())
        return json.dumps({"tool_name": "Write", "tool_input": {"file_path": target}})

    def test_memory_write_allowed_worktree_mode(self) -> None:
        r = _run_lean("pre_tool_use", "worktree", "claude", self._mem_write())
        assert r.returncode == 0
        assert r.stdout == ""

    def test_memory_write_allowed_plan_mode(self) -> None:
        r = _run_lean("pre_tool_use", "plan", "claude", self._mem_write())
        assert r.returncode == 0
        assert r.stdout == ""

    def test_memory_shell_redirect_allowed_plan_mode(self) -> None:
        # Exercises the shell channel + the _is_plan_artifact_path ordering trap.
        stdin = json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": f"echo x > {self._mem_target()}"}}
        )
        r = _run_lean("pre_tool_use", "plan", "claude", stdin)
        assert r.returncode == 0

    def test_sibling_project_memory_denied(self) -> None:
        # A different worktree's encoded memory dir is not *this* session's own —
        # the allowlist is scoped per-session, not the whole ~/.claude/projects tree.
        from wade.hooks.cli import _encode_claude_project_path

        encoded = _encode_claude_project_path(Path("/repo/other-wt"))
        target = Path.home() / ".claude" / "projects" / encoded / "memory" / "note.md"
        stdin = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(target)}})
        r = _run_lean("pre_tool_use", "worktree", "claude", stdin)
        assert r.returncode == 2

    def test_session_transcript_write_denied(self) -> None:
        # Sibling to memory/ under the same encoded project dir, but not memory
        # itself — must stay denied even for this session's own project dir.
        from wade.hooks.cli import _encode_claude_project_path

        encoded = _encode_claude_project_path(Path(WT))
        target = Path.home() / ".claude" / "projects" / encoded / "session.jsonl"
        stdin = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(target)}})
        r = _run_lean("pre_tool_use", "worktree", "claude", stdin)
        assert r.returncode == 2

    def test_config_file_write_denied(self) -> None:
        # ~/.claude/settings.json holds the hooks block — outside the memory subtree.
        settings = str(Path.home() / ".claude" / "settings.json")
        stdin = json.dumps({"tool_name": "Write", "tool_input": {"file_path": settings}})
        r = _run_lean("pre_tool_use", "worktree", "claude", stdin)
        assert r.returncode == 2

    def test_sibling_repo_write_denied(self) -> None:
        stdin = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}})
        r = _run_lean("pre_tool_use", "worktree", "claude", stdin)
        assert r.returncode == 2

    def test_empty_tuple_tool_gets_no_bypass(self) -> None:
        # Antigravity-CLI has an empty memory entry, so the claude memory path is just
        # another out-of-worktree write for it — denied.
        stdin = json.dumps({"tool_name": "Write", "tool_input": {"path": str(self._mem_target())}})
        r = _run_lean("pre_tool_use", "worktree", "antigravity-cli", stdin)
        # agy's DECISION dialect exits 0 for both allow and deny — the block lives
        # in the {"decision": "deny"} body, so assert that rather than the exit code.
        assert r.returncode == 0
        assert json.loads(r.stdout)["decision"] == "deny"
