"""Tests for the #352 PostToolUse lint-feedback install (bootstrap side).

The installed command is **stable** (``wade-hook post_tool_use --tool <id>
--root <root>``) — the lint command/timeout/scope are resolved from ``.wade.yml``
at runtime — so this asserts the *gating*: enabled + resolvable lint → an add for
context-capable tools only (agy skipped); disabled / unresolvable → a removal for
every tool. Plus a real-writer smoke test.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from crossby.models.ai import AIToolID

from wade.models.config import (
    HooksConfig,
    PostToolUseConfig,
    PreCommitConfig,
    ProjectConfig,
    ProjectSettings,
)
from wade.services.implementation_service import bootstrap as bootstrap_mod

_ALL_TOOLS = [
    AIToolID.CLAUDE,
    AIToolID.CURSOR,
    AIToolID.COPILOT,
    AIToolID.CODEX,
    AIToolID.ANTIGRAVITY_CLI,
]
_CONTEXT_CAPABLE = {AIToolID.CLAUDE, AIToolID.CURSOR, AIToolID.COPILOT, AIToolID.CODEX}


def _capturing_writers(captured: list[tuple[AIToolID, object]]):
    """Fake ``_hook_writers()`` output that records each ``writer.sync`` call."""

    def make_writer(tid: AIToolID):
        class _W:
            def sync(self, data: object, path: Path) -> object:
                captured.append((tid, data))
                return SimpleNamespace(action="noop", message="")

        return _W()

    return [(tid, make_writer(tid)) for tid in _ALL_TOOLS]


def _install(config: ProjectConfig, tmp_path: Path) -> list[tuple[AIToolID, object]]:
    wt = tmp_path / "wt"
    wt.mkdir(exist_ok=True)
    captured: list[tuple[AIToolID, object]] = []
    with patch.object(bootstrap_mod, "_hook_writers", lambda: _capturing_writers(captured)):
        bootstrap_mod._install_post_tool_use_lint_hook(wt, config)
    return captured


def _classify(captured: list[tuple[AIToolID, object]]):
    adds: dict[AIToolID, object] = {}
    removes: dict[AIToolID, object] = {}
    for tid, data in captured:
        if getattr(data, "hooks", None):
            adds[tid] = data.hooks[0]
        if getattr(data, "hooks_remove", None):
            removes[tid] = data.hooks_remove
    return adds, removes


class TestGating:
    def test_adds_to_context_capable_tools_removes_from_agy(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(
                post_tool_use=PostToolUseConfig(enabled=True, lint_cmd="ruff check", timeout=12)
            ),
        )
        adds, removes = _classify(_install(config, tmp_path))
        assert set(adds) == _CONTEXT_CAPABLE
        assert set(removes) == {AIToolID.ANTIGRAVITY_CLI}
        # The command is STABLE — tool + root only, no baked lint cmd/timeout/scope.
        entry = adds[AIToolID.CLAUDE]
        assert entry.event == "post_tool_use"
        assert entry.command.startswith("wade-hook post_tool_use --tool claude --root ")
        assert "--lint-cmd" not in entry.command
        assert "--timeout" not in entry.command
        assert "--unscoped" not in entry.command

    def test_fallback_to_pre_commit_lint_still_adds(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(
                pre_commit=PreCommitConfig(lint="./scripts/check.sh --lint"),
                post_tool_use=PostToolUseConfig(enabled=True),
            ),
        )
        adds, _removes = _classify(_install(config, tmp_path))
        assert set(adds) == _CONTEXT_CAPABLE

    def test_disabled_removes_from_every_tool(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(post_tool_use=PostToolUseConfig(enabled=False, lint_cmd="ruff")),
        )
        adds, removes = _classify(_install(config, tmp_path))
        assert adds == {}
        assert set(removes) == set(_ALL_TOOLS)

    def test_enabled_without_resolvable_command_removes(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(post_tool_use=PostToolUseConfig(enabled=True)),
        )
        adds, removes = _classify(_install(config, tmp_path))
        assert adds == {}
        assert set(removes) == set(_ALL_TOOLS)


class TestFailOpen:
    def test_writer_failure_warns_and_continues(self, tmp_path: Path) -> None:
        # The gate is optional + off-by-default; a writer failure (malformed tool
        # settings file, OSError on write) must warn-and-continue, never abort
        # bootstrap — matching _install_managed_git_hooks. The guard is per tool,
        # so a later tool is still reconciled after an earlier one raises.
        wt = tmp_path / "wt"
        wt.mkdir()
        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(post_tool_use=PostToolUseConfig(enabled=True, lint_cmd="ruff check")),
        )
        synced: list[AIToolID] = []

        def make_writer(tid: AIToolID):
            class _W:
                def sync(self, data: object, path: Path) -> object:
                    if tid is AIToolID.CLAUDE:
                        raise OSError("boom")
                    synced.append(tid)
                    return SimpleNamespace(action="noop", message="")

            return _W()

        writers = [(tid, make_writer(tid)) for tid in _ALL_TOOLS]
        with patch.object(bootstrap_mod, "_hook_writers", lambda: writers):
            # Must not raise despite CLAUDE's writer erroring.
            bootstrap_mod._install_post_tool_use_lint_hook(wt, config)

        # Tools processed after the failing one were still reconciled.
        assert AIToolID.CURSOR in synced


class TestRealWriterSmoke:
    def test_claude_config_gets_stable_post_tool_use_hook(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(
                post_tool_use=PostToolUseConfig(enabled=True, lint_cmd="ruff check", timeout=8)
            ),
        )
        bootstrap_mod._install_post_tool_use_lint_hook(wt, config)
        settings = wt / ".claude" / "settings.json"
        assert settings.is_file()
        text = settings.read_text()
        assert "PostToolUse" in json.dumps(json.loads(text))
        assert "wade-hook post_tool_use --tool claude --root" in text
        assert "--lint-cmd" not in text

    def test_disable_removes_previously_installed_hook(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        enabled = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(post_tool_use=PostToolUseConfig(enabled=True, lint_cmd="ruff check")),
        )
        bootstrap_mod._install_post_tool_use_lint_hook(wt, enabled)
        assert "wade-hook post_tool_use" in (wt / ".claude" / "settings.json").read_text()

        # Re-bootstrap with the gate turned off — the stale entry must be removed.
        disabled = ProjectConfig(project=ProjectSettings(), hooks=HooksConfig())
        bootstrap_mod._install_post_tool_use_lint_hook(wt, disabled)
        settings = wt / ".claude" / "settings.json"
        if settings.is_file():
            assert "wade-hook post_tool_use" not in settings.read_text()
