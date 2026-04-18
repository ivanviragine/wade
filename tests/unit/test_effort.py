"""Tests for effort level support across models, adapters, config, and resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from wade.models.ai import AIToolID, EffortLevel

# ---------------------------------------------------------------------------
# EffortLevel enum
# ---------------------------------------------------------------------------


class TestEffortLevel:
    def test_values(self) -> None:
        assert EffortLevel.LOW == "low"
        assert EffortLevel.MEDIUM == "medium"
        assert EffortLevel.HIGH == "high"
        assert EffortLevel.XHIGH == "xhigh"
        assert EffortLevel.MAX == "max"

    def test_from_string(self) -> None:
        assert EffortLevel("low") is EffortLevel.LOW
        assert EffortLevel("xhigh") is EffortLevel.XHIGH
        assert EffortLevel("max") is EffortLevel.MAX

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            EffortLevel("ultra")

    def test_all_members(self) -> None:
        assert list(EffortLevel) == [
            EffortLevel.LOW,
            EffortLevel.MEDIUM,
            EffortLevel.HIGH,
            EffortLevel.XHIGH,
            EffortLevel.MAX,
        ]


# ---------------------------------------------------------------------------
# Config model — effort fields and get_effort()
# ---------------------------------------------------------------------------


class TestConfigEffort:
    def test_ai_command_config_effort_default(self) -> None:
        from wade.models.config import AICommandConfig

        cfg = AICommandConfig()
        assert cfg.effort is None

    def test_ai_command_config_effort_set(self) -> None:
        from wade.models.config import AICommandConfig

        cfg = AICommandConfig(effort="high")
        assert cfg.effort == "high"

    def test_ai_config_effort_default(self) -> None:
        from wade.models.config import AIConfig

        cfg = AIConfig()
        assert cfg.effort is None

    def test_ai_config_effort_set(self) -> None:
        from wade.models.config import AIConfig

        cfg = AIConfig(effort="medium")
        assert cfg.effort == "medium"

    def test_get_effort_global(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig

        config = ProjectConfig(ai=AIConfig(effort="high"))
        assert config.get_effort() == "high"
        assert config.get_effort("plan") == "high"

    def test_get_effort_command_override(self) -> None:
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig

        config = ProjectConfig(
            ai=AIConfig(
                effort="low",
                plan=AICommandConfig(effort="max"),
            )
        )
        assert config.get_effort("plan") == "max"
        assert config.get_effort("implement") == "low"

    def test_get_effort_returns_none_when_unset(self) -> None:
        from wade.models.config import ProjectConfig

        config = ProjectConfig()
        assert config.get_effort() is None
        assert config.get_effort("plan") is None

    def test_get_effort_command_only(self) -> None:
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig

        config = ProjectConfig(
            ai=AIConfig(implement=AICommandConfig(effort="medium")),
        )
        assert config.get_effort("implement") == "medium"
        assert config.get_effort("plan") is None


# ---------------------------------------------------------------------------
# Adapter capabilities — supports_effort
# ---------------------------------------------------------------------------


class TestAdapterSupportsEffort:
    """Verify which adapters declare supports_effort=True."""

    @pytest.mark.parametrize(
        "tool_id",
        [AIToolID.CLAUDE, AIToolID.CODEX, AIToolID.CURSOR, AIToolID.OPENCODE],
    )
    def test_supports_effort_true(self, tool_id: AIToolID) -> None:
        from wade.ai_tools.base import AbstractAITool

        adapter = AbstractAITool.get(tool_id)
        assert adapter.capabilities().supports_effort is True

    @pytest.mark.parametrize(
        "tool_id",
        [AIToolID.COPILOT, AIToolID.GEMINI, AIToolID.ANTIGRAVITY, AIToolID.VSCODE],
    )
    def test_supports_effort_false(self, tool_id: AIToolID) -> None:
        from wade.ai_tools.base import AbstractAITool

        adapter = AbstractAITool.get(tool_id)
        assert adapter.capabilities().supports_effort is False


# ---------------------------------------------------------------------------
# Claude adapter — effort_args
# ---------------------------------------------------------------------------


class TestClaudeEffortArgs:
    def _get_adapter(self):
        from wade.ai_tools.claude import ClaudeAdapter

        return ClaudeAdapter()

    def test_effort_low(self) -> None:
        result = self._get_adapter().effort_args(EffortLevel.LOW)
        assert result == ["--effort", "low"]

    def test_effort_max(self) -> None:
        result = self._get_adapter().effort_args(EffortLevel.MAX)
        assert result == ["--effort", "max"]

    def test_resolve_effort_model_unchanged(self) -> None:
        adapter = self._get_adapter()
        assert (
            adapter.resolve_effort_model("claude-sonnet-4-6", EffortLevel.HIGH)
            == "claude-sonnet-4-6"
        )


# ---------------------------------------------------------------------------
# Codex adapter — effort_args with mapping
# ---------------------------------------------------------------------------


class TestCodexEffortArgs:
    def _get_adapter(self):
        from wade.ai_tools.codex import CodexAdapter

        return CodexAdapter()

    def test_effort_low(self) -> None:
        result = self._get_adapter().effort_args(EffortLevel.LOW)
        assert result == ["-c", 'model_reasoning_effort="low"']

    def test_effort_medium(self) -> None:
        result = self._get_adapter().effort_args(EffortLevel.MEDIUM)
        assert result == ["-c", 'model_reasoning_effort="medium"']

    def test_effort_high(self) -> None:
        result = self._get_adapter().effort_args(EffortLevel.HIGH)
        assert result == ["-c", 'model_reasoning_effort="high"']

    def test_effort_xhigh_maps_to_xhigh(self) -> None:
        result = self._get_adapter().effort_args(EffortLevel.XHIGH)
        assert result == ["-c", 'model_reasoning_effort="xhigh"']

    def test_effort_max_maps_to_xhigh(self) -> None:
        result = self._get_adapter().effort_args(EffortLevel.MAX)
        assert result == ["-c", 'model_reasoning_effort="xhigh"']

    def test_yolo_args(self) -> None:
        result = self._get_adapter().yolo_args()
        assert result == ["-a", "never"]


# ---------------------------------------------------------------------------
# Cursor adapter — resolve_effort_model
# ---------------------------------------------------------------------------


class TestCursorEffort:
    def _get_adapter(self):
        from wade.ai_tools.cursor import CursorAdapter

        return CursorAdapter()

    def test_high_appends_thinking(self) -> None:
        result = self._get_adapter().resolve_effort_model("sonnet-4.6", EffortLevel.HIGH)
        assert result == "sonnet-4.6-thinking"

    def test_max_appends_thinking(self) -> None:
        result = self._get_adapter().resolve_effort_model("gpt-5.3-codex", EffortLevel.MAX)
        assert result == "gpt-5.3-codex-thinking"

    def test_low_unchanged(self) -> None:
        result = self._get_adapter().resolve_effort_model("sonnet-4.6", EffortLevel.LOW)
        assert result == "sonnet-4.6"

    def test_medium_unchanged(self) -> None:
        result = self._get_adapter().resolve_effort_model("opus-4.6", EffortLevel.MEDIUM)
        assert result == "opus-4.6"

    def test_already_thinking_not_doubled(self) -> None:
        result = self._get_adapter().resolve_effort_model("sonnet-4.6-thinking", EffortLevel.HIGH)
        assert result == "sonnet-4.6-thinking"

    def test_none_model(self) -> None:
        result = self._get_adapter().resolve_effort_model(None, EffortLevel.HIGH)
        assert result is None

    def test_xhigh_appends_thinking_old_style(self) -> None:
        result = self._get_adapter().resolve_effort_model("sonnet-4.6", EffortLevel.XHIGH)
        assert result == "sonnet-4.6-thinking"

    def test_new_style_high_unchanged(self) -> None:
        result = self._get_adapter().resolve_effort_model("claude-opus-4-7-high", EffortLevel.HIGH)
        assert result == "claude-opus-4-7-high"

    def test_new_style_xhigh_inserts_thinking(self) -> None:
        result = self._get_adapter().resolve_effort_model("claude-opus-4-7-high", EffortLevel.XHIGH)
        assert result == "claude-opus-4-7-thinking-high"

    def test_new_style_max_inserts_thinking(self) -> None:
        result = self._get_adapter().resolve_effort_model("claude-opus-4-7-high", EffortLevel.MAX)
        assert result == "claude-opus-4-7-thinking-high"

    def test_new_style_already_thinking_unchanged(self) -> None:
        result = self._get_adapter().resolve_effort_model(
            "claude-opus-4-7-thinking-high", EffortLevel.MAX
        )
        assert result == "claude-opus-4-7-thinking-high"

    def test_effort_args_empty(self) -> None:
        """Cursor uses model variants, not CLI args — effort_args returns []."""
        result = self._get_adapter().effort_args(EffortLevel.HIGH)
        assert result == []


# ---------------------------------------------------------------------------
# OpenCode adapter — effort_args with --variant
# ---------------------------------------------------------------------------


class TestOpenCodeEffortArgs:
    def _get_adapter(self):
        from wade.ai_tools.opencode import OpenCodeAdapter

        return OpenCodeAdapter()

    def test_effort_low(self) -> None:
        result = self._get_adapter().effort_args(EffortLevel.LOW)
        assert result == ["--variant", "low"]

    def test_effort_medium(self) -> None:
        result = self._get_adapter().effort_args(EffortLevel.MEDIUM)
        assert result == ["--variant", "medium"]

    def test_effort_high(self) -> None:
        result = self._get_adapter().effort_args(EffortLevel.HIGH)
        assert result == ["--variant", "high"]

    def test_effort_xhigh_maps_to_high(self) -> None:
        result = self._get_adapter().effort_args(EffortLevel.XHIGH)
        assert result == ["--variant", "high"]

    def test_effort_max_maps_to_high(self) -> None:
        result = self._get_adapter().effort_args(EffortLevel.MAX)
        assert result == ["--variant", "high"]


# ---------------------------------------------------------------------------
# Base adapter — build_launch_command effort integration
# ---------------------------------------------------------------------------


class TestBuildLaunchCommandEffort:
    """Test that build_launch_command threads effort correctly."""

    def test_effort_args_appended_for_supported_tool(self) -> None:
        from wade.ai_tools.claude import ClaudeAdapter

        adapter = ClaudeAdapter()
        cmd = adapter.build_launch_command(model="claude-sonnet-4-6", effort=EffortLevel.HIGH)
        assert "--effort" in cmd
        idx = cmd.index("--effort")
        assert cmd[idx + 1] == "high"

    def test_effort_none_no_extra_args(self) -> None:
        from wade.ai_tools.claude import ClaudeAdapter

        adapter = ClaudeAdapter()
        cmd = adapter.build_launch_command(model="claude-sonnet-4-6", effort=None)
        assert "--effort" not in cmd

    def test_cursor_effort_changes_model(self) -> None:
        from wade.ai_tools.cursor import CursorAdapter

        adapter = CursorAdapter()
        cmd = adapter.build_launch_command(model="sonnet-4.6", effort=EffortLevel.HIGH)
        assert "sonnet-4.6-thinking" in cmd

    def test_cursor_low_effort_keeps_model(self) -> None:
        from wade.ai_tools.cursor import CursorAdapter

        adapter = CursorAdapter()
        cmd = adapter.build_launch_command(model="sonnet-4.6", effort=EffortLevel.LOW)
        assert "sonnet-4.6" in cmd
        assert "sonnet-4.6-thinking" not in cmd

    def test_unsupported_tool_ignores_effort(self) -> None:
        from wade.ai_tools.copilot import CopilotAdapter

        adapter = CopilotAdapter()
        cmd_without = adapter.build_launch_command()
        cmd_with = adapter.build_launch_command(effort=EffortLevel.MAX)
        assert cmd_without == cmd_with


# ---------------------------------------------------------------------------
# resolve_effort service
# ---------------------------------------------------------------------------


class TestResolveEffort:
    _RESOLVE = "wade.services.ai_resolution.resolve_effort"

    def test_explicit_arg_wins(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig
        from wade.services.ai_resolution import resolve_effort

        config = ProjectConfig(ai=AIConfig(effort="low"))
        result = resolve_effort("high", config)
        assert result is EffortLevel.HIGH

    def test_command_config_fallback(self) -> None:
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
        from wade.services.ai_resolution import resolve_effort

        config = ProjectConfig(ai=AIConfig(plan=AICommandConfig(effort="max")))
        result = resolve_effort(None, config, command="plan")
        assert result is EffortLevel.MAX

    def test_global_config_fallback(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig
        from wade.services.ai_resolution import resolve_effort

        config = ProjectConfig(ai=AIConfig(effort="medium"))
        result = resolve_effort(None, config)
        assert result is EffortLevel.MEDIUM

    def test_returns_none_when_unset(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_effort

        config = ProjectConfig()
        result = resolve_effort(None, config)
        assert result is None

    def test_invalid_level_returns_none(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_effort

        config = ProjectConfig()
        result = resolve_effort("ultra", config)
        assert result is None

    def test_unsupported_tool_returns_none(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_effort

        config = ProjectConfig()
        result = resolve_effort("high", config, tool="copilot")
        assert result is None

    def test_supported_tool_returns_effort(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_effort

        config = ProjectConfig()
        result = resolve_effort("high", config, tool="claude")
        assert result is EffortLevel.HIGH

    def test_env_var_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_effort

        monkeypatch.setenv("WADE_EFFORT", "high")
        config = ProjectConfig()
        result = resolve_effort(None, config)
        assert result is EffortLevel.HIGH

    def test_explicit_arg_beats_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_effort

        monkeypatch.setenv("WADE_EFFORT", "low")
        config = ProjectConfig()
        result = resolve_effort("max", config)
        assert result is EffortLevel.MAX

    def test_env_var_beats_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from wade.models.config import AIConfig, ProjectConfig
        from wade.services.ai_resolution import resolve_effort

        monkeypatch.setenv("WADE_EFFORT", "high")
        config = ProjectConfig(ai=AIConfig(effort="low"))
        result = resolve_effort(None, config)
        assert result is EffortLevel.HIGH

    def test_invalid_env_var_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_effort

        monkeypatch.setenv("WADE_EFFORT", "turbo")
        config = ProjectConfig()
        result = resolve_effort(None, config)
        assert result is None


# ---------------------------------------------------------------------------
# confirm_ai_selection — effort-specific tests
# ---------------------------------------------------------------------------

_IS_TTY = "wade.ui.prompts.is_tty"
_SELECT = "wade.ui.prompts.select"
_DETECT = "wade.services.ai_resolution.AbstractAITool.detect_installed"
_CONSOLE_KV = "wade.ui.console.console.kv"


def _make_installed(*names: str):
    from wade.models.ai import AIToolID

    return [AIToolID(n) for n in names]


class TestConfirmEffort:
    """Effort-specific behaviour in confirm_ai_selection."""

    def test_effort_explicit_skips_prompts(self) -> None:
        """All four explicit → no prompt."""
        from wade.services.ai_resolution import confirm_ai_selection

        with patch(_IS_TTY, return_value=True), patch(_SELECT) as mock_select:
            result = confirm_ai_selection(
                "claude",
                "claude-sonnet-4-6",
                tool_explicit=True,
                model_explicit=True,
                resolved_effort=EffortLevel.HIGH,
                effort_explicit=True,
                yolo_explicit=True,
            )
        assert result == ("claude", "claude-sonnet-4-6", EffortLevel.HIGH, False)
        mock_select.assert_not_called()

    def test_non_tty_preserves_effort(self) -> None:
        from wade.services.ai_resolution import confirm_ai_selection

        with patch(_IS_TTY, return_value=False):
            result = confirm_ai_selection(
                "claude",
                "claude-sonnet-4-6",
                tool_explicit=False,
                model_explicit=False,
                resolved_effort=EffortLevel.MAX,
            )
        assert result == ("claude", "claude-sonnet-4-6", EffortLevel.MAX, False)

    def test_menu_includes_change_effort_for_supported_tool(self) -> None:
        """Claude supports effort → 'Change effort' appears in menu."""
        from wade.services.ai_resolution import confirm_ai_selection

        menu_items_seen: list[list[str]] = []

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            menu_items_seen.append(list(items))
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed("claude")),
            patch(_CONSOLE_KV),
        ):
            confirm_ai_selection(
                "claude",
                "claude-sonnet-4-6",
                tool_explicit=False,
                model_explicit=False,
            )

        assert len(menu_items_seen) >= 1
        assert "Change effort" in menu_items_seen[0]

    def test_menu_excludes_change_effort_for_unsupported_tool(self) -> None:
        """Copilot does not support effort → 'Change effort' not in menu."""
        from wade.services.ai_resolution import confirm_ai_selection

        menu_items_seen: list[list[str]] = []

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            menu_items_seen.append(list(items))
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed("copilot")),
            patch(_CONSOLE_KV),
        ):
            confirm_ai_selection(
                "copilot",
                "gpt-5",
                tool_explicit=False,
                model_explicit=False,
            )

        assert len(menu_items_seen) >= 1
        assert "Change effort" not in menu_items_seen[0]

    def test_change_effort_returns_selected_level(self) -> None:
        """User selects Change effort → picks 'high' → returned."""
        from wade.services.ai_resolution import confirm_ai_selection

        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return items.index("Change effort")
            if call_count == 2:
                # Effort picker: ["(none — use tool default)", "low", "medium", "high", "max"]
                return items.index("high")
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed("claude")),
            patch(_CONSOLE_KV),
        ):
            _, _, effort, _yolo = confirm_ai_selection(
                "claude",
                "claude-sonnet-4-6",
                tool_explicit=False,
                model_explicit=False,
            )

        assert effort is EffortLevel.HIGH

    def test_change_effort_to_none(self) -> None:
        """User selects Change effort → picks '(none)' → None returned."""
        from wade.services.ai_resolution import confirm_ai_selection

        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return items.index("Change effort")
            if call_count == 2:
                return 0  # "(none — use tool default)"
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed("claude")),
            patch(_CONSOLE_KV),
        ):
            _, _, effort, _yolo = confirm_ai_selection(
                "claude",
                "claude-sonnet-4-6",
                tool_explicit=False,
                model_explicit=False,
                resolved_effort=EffortLevel.MAX,
            )

        assert effort is None

    def test_effort_explicit_hides_change_effort(self) -> None:
        """When effort_explicit=True, 'Change effort' is not in the menu."""
        from wade.services.ai_resolution import confirm_ai_selection

        menu_items_seen: list[list[str]] = []

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            menu_items_seen.append(list(items))
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed("claude")),
            patch(_CONSOLE_KV),
        ):
            confirm_ai_selection(
                "claude",
                "claude-sonnet-4-6",
                tool_explicit=False,
                model_explicit=False,
                effort_explicit=True,
            )

        assert len(menu_items_seen) >= 1
        assert "Change effort" not in menu_items_seen[0]
