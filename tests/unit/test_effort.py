"""Tests for effort level support across models, adapters, config, and resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from crossby.models.ai import AIToolID, EffortLevel

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
        from crossby.ai_tools import AbstractAITool

        adapter = AbstractAITool.get(tool_id)
        assert adapter.capabilities().supports_effort is True

    @pytest.mark.parametrize(
        "tool_id",
        [AIToolID.COPILOT, AIToolID.GEMINI, AIToolID.ANTIGRAVITY, AIToolID.VSCODE],
    )
    def test_supports_effort_false(self, tool_id: AIToolID) -> None:
        from crossby.ai_tools import AbstractAITool

        adapter = AbstractAITool.get(tool_id)
        assert adapter.capabilities().supports_effort is False


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
    from crossby.models.ai import AIToolID

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
