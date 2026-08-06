"""Tests for the #352 PostToolUse lint-feedback install (bootstrap side).

Verifies the gating logic — enabled + resolvable lint command → a
``post_tool_use`` hook is installed only into context-capable tools (agy, whose
DECISION dialect has no context channel, is skipped) — plus one real-writer
smoke test that the HookEntry lands in Claude's config.
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


class TestGating:
    def test_installs_into_context_capable_tools_only(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(
                post_tool_use=PostToolUseConfig(enabled=True, lint_cmd="ruff check", timeout=12)
            ),
        )
        captured = _install(config, tmp_path)
        tools = {tid for tid, _ in captured}
        # agy (DECISION dialect) is skipped; the other four get the hook.
        assert AIToolID.ANTIGRAVITY_CLI not in tools
        assert tools == {AIToolID.CLAUDE, AIToolID.CURSOR, AIToolID.COPILOT, AIToolID.CODEX}
        # Command shape: file-scoped (no --unscoped), carries lint cmd + timeout.
        _tid, data = captured[0]
        entry = data.hooks[0]
        assert entry.event == "post_tool_use"
        assert "--lint-cmd" in entry.command
        assert "--timeout 12" in entry.command
        assert "--unscoped" not in entry.command

    def test_fallback_to_pre_commit_lint_is_unscoped(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(
                pre_commit=PreCommitConfig(lint="./scripts/check.sh --lint"),
                post_tool_use=PostToolUseConfig(enabled=True),
            ),
        )
        captured = _install(config, tmp_path)
        assert captured, "expected an install when falling back to pre_commit.lint"
        _tid, data = captured[0]
        assert "--unscoped" in data.hooks[0].command

    def test_disabled_installs_nothing(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(post_tool_use=PostToolUseConfig(enabled=False, lint_cmd="ruff")),
        )
        assert _install(config, tmp_path) == []

    def test_enabled_without_resolvable_command_installs_nothing(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(post_tool_use=PostToolUseConfig(enabled=True)),
        )
        assert _install(config, tmp_path) == []


class TestRealWriterSmoke:
    def test_claude_config_gets_post_tool_use_hook(self, tmp_path: Path) -> None:
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
        data = json.loads(text)
        # crossby nests hooks under a "hooks" mapping keyed by the tool's event
        # name (PostToolUse for Claude). Assert the wade-hook command landed.
        assert "PostToolUse" in json.dumps(data)
        assert "wade-hook post_tool_use" in text
