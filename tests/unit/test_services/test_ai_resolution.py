"""Tests for confirm_ai_selection in ai_resolution."""

from __future__ import annotations

from unittest.mock import patch

import wade.ai_tools  # noqa: F401 — registers all adapters via __init_subclass__
from wade.models.ai import EffortLevel
from wade.models.config import AICommandConfig, AIConfig, ComplexityModelMapping, ProjectConfig
from wade.services.ai_resolution import confirm_ai_selection, resolve_effort

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLAUDE = "claude"
_COPILOT = "copilot"
_MODEL_A = "claude-sonnet-4-6"
_MODEL_B = "claude-opus-4-6"


def _make_installed(*names: str):
    """Return a list of AIToolID-like values."""
    from wade.models.ai import AIToolID

    return [AIToolID(n) for n in names]


# ---------------------------------------------------------------------------
# Helpers — patch targets
# The functions use local imports so we must patch the source modules directly.
# ---------------------------------------------------------------------------

_IS_TTY = "wade.ui.prompts.is_tty"
_SELECT = "wade.ui.prompts.select"
_INPUT_PROMPT = "wade.ui.prompts.input_prompt"
_DETECT = "wade.services.ai_resolution.AbstractAITool.detect_installed"
_MODELS_FOR_TOOL = "wade.data.get_models_for_tool"
_CONSOLE_KV = "wade.ui.console.console.kv"


# ---------------------------------------------------------------------------
# Early-exit cases
# ---------------------------------------------------------------------------


class TestConfirmAiSelectionEarlyExit:
    """confirm_ai_selection should return unchanged values without prompting."""

    def test_non_tty_returns_unchanged(self) -> None:
        with patch(_IS_TTY, return_value=False), patch(_SELECT) as mock_select:
            result = confirm_ai_selection(
                _CLAUDE, _MODEL_A, tool_explicit=False, model_explicit=False
            )
        assert result == (_CLAUDE, _MODEL_A, None, False)
        mock_select.assert_not_called()

    def test_both_explicit_skips_prompts(self) -> None:
        with patch(_IS_TTY, return_value=True), patch(_SELECT) as mock_select:
            result = confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=True,
                model_explicit=True,
                effort_explicit=True,
                yolo_explicit=True,
            )
        assert result == (_CLAUDE, _MODEL_A, None, False)
        mock_select.assert_not_called()

    def test_none_tool_returns_none(self) -> None:
        with patch(_IS_TTY, return_value=True), patch(_SELECT) as mock_select:
            result = confirm_ai_selection(None, None, tool_explicit=False, model_explicit=False)
        assert result == (None, None, None, False)
        mock_select.assert_not_called()


# ---------------------------------------------------------------------------
# Menu construction
# ---------------------------------------------------------------------------


class TestConfirmAiSelectionMenuItems:
    """Verify which menu items appear based on explicit flags."""

    def test_tool_explicit_model_not__shows_change_model_only(self) -> None:
        """When tool is explicit, menu has Proceed + Change model only."""
        menu_items_seen: list[list[str]] = []

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            menu_items_seen.append(list(items))
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed(_CLAUDE)),
            patch(_CONSOLE_KV),
        ):
            confirm_ai_selection(_CLAUDE, _MODEL_A, tool_explicit=True, model_explicit=False)

        assert len(menu_items_seen) == 1
        items = menu_items_seen[0]
        assert "Proceed" in items
        assert "Change model" in items
        assert "Change AI tool" not in items

    def test_single_installed_tool_omits_change_tool(self) -> None:
        """Single installed tool → Change AI tool is never shown."""
        menu_items_seen: list[list[str]] = []

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            menu_items_seen.append(list(items))
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed(_CLAUDE)),
            patch(_CONSOLE_KV),
        ):
            confirm_ai_selection(_CLAUDE, _MODEL_A, tool_explicit=False, model_explicit=False)

        assert len(menu_items_seen) == 1
        items = menu_items_seen[0]
        assert "Change AI tool" not in items
        assert "Change model" in items

    def test_model_explicit_single_tool__exits_immediately(self) -> None:
        """model+effort+yolo explicit + single tool → nothing to change → no prompt."""
        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT) as mock_select,
            patch(_DETECT, return_value=_make_installed(_CLAUDE)),
            patch(_CONSOLE_KV),
        ):
            confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=False,
                model_explicit=True,
                effort_explicit=True,
                yolo_explicit=True,
            )

        # Only ["Proceed"] in menu → exits silently without prompting.
        mock_select.assert_not_called()

    def test_model_explicit_two_installed__shows_change_tool(self) -> None:
        """model_explicit + two installed tools → Change AI tool appears."""
        menu_items_seen: list[list[str]] = []

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            menu_items_seen.append(list(items))
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed(_CLAUDE, _COPILOT)),
            patch(_CONSOLE_KV),
        ):
            confirm_ai_selection(_CLAUDE, _MODEL_A, tool_explicit=False, model_explicit=True)

        assert len(menu_items_seen) == 1
        items = menu_items_seen[0]
        assert "Change AI tool" in items
        assert "Change model" not in items

    def test_neither_explicit_two_installed__full_menu(self) -> None:
        """Neither explicit + two tools → full menu shown."""
        menu_items_seen: list[list[str]] = []

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            menu_items_seen.append(list(items))
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed(_CLAUDE, _COPILOT)),
            patch(_CONSOLE_KV),
        ):
            confirm_ai_selection(_CLAUDE, _MODEL_A, tool_explicit=False, model_explicit=False)

        assert len(menu_items_seen) == 1
        items = menu_items_seen[0]
        assert "Proceed" in items
        assert "Change AI tool" in items
        assert "Change model" in items


# ---------------------------------------------------------------------------
# Proceed immediately
# ---------------------------------------------------------------------------


class TestProceedImmediately:
    def test_proceed_returns_resolved_values(self) -> None:
        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, return_value=0),  # Proceed
            patch(_DETECT, return_value=_make_installed(_CLAUDE, _COPILOT)),
            patch(_CONSOLE_KV),
        ):
            result = confirm_ai_selection(
                _CLAUDE, _MODEL_A, tool_explicit=False, model_explicit=False
            )

        assert result == (_CLAUDE, _MODEL_A, None, False)


# ---------------------------------------------------------------------------
# Change AI tool
# ---------------------------------------------------------------------------


class TestChangeAiTool:
    def test_change_tool_returns_new_tool(self) -> None:
        """Selecting Change AI tool → choose copilot → model picker fires."""
        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Main confirmation menu
                return items.index("Change AI tool")
            if call_count == 2:
                # Tool picker
                return items.index(_COPILOT)
            if call_count == 3:
                # Model picker
                return 0  # first model
            # Subsequent main menu calls → Proceed
            return 0

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed(_CLAUDE, _COPILOT)),
            patch(_MODELS_FOR_TOOL, return_value=[_MODEL_B]),
            patch(_CONSOLE_KV),
        ):
            tool, model, effort, _yolo = confirm_ai_selection(
                _CLAUDE, _MODEL_A, tool_explicit=False, model_explicit=False
            )

        assert tool == _COPILOT
        assert model == _MODEL_B
        assert effort is None

    def test_tool_change_forces_model_reselect_when_model_explicit(self) -> None:
        """Tool change forces model re-prompt even when model_explicit=True."""
        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return items.index("Change AI tool")
            if call_count == 2:
                return items.index(_COPILOT)
            if call_count == 3:
                return 0  # first model
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed(_CLAUDE, _COPILOT)),
            patch(_MODELS_FOR_TOOL, return_value=[_MODEL_B]),
            patch(_CONSOLE_KV),
        ):
            tool, model, effort, _yolo = confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=False,
                model_explicit=True,  # model was explicit but tool change overrides
            )

        assert tool == _COPILOT
        assert model == _MODEL_B
        assert effort is None


# ---------------------------------------------------------------------------
# Change model
# ---------------------------------------------------------------------------


class TestChangeModel:
    def test_change_model_returns_selected_model(self) -> None:
        """User picks Change model → selects MODEL_B from list."""
        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return items.index("Change model")
            if call_count == 2:
                return items.index(_MODEL_B)
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed(_CLAUDE)),
            patch(_MODELS_FOR_TOOL, return_value=[_MODEL_A, _MODEL_B]),
            patch(_CONSOLE_KV),
        ):
            _, model, _, _yolo = confirm_ai_selection(
                _CLAUDE, _MODEL_A, tool_explicit=False, model_explicit=False
            )

        assert model == _MODEL_B

    def test_change_model_custom_fires_input_prompt(self) -> None:
        """User picks Custom… → input_prompt fires → custom model returned."""
        custom_model = "my-custom-model-v9"
        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return items.index("Change model")
            if call_count == 2:
                return items.index("Custom…")
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_INPUT_PROMPT, return_value=custom_model) as mock_input,
            patch(_DETECT, return_value=_make_installed(_CLAUDE)),
            patch(_MODELS_FOR_TOOL, return_value=[_MODEL_A]),
            patch(_CONSOLE_KV),
        ):
            _, model, _, _yolo = confirm_ai_selection(
                _CLAUDE, _MODEL_A, tool_explicit=False, model_explicit=False
            )

        assert model == custom_model
        mock_input.assert_called_once()


# ---------------------------------------------------------------------------
# Change effort
# ---------------------------------------------------------------------------


class TestChangeEffort:
    def test_change_effort_selects_level(self) -> None:
        """User picks Change effort → selects 'max' → effort is EffortLevel.MAX."""
        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs: object) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return items.index("Change effort")
            if call_count == 2:
                # Effort picker: ["(none — use tool default)", "low", "medium", "high", "max"]
                return items.index("max")
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed(_CLAUDE)),
            patch(_CONSOLE_KV),
        ):
            _, _, effort, _yolo = confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=False,
                model_explicit=True,
                effort_explicit=False,
            )

        assert effort == EffortLevel.MAX

    def test_tool_switch_clears_effort_for_unsupported_tool(self) -> None:
        """Switching to a tool that doesn't support effort clears stale effort."""
        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs: object) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return items.index("Change AI tool")
            if call_count == 2:
                return items.index(_COPILOT)
            if call_count == 3:
                return 0  # first model
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed(_CLAUDE, _COPILOT)),
            patch(_MODELS_FOR_TOOL, return_value=[_MODEL_B]),
            patch(_CONSOLE_KV),
        ):
            tool, model, effort, _yolo = confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=False,
                model_explicit=False,
                resolved_effort=EffortLevel.HIGH,
                effort_explicit=False,
            )

        assert tool == _COPILOT
        assert model == _MODEL_B
        assert effort is None  # stale effort cleared when copilot doesn't support it


# ---------------------------------------------------------------------------
# resolve_effort — per-tier priority chain
# ---------------------------------------------------------------------------


def _make_config(
    *,
    global_effort: str | None = None,
    plan_effort: str | None = None,
    claude_complex_effort: str | None = None,
) -> ProjectConfig:
    """Build a minimal ProjectConfig for testing resolve_effort."""
    mapping = ComplexityModelMapping(complex_effort=claude_complex_effort)
    ai = AIConfig(effort=global_effort, plan=AICommandConfig(effort=plan_effort))
    return ProjectConfig(ai=ai, models={"claude": mapping})


class TestResolveEffortPerTier:
    """resolve_effort honours: CLI → env → command-config → tier → global."""

    def test_explicit_effort_arg_wins(self) -> None:
        config = _make_config(global_effort="low", plan_effort="medium")
        result = resolve_effort("high", config, "plan")
        assert result == EffortLevel.HIGH

    def test_command_specific_effort_beats_global(self) -> None:
        config = _make_config(global_effort="low", plan_effort="medium")
        result = resolve_effort(None, config, "plan")
        assert result == EffortLevel.MEDIUM

    def test_tier_effort_used_when_no_command_config(self) -> None:
        """When command has no effort override, per-complexity-tier effort is used."""
        config = _make_config(global_effort="low", claude_complex_effort="high")
        result = resolve_effort(None, config, "plan", tool="claude", complexity="complex")
        assert result == EffortLevel.HIGH

    def test_command_effort_beats_tier_effort(self) -> None:
        """Command-specific config takes priority over per-tier effort."""
        config = _make_config(plan_effort="medium", claude_complex_effort="high")
        result = resolve_effort(None, config, "plan", tool="claude", complexity="complex")
        assert result == EffortLevel.MEDIUM

    def test_global_effort_is_fallback(self) -> None:
        """When neither command nor tier has effort, global ai.effort is used."""
        config = _make_config(global_effort="low")
        result = resolve_effort(None, config, "plan", tool="claude", complexity="complex")
        assert result == EffortLevel.LOW

    def test_returns_none_when_no_effort_anywhere(self) -> None:
        config = _make_config()
        result = resolve_effort(None, config, "plan")
        assert result is None

    def test_invalid_effort_string_returns_none(self) -> None:
        config = _make_config()
        result = resolve_effort("not-a-valid-level", config, "plan")
        assert result is None
