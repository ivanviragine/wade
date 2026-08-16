"""Tests for confirm_ai_selection in ai_resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import crossby.ai_tools  # noqa: F401 — registers all adapters via __init_subclass__
import pytest
from crossby.models.ai import EffortLevel

from wade.models.config import AICommandConfig, AIConfig, ComplexityModelMapping, ProjectConfig
from wade.models.delegation import DelegationMode
from wade.models.permission import PermissionMode
from wade.services.ai_resolution import (
    confirm_ai_selection,
    resolve_effort,
    resolve_network_access,
    valid_effort_levels,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLAUDE = "claude"
_COPILOT = "copilot"
_ANTIGRAVITY_CLI = "antigravity-cli"
_MODEL_A = "claude-sonnet-4-6"
_MODEL_B = "claude-opus-4-6"


def _make_installed(*names: str):
    """Return a list of AIToolID-like values."""
    from crossby.models.ai import AIToolID

    return [AIToolID(n) for n in names]


# ---------------------------------------------------------------------------
# Helpers — patch targets
# The functions use local imports so we must patch the source modules directly.
# ---------------------------------------------------------------------------

_IS_TTY = "wade.ui.prompts.is_tty"
_SELECT = "wade.ui.prompts.select"
_INPUT_PROMPT = "wade.ui.prompts.input_prompt"
_DETECT = "wade.services.ai_resolution.AbstractAITool.detect_installed"
_MODELS_FOR_TOOL = "crossby.data.get_models_for_tool"
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
        assert result == (_CLAUDE, _MODEL_A, None, PermissionMode.DEFAULT)
        mock_select.assert_not_called()

    def test_both_explicit_skips_prompts(self) -> None:
        with patch(_IS_TTY, return_value=True), patch(_SELECT) as mock_select:
            result = confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=True,
                model_explicit=True,
                effort_explicit=True,
                permission_mode_explicit=True,
            )
        assert result == (_CLAUDE, _MODEL_A, None, PermissionMode.DEFAULT)
        mock_select.assert_not_called()

    def test_none_tool_returns_none(self) -> None:
        with patch(_IS_TTY, return_value=True), patch(_SELECT) as mock_select:
            result = confirm_ai_selection(None, None, tool_explicit=False, model_explicit=False)
        assert result == (None, None, None, PermissionMode.DEFAULT)
        mock_select.assert_not_called()

    def test_headless_mode_skips_prompt_even_on_tty(self) -> None:
        """Headless mode is unattended by definition — never block on a TTY prompt,
        even when a TTY is attached and flags weren't explicit."""
        with patch(_IS_TTY, return_value=True), patch(_SELECT) as mock_select:
            result = confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=False,
                model_explicit=False,
                mode=DelegationMode.HEADLESS,
            )
        assert result == (_CLAUDE, _MODEL_A, None, PermissionMode.DEFAULT)
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
                permission_mode_explicit=True,
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

        assert result == (_CLAUDE, _MODEL_A, None, PermissionMode.DEFAULT)


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

    def test_tool_switch_clears_effort_for_excluded_level(self) -> None:
        """Switching to a tool that supports effort but excludes the retained
        level clears it — Proceed must not return an unsupported level."""
        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs: object) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return items.index("Change AI tool")
            if call_count == 2:
                return items.index(_ANTIGRAVITY_CLI)
            if call_count == 3:
                return 0  # first model
            return 0  # Proceed — does NOT reopen the effort picker

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed(_CLAUDE, _ANTIGRAVITY_CLI)),
            patch(_MODELS_FOR_TOOL, return_value=[_MODEL_B]),
            patch(_CONSOLE_KV),
        ):
            tool, _model, effort, _pm = confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=False,
                model_explicit=False,
                resolved_effort=EffortLevel.XHIGH,
                effort_explicit=False,
            )

        assert tool == _ANTIGRAVITY_CLI
        assert effort is None  # xhigh is excluded by antigravity-cli → cleared

    def test_tool_switch_keeps_effort_when_new_tool_honors_level(self) -> None:
        """Switching to a restricted tool keeps a retained level it still honors."""
        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs: object) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return items.index("Change AI tool")
            if call_count == 2:
                return items.index(_ANTIGRAVITY_CLI)
            if call_count == 3:
                return 0  # first model
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed(_CLAUDE, _ANTIGRAVITY_CLI)),
            patch(_MODELS_FOR_TOOL, return_value=[_MODEL_B]),
            patch(_CONSOLE_KV),
        ):
            tool, _model, effort, _pm = confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=False,
                model_explicit=False,
                resolved_effort=EffortLevel.MEDIUM,
                effort_explicit=False,
            )

        assert tool == _ANTIGRAVITY_CLI
        assert effort is EffortLevel.MEDIUM  # medium is within antigravity-cli's set


# ---------------------------------------------------------------------------
# valid_effort_levels — tool-valid effort levels from crossby capabilities
# ---------------------------------------------------------------------------


class TestValidEffortLevels:
    """valid_effort_levels reflects each tool's crossby supported_efforts."""

    def test_antigravity_cli_restricted(self) -> None:
        """antigravity-cli supports only low/medium/high."""
        assert valid_effort_levels("antigravity-cli") == [
            EffortLevel.LOW,
            EffortLevel.MEDIUM,
            EffortLevel.HIGH,
        ]

    def test_claude_supports_all_levels(self) -> None:
        """claude imposes no restriction — every EffortLevel is offered."""
        assert valid_effort_levels("claude") == list(EffortLevel)

    def test_unknown_tool_falls_back_to_all(self) -> None:
        """An unknown tool falls back to the full EffortLevel set."""
        assert valid_effort_levels("not-a-real-tool") == list(EffortLevel)

    def test_none_tool_falls_back_to_all(self) -> None:
        """No tool falls back to the full EffortLevel set."""
        assert valid_effort_levels(None) == list(EffortLevel)


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


# ---------------------------------------------------------------------------
# Always-display — the resolved selection surfaces on EVERY path
# ---------------------------------------------------------------------------


def _kv_pairs(mock_kv: MagicMock) -> list[tuple[str, str]]:
    """Extract (key, value) tuples from recorded console.kv calls."""
    pairs: list[tuple[str, str]] = []
    for call in mock_kv.call_args_list:
        if len(call.args) >= 2:
            pairs.append((call.args[0], call.args[1]))
    return pairs


class TestConfirmAiSelectionAlwaysDisplays:
    """The resolved selection (tool/model/effort/permission mode) is displayed
    exactly once, BEFORE the skip guard — so it appears on every launch path."""

    def test_non_tty_still_displays_permission_mode(self) -> None:
        with patch(_IS_TTY, return_value=False), patch(_CONSOLE_KV) as mock_kv:
            confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=False,
                model_explicit=False,
                resolved_permission_mode=PermissionMode.YOLO,
            )
        pairs = _kv_pairs(mock_kv)
        assert ("AI tool", _CLAUDE) in pairs
        pm = [v for k, v in pairs if k == "Permission mode"]
        assert pm and pm[0].startswith("yolo — ")

    def test_headless_still_displays_permission_mode(self) -> None:
        with patch(_IS_TTY, return_value=True), patch(_CONSOLE_KV) as mock_kv:
            confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=False,
                model_explicit=False,
                resolved_permission_mode=PermissionMode.DEFAULT,
                mode=DelegationMode.HEADLESS,
            )
        pm = [v for k, v in _kv_pairs(mock_kv) if k == "Permission mode"]
        assert pm and pm[0].startswith("default — ")

    def test_all_explicit_still_displays_permission_mode(self) -> None:
        with patch(_IS_TTY, return_value=True), patch(_CONSOLE_KV) as mock_kv:
            confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=True,
                model_explicit=True,
                effort_explicit=True,
                permission_mode_explicit=True,
                resolved_permission_mode=PermissionMode.ACCEPT_EDITS,
            )
        pm = [v for k, v in _kv_pairs(mock_kv) if k == "Permission mode"]
        assert pm and pm[0].startswith("accept-edits — ")

    @pytest.mark.parametrize("mode", list(PermissionMode))
    def test_each_mode_renders_its_descriptor(self, mode: PermissionMode) -> None:
        from wade.models.permission import describe_permission_mode

        with patch(_IS_TTY, return_value=False), patch(_CONSOLE_KV) as mock_kv:
            confirm_ai_selection(
                _CLAUDE,
                _MODEL_A,
                tool_explicit=False,
                model_explicit=False,
                resolved_permission_mode=mode,
            )
        pm = [v for k, v in _kv_pairs(mock_kv) if k == "Permission mode"]
        assert pm == [f"{mode.value} — {describe_permission_mode(mode)}"]

    def test_resolved_tool_none_renders_not_resolved(self) -> None:
        """No tool resolved → a single 'not resolved' line, never a permission line."""
        with patch(_IS_TTY, return_value=True), patch(_CONSOLE_KV) as mock_kv:
            confirm_ai_selection(
                None,
                None,
                tool_explicit=False,
                model_explicit=False,
                resolved_permission_mode=PermissionMode.YOLO,
            )
        pairs = _kv_pairs(mock_kv)
        assert ("AI tool", "not resolved") in pairs
        assert all(k != "Permission mode" for k, _ in pairs)

    def test_display_not_double_printed_on_first_render(self) -> None:
        """The hoisted display renders once; the change-loop must not re-print it
        on its first iteration."""
        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, return_value=0),  # Proceed immediately
            patch(_DETECT, return_value=_make_installed(_CLAUDE)),
            patch(_CONSOLE_KV) as mock_kv,
        ):
            confirm_ai_selection(_CLAUDE, _MODEL_A, tool_explicit=False, model_explicit=False)
        tool_lines = [1 for k, _ in _kv_pairs(mock_kv) if k == "AI tool"]
        assert sum(tool_lines) == 1


class TestResolveNetworkAccess:
    """``resolve_network_access`` precedence: flag > command config > global > False."""

    def test_default_is_false_when_unset(self) -> None:
        # Nothing configured, no flag — network is disabled by default.
        assert resolve_network_access(None, ProjectConfig(), "implement") is False

    def test_explicit_flag_true_wins_over_config_false(self) -> None:
        config = ProjectConfig(ai=AIConfig(network_access=False))
        # --network overrides a config that (redundantly) disables it.
        assert resolve_network_access(True, config, "implement") is True

    def test_explicit_flag_false_wins_over_config_true(self) -> None:
        config = ProjectConfig(ai=AIConfig(network_access=True))
        # --no-network overrides a config that enables it (never silently on).
        assert resolve_network_access(False, config, "implement") is False

    def test_command_config_wins_over_global(self) -> None:
        config = ProjectConfig(
            ai=AIConfig(
                network_access=False,
                implement=AICommandConfig(network_access=True),
            )
        )
        assert resolve_network_access(None, config, "implement") is True
        # A different command falls back to the (disabled) global.
        assert resolve_network_access(None, config, "review_pr_comments") is False

    def test_global_config_used_when_no_command_override(self) -> None:
        config = ProjectConfig(ai=AIConfig(network_access=True))
        assert resolve_network_access(None, config, "implement") is True

    def test_resume_reresolves_from_current_config(self) -> None:
        """A resumed session reflects the CURRENT config, not the original launch.

        The two ``build_resume_command`` call sites re-resolve ``network_access``
        fresh from the config loaded at resume time — network policy is a
        launch-time OS concern, not persisted session state. Model that here:
        the same ``(None, config, command)`` call yields the *current* config's
        value, so flipping the policy between launch and resume flips the result.
        """
        # Original launch: network enabled via config.
        launch_cfg = ProjectConfig(ai=AIConfig(network_access=True))
        assert resolve_network_access(None, launch_cfg, "implement") is True
        # Policy changed before resume — a freshly loaded config disables it.
        resume_cfg = ProjectConfig(ai=AIConfig(network_access=False))
        assert resolve_network_access(None, resume_cfg, "implement") is False

    def test_get_network_access_command_over_global_over_default(self) -> None:
        # ProjectConfig.get_network_access is the config-level resolver the
        # service resolver defers to; verify its fallback chain directly.
        assert ProjectConfig().get_network_access("implement") is False
        cfg = ProjectConfig(
            ai=AIConfig(
                network_access=True,
                review_pr_comments=AICommandConfig(network_access=False),
            )
        )
        assert cfg.get_network_access("review_pr_comments") is False
        assert cfg.get_network_access("implement") is True
