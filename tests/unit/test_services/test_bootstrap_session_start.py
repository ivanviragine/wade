"""Tests for the #351 SessionStart context-injection install (bootstrap side).

Asserts the *gating* (installed only for ``supports_session_start_hook`` tools —
agy skipped), the load-bearing ``tools=[]`` (→ ``.*`` matcher, so the hook
re-fires on the resume/compact *sources*), that the four real writers install it
unscoped, that ``bootstrap_worktree`` installs it only when a ``session_phase`` is
given, and the ``plan_mode`` ⇔ ``SessionPhase.PLAN`` invariant across call sites.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from crossby.models.ai import AIToolID

from wade.models.config import HooksConfig, ProjectConfig, ProjectSettings
from wade.models.hooks import SessionPhase
from wade.services import deps_service, plan_service, review_service
from wade.services.implementation_service import bootstrap as bootstrap_mod
from wade.services.implementation_service import bootstrap_worktree, core

_ALL_TOOLS = [
    AIToolID.CLAUDE,
    AIToolID.CURSOR,
    AIToolID.COPILOT,
    AIToolID.CODEX,
    AIToolID.ANTIGRAVITY_CLI,
]
# supports_session_start_hook is True for these four, default-False for agy.
_SESSION_START_CAPABLE = {AIToolID.CLAUDE, AIToolID.CURSOR, AIToolID.COPILOT, AIToolID.CODEX}


def _capturing_writers(captured: list[tuple[AIToolID, object]]):
    """Fake ``_hook_writers()`` output that records each ``writer.sync`` call."""

    def make_writer(tid: AIToolID):
        class _W:
            def sync(self, data: object, path: Path) -> object:
                captured.append((tid, data))
                return SimpleNamespace(action="noop", message="")

        return _W()

    return [(tid, make_writer(tid)) for tid in _ALL_TOOLS]


def _install(tmp_path: Path, phase: SessionPhase) -> list[tuple[AIToolID, object]]:
    wt = tmp_path / "wt"
    wt.mkdir(exist_ok=True)
    captured: list[tuple[AIToolID, object]] = []
    with patch.object(bootstrap_mod, "_hook_writers", lambda: _capturing_writers(captured)):
        bootstrap_mod._install_session_start_hook(wt, phase=phase)
    return captured


class TestGating:
    def test_installs_for_capable_tools_only_agy_skipped(self, tmp_path: Path) -> None:
        captured = _install(tmp_path, SessionPhase.IMPLEMENT)
        tools = {tid for tid, _ in captured}
        assert tools == _SESSION_START_CAPABLE
        # agy's writer.sync is never called — the capability gate `continue`s past it.
        assert AIToolID.ANTIGRAVITY_CLI not in tools

    def test_entry_has_empty_tools_and_bakes_phase(self, tmp_path: Path) -> None:
        captured = dict(_install(tmp_path, SessionPhase.IMPLEMENT))
        data = captured[AIToolID.CLAUDE]
        entry = data.hooks[0]
        assert entry.event == "session_start"
        # tools=[] is load-bearing: _tools_to_matcher([]) → ".*", which is what makes
        # SessionStart re-fire on the resume/compact SOURCES (matcher matches source).
        assert entry.tools == []
        assert entry.fail_closed is False  # non-blocking, like the Stop hook
        assert "wade-hook session_start" in entry.command
        assert "--guard context" in entry.command
        assert "--phase implement" in entry.command

    def test_phase_value_flows_into_command(self, tmp_path: Path) -> None:
        for phase in SessionPhase:
            captured = dict(_install(tmp_path, phase))
            entry = captured[AIToolID.CLAUDE].hooks[0]
            assert f"--phase {phase.value}" in entry.command


class TestRealWriters:
    def test_claude_and_codex_get_dot_star_matcher(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        bootstrap_mod._install_session_start_hook(wt, phase=SessionPhase.IMPLEMENT)

        claude = json.loads((wt / ".claude" / "settings.json").read_text())
        assert claude["hooks"]["SessionStart"][0]["matcher"] == ".*"
        assert "wade-hook session_start" in json.dumps(claude)

        codex = json.loads((wt / ".codex" / "hooks.json").read_text())
        assert codex["hooks"]["SessionStart"][0]["matcher"] == ".*"

    def test_cursor_and_copilot_install_unscoped(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        bootstrap_mod._install_session_start_hook(wt, phase=SessionPhase.REVIEW)

        cursor = json.loads((wt / ".cursor" / "hooks.json").read_text())
        cursor_entries = cursor["hooks"]["sessionStart"]
        # Unscoped: no per-tool matcher → fires on every session-start source.
        assert cursor_entries and all("matcher" not in e for e in cursor_entries)
        assert "--phase review" in json.dumps(cursor)

        copilot = json.loads((wt / ".github" / "hooks" / "hooks.json").read_text())
        copilot_entries = copilot["hooks"]["sessionStart"]
        assert copilot_entries and all("matcher" not in e for e in copilot_entries)
        assert "--phase review" in json.dumps(copilot)

    def test_agy_gets_no_session_start_hook(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        bootstrap_mod._install_session_start_hook(wt, phase=SessionPhase.IMPLEMENT)
        # agy is skipped by the gate — no config file names its tool id / command.
        blob = "\n".join(p.read_text() for p in wt.rglob("*") if p.is_file())
        assert "antigravity-cli" not in blob


class TestPhaseReconciliation:
    """Re-bootstrapping a reused worktree replaces the prior phase's hook, not stacks it.

    An implementation worktree is later reused for its review session, which
    re-bootstraps with ``SessionPhase.REVIEW``. crossby dedups by exact command, so
    the differing ``--phase`` would leave both entries firing unless the install
    revokes the other-phase variants via ``hooks_remove``.
    """

    def test_hooks_remove_carries_other_phase_commands(self, tmp_path: Path) -> None:
        captured = dict(_install(tmp_path, SessionPhase.REVIEW))
        data = captured[AIToolID.CLAUDE]
        removed = {cmd for event, cmd in data.hooks_remove}
        # Exactly the two OTHER phases are revoked; the current phase is not.
        assert all(event == "session_start" for event, _ in data.hooks_remove)
        assert any("--phase implement" in c for c in removed)
        assert any("--phase plan" in c for c in removed)
        assert not any("--phase review" in c for c in removed)

    def test_reused_worktree_ends_with_single_entry(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        # Impl session first, then the worktree is reused for review.
        bootstrap_mod._install_session_start_hook(wt, phase=SessionPhase.IMPLEMENT)
        bootstrap_mod._install_session_start_hook(wt, phase=SessionPhase.REVIEW)

        claude = json.loads((wt / ".claude" / "settings.json").read_text())
        entries = claude["hooks"]["SessionStart"]
        commands = [h["command"] for e in entries for h in e["hooks"]]
        wade_cmds = [c for c in commands if "wade-hook session_start" in c]
        assert len(wade_cmds) == 1
        assert "--phase review" in wade_cmds[0]
        assert "--phase implement" not in wade_cmds[0]


class TestBootstrapWiring:
    def test_no_hook_when_session_phase_unset(self, tmp_path: Path) -> None:
        wt = tmp_path / "worktree"
        wt.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        config = ProjectConfig(project=ProjectSettings(), hooks=HooksConfig())
        with patch.object(bootstrap_mod, "_install_session_start_hook") as install:
            bootstrap_worktree(wt, config, repo_root)
        install.assert_not_called()

    def test_hook_installed_with_phase_when_set(self, tmp_path: Path) -> None:
        wt = tmp_path / "worktree"
        wt.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        config = ProjectConfig(project=ProjectSettings(), hooks=HooksConfig())
        with patch.object(bootstrap_mod, "_install_session_start_hook") as install:
            bootstrap_worktree(wt, config, repo_root, session_phase=SessionPhase.REVIEW)
        install.assert_called_once()
        assert install.call_args.kwargs["phase"] == SessionPhase.REVIEW


class TestSignalConsistency:
    """`plan_mode is True` iff `session_phase == SessionPhase.PLAN` at every call site.

    ``plan_mode`` (write/stop-guard choice) and ``session_phase`` (session-start
    context) are independent signals that happen to be correlated. Rather than
    couple them in code, this pins the invariant by statically inspecting each
    known ``bootstrap_worktree(...)`` call so the two cannot silently drift.
    """

    _CALL_SITE_MODULES: ClassVar[list[ModuleType]] = [
        core,
        review_service,
        plan_service,
        deps_service,
    ]

    def _bootstrap_calls(self, module: ModuleType) -> list[ast.Call]:
        tree = ast.parse(inspect.getsource(module))
        calls: list[ast.Call] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name == "bootstrap_worktree":
                calls.append(node)
        return calls

    @staticmethod
    def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
        for kw in call.keywords:
            if kw.arg == name:
                return kw.value
        return None

    def test_plan_mode_iff_session_phase_plan(self) -> None:
        seen = 0
        for module in self._CALL_SITE_MODULES:
            for call in self._bootstrap_calls(module):
                seen += 1
                pm = self._kwarg(call, "plan_mode")
                plan_mode = bool(pm.value) if isinstance(pm, ast.Constant) else False

                sp = self._kwarg(call, "session_phase")
                is_plan_phase = (
                    isinstance(sp, ast.Attribute)
                    and sp.attr == SessionPhase.PLAN.name
                    and isinstance(sp.value, ast.Name)
                    and sp.value.id == "SessionPhase"
                )
                assert plan_mode == is_plan_phase, (
                    f"{module.__name__}: plan_mode={plan_mode} but "
                    f"session_phase==PLAN is {is_plan_phase}"
                )
        # Guard the guard: all four known call sites must be present, so a new one
        # added without wiring session_phase trips this count.
        assert seen == 4
