"""Tests for confirm_ai_selection in ai_resolution."""

from __future__ import annotations

from unittest.mock import patch

from wade.services.ai_resolution import confirm_ai_selection

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
        assert result == (_CLAUDE, _MODEL_A)
        mock_select.assert_not_called()

    def test_both_explicit_skips_prompts(self) -> None:
        with patch(_IS_TTY, return_value=True), patch(_SELECT) as mock_select:
            result = confirm_ai_selection(
                _CLAUDE, _MODEL_A, tool_explicit=True, model_explicit=True
            )
        assert result == (_CLAUDE, _MODEL_A)
        mock_select.assert_not_called()

    def test_none_tool_returns_none(self) -> None:
        with patch(_IS_TTY, return_value=True), patch(_SELECT) as mock_select:
            result = confirm_ai_selection(None, None, tool_explicit=False, model_explicit=False)
        assert result == (None, None)
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
        """model_explicit + single installed tool → nothing to change → no prompt."""
        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT) as mock_select,
            patch(_DETECT, return_value=_make_installed(_CLAUDE)),
            patch(_CONSOLE_KV),
        ):
            confirm_ai_selection(_CLAUDE, _MODEL_A, tool_explicit=False, model_explicit=True)

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

        assert result == (_CLAUDE, _MODEL_A)


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
            tool, model = confirm_ai_selection(
                _CLAUDE, _MODEL_A, tool_explicit=False, model_explicit=False
            )

        assert tool == _COPILOT
        assert model == _MODEL_B

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
            tool, model = confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=False,
                model_explicit=True,  # model was explicit but tool change overrides
            )

        assert tool == _COPILOT
        assert model == _MODEL_B


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
            _, model = confirm_ai_selection(
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
            _, model = confirm_ai_selection(
                _CLAUDE, _MODEL_A, tool_explicit=False, model_explicit=False
            )

        assert model == custom_model
        mock_input.assert_called_once()
