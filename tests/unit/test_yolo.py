"""Tests for YOLO mode support across adapters, config, resolution, and build_launch_command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Config model — yolo fields and get_yolo()
# ---------------------------------------------------------------------------


class TestConfigYolo:
    def test_ai_command_config_yolo_default(self) -> None:
        from wade.models.config import AICommandConfig

        cfg = AICommandConfig()
        assert cfg.yolo is None

    def test_ai_command_config_yolo_set(self) -> None:
        from wade.models.config import AICommandConfig

        cfg = AICommandConfig(yolo=True)
        assert cfg.yolo is True

    def test_ai_config_yolo_default(self) -> None:
        from wade.models.config import AIConfig

        cfg = AIConfig()
        assert cfg.yolo is None

    def test_ai_config_yolo_set(self) -> None:
        from wade.models.config import AIConfig

        cfg = AIConfig(yolo=True)
        assert cfg.yolo is True

    def test_get_yolo_global(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig

        config = ProjectConfig(ai=AIConfig(yolo=True))
        assert config.get_yolo() is True
        assert config.get_yolo("implement") is True

    def test_get_yolo_command_override(self) -> None:
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig

        config = ProjectConfig(
            ai=AIConfig(
                yolo=False,
                implement=AICommandConfig(yolo=True),
            )
        )
        assert config.get_yolo("implement") is True
        assert config.get_yolo("plan") is False

    def test_get_yolo_no_config(self) -> None:
        from wade.models.config import ProjectConfig

        config = ProjectConfig()
        assert config.get_yolo() is None
        assert config.get_yolo("implement") is None


# ---------------------------------------------------------------------------
# Config loader — YAML parsing
# ---------------------------------------------------------------------------


class TestConfigLoaderYolo:
    def test_parse_yolo_global(self, tmp_path: Path) -> None:
        from wade.config.loader import load_config

        config_file = tmp_path / ".wade.yml"
        config_file.write_text("ai:\n  yolo: true\n")

        config = load_config(tmp_path)
        assert config.ai.yolo is True

    def test_parse_yolo_per_command(self, tmp_path: Path) -> None:
        from wade.config.loader import load_config

        config_file = tmp_path / ".wade.yml"
        config_file.write_text("ai:\n  work:\n    yolo: true\n")

        config = load_config(tmp_path)
        assert config.ai.implement.yolo is True
        assert config.ai.yolo is None


# ---------------------------------------------------------------------------
# Adapter yolo_args()
# ---------------------------------------------------------------------------


class TestAdapterYoloArgs:
    def test_claude_yolo_args(self) -> None:
        from wade.ai_tools.claude import ClaudeAdapter

        result = ClaudeAdapter().yolo_args()
        assert result == ["--dangerously-skip-permissions"]

    def test_gemini_yolo_args(self) -> None:
        from wade.ai_tools.gemini import GeminiAdapter

        result = GeminiAdapter().yolo_args()
        assert result == ["--yolo"]

    def test_codex_yolo_args(self) -> None:
        from wade.ai_tools.codex import CodexAdapter

        result = CodexAdapter().yolo_args()
        assert result == ["--yolo"]

    def test_copilot_yolo_args(self) -> None:
        from wade.ai_tools.copilot import CopilotAdapter

        result = CopilotAdapter().yolo_args()
        assert result == ["--yolo"]

    def test_cursor_yolo_args(self) -> None:
        from wade.ai_tools.cursor import CursorAdapter

        result = CursorAdapter().yolo_args()
        assert result == ["--force"]

    def test_opencode_yolo_args_empty(self) -> None:
        from wade.ai_tools.opencode import OpenCodeAdapter

        result = OpenCodeAdapter().yolo_args()
        assert result == []


# ---------------------------------------------------------------------------
# Adapter capabilities — supports_yolo
# ---------------------------------------------------------------------------


class TestAdapterSupportsYolo:
    def test_claude_supports_yolo(self) -> None:
        from wade.ai_tools.claude import ClaudeAdapter

        assert ClaudeAdapter().capabilities().supports_yolo is True

    def test_gemini_supports_yolo(self) -> None:
        from wade.ai_tools.gemini import GeminiAdapter

        assert GeminiAdapter().capabilities().supports_yolo is True

    def test_codex_supports_yolo(self) -> None:
        from wade.ai_tools.codex import CodexAdapter

        assert CodexAdapter().capabilities().supports_yolo is True

    def test_copilot_supports_yolo(self) -> None:
        from wade.ai_tools.copilot import CopilotAdapter

        assert CopilotAdapter().capabilities().supports_yolo is True

    def test_cursor_supports_yolo(self) -> None:
        from wade.ai_tools.cursor import CursorAdapter

        assert CursorAdapter().capabilities().supports_yolo is True

    def test_opencode_does_not_support_yolo(self) -> None:
        from wade.ai_tools.opencode import OpenCodeAdapter

        assert OpenCodeAdapter().capabilities().supports_yolo is False


# ---------------------------------------------------------------------------
# build_launch_command() — YOLO mode
# ---------------------------------------------------------------------------


class TestBuildLaunchCommandYolo:
    def test_claude_yolo_includes_flag(self) -> None:
        from wade.ai_tools.claude import ClaudeAdapter

        cmd = ClaudeAdapter().build_launch_command(yolo=True)
        assert "--dangerously-skip-permissions" in cmd

    def test_gemini_yolo_includes_flag(self) -> None:
        from wade.ai_tools.gemini import GeminiAdapter

        cmd = GeminiAdapter().build_launch_command(yolo=True)
        assert "--yolo" in cmd

    def test_codex_yolo_includes_flag(self) -> None:
        from wade.ai_tools.codex import CodexAdapter

        cmd = CodexAdapter().build_launch_command(yolo=True)
        assert "--yolo" in cmd

    def test_copilot_yolo_includes_flag(self) -> None:
        from wade.ai_tools.copilot import CopilotAdapter

        cmd = CopilotAdapter().build_launch_command(yolo=True)
        assert "--yolo" in cmd

    def test_cursor_yolo_includes_flag(self) -> None:
        from wade.ai_tools.cursor import CursorAdapter

        cmd = CursorAdapter().build_launch_command(yolo=True)
        assert "--force" in cmd

    def test_yolo_false_excludes_flag(self) -> None:
        from wade.ai_tools.claude import ClaudeAdapter

        cmd = ClaudeAdapter().build_launch_command(yolo=False)
        assert "--dangerously-skip-permissions" not in cmd

    def test_yolo_supersedes_plan_mode(self) -> None:
        """When yolo=True and plan_mode=True, YOLO flags should be used
        instead of plan_mode flags (for tools that support yolo)."""
        from wade.ai_tools.claude import ClaudeAdapter

        cmd = ClaudeAdapter().build_launch_command(plan_mode=True, yolo=True)
        assert "--dangerously-skip-permissions" in cmd
        assert "--permission-mode" not in cmd

    def test_yolo_unsupported_falls_back_to_plan_mode(self) -> None:
        """When yolo=True but tool doesn't support it, plan_mode_args should
        still be used."""
        from wade.ai_tools.opencode import OpenCodeAdapter

        with pytest.warns(
            UserWarning,
            match=r"does not support YOLO mode; falling back to plan mode",
        ):
            cmd = OpenCodeAdapter().build_launch_command(plan_mode=True, yolo=True)
        # OpenCode doesn't support yolo → should fall back to plan mode
        # OpenCode has no plan_mode_args, so plan_mode flag has no effect,
        # but the key assertion is that yolo_args are NOT in the command
        assert "--force" not in cmd
        assert "--dangerously-skip-permissions" not in cmd
        assert "--yolo" not in cmd


# ---------------------------------------------------------------------------
# resolve_yolo() — fallback chain
# ---------------------------------------------------------------------------


class TestResolveYolo:
    def test_explicit_true(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        result = resolve_yolo(True, config, "implement")
        assert result is True

    def test_explicit_false(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        result = resolve_yolo(False, config, "implement")
        assert result is False

    def test_none_falls_to_config(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig(ai=AIConfig(yolo=True))
        result = resolve_yolo(None, config, "implement")
        assert result is True

    def test_none_with_no_config_returns_false(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        result = resolve_yolo(None, config, "implement")
        assert result is False

    def test_command_config_override(self) -> None:
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig(
            ai=AIConfig(
                yolo=False,
                implement=AICommandConfig(yolo=True),
            )
        )
        result = resolve_yolo(None, config, "implement")
        assert result is True

    def test_unsupported_tool_returns_false(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        result = resolve_yolo(True, config, "implement", tool="opencode")
        assert result is False

    def test_supported_tool_returns_true(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        result = resolve_yolo(True, config, "implement", tool="claude")
        assert result is True

    @patch("wade.services.ai_resolution.logger")
    def test_unsupported_tool_logs_warning(self, mock_logger: object) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        resolve_yolo(True, config, "implement", tool="opencode")
        assert mock_logger.warning.called  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# confirm_ai_selection() — YOLO in interactive menu
# ---------------------------------------------------------------------------

_IS_TTY = "wade.ui.prompts.is_tty"
_SELECT = "wade.ui.prompts.select"
_DETECT = "wade.services.ai_resolution.AbstractAITool.detect_installed"
_CONSOLE_KV = "wade.ui.console.console.kv"


def _make_installed(*names: str) -> list:
    from wade.models.ai import AIToolID

    return [AIToolID(n) for n in names]


class TestConfirmYolo:
    """YOLO-specific behaviour in confirm_ai_selection."""

    def test_yolo_explicit_skips_menu_option(self) -> None:
        """When yolo_explicit=True, 'Turn on YOLO mode' is not in the menu."""
        from wade.services.ai_resolution import confirm_ai_selection

        menu_items_seen: list[list[str]] = []

        def fake_select(title: str, items: list[str], **kwargs: object) -> int:
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
                yolo_explicit=True,
            )

        assert len(menu_items_seen) >= 1
        assert "Turn on YOLO mode" not in menu_items_seen[0]
        assert "Turn off YOLO mode" not in menu_items_seen[0]

    def test_menu_shows_turn_on_yolo_for_supported_tool(self) -> None:
        """Claude supports yolo → 'Turn on YOLO mode' appears in menu."""
        from wade.services.ai_resolution import confirm_ai_selection

        menu_items_seen: list[list[str]] = []

        def fake_select(title: str, items: list[str], **kwargs: object) -> int:
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
                model_explicit=True,
                effort_explicit=True,
                yolo_explicit=False,
            )

        assert len(menu_items_seen) >= 1
        assert "Turn on YOLO mode" in menu_items_seen[0]

    def test_menu_shows_turn_off_when_yolo_on(self) -> None:
        """When resolved_yolo=True, menu shows 'Turn off YOLO mode'."""
        from wade.services.ai_resolution import confirm_ai_selection

        menu_items_seen: list[list[str]] = []

        def fake_select(title: str, items: list[str], **kwargs: object) -> int:
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
                model_explicit=True,
                effort_explicit=True,
                resolved_yolo=True,
                yolo_explicit=False,
            )

        assert len(menu_items_seen) >= 1
        assert "Turn off YOLO mode" in menu_items_seen[0]

    def test_menu_excludes_yolo_for_unsupported_tool(self) -> None:
        """OpenCode does not support yolo → no YOLO option in menu."""
        from wade.services.ai_resolution import confirm_ai_selection

        menu_items_seen: list[list[str]] = []

        def fake_select(title: str, items: list[str], **kwargs: object) -> int:
            menu_items_seen.append(list(items))
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed("opencode")),
            patch(_CONSOLE_KV),
        ):
            confirm_ai_selection(
                "opencode",
                None,
                tool_explicit=False,
                model_explicit=False,
                yolo_explicit=False,
            )

        assert len(menu_items_seen) >= 1
        assert "Turn on YOLO mode" not in menu_items_seen[0]

    def test_toggle_yolo_on(self) -> None:
        """User selects 'Turn on YOLO mode' → yolo becomes True."""
        from wade.services.ai_resolution import confirm_ai_selection

        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs: object) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return items.index("Turn on YOLO mode")
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed("claude")),
            patch(_CONSOLE_KV),
        ):
            _, _, _, yolo = confirm_ai_selection(
                "claude",
                "claude-sonnet-4-6",
                tool_explicit=False,
                model_explicit=True,
                effort_explicit=True,
                yolo_explicit=False,
            )

        assert yolo is True

    def test_toggle_yolo_off(self) -> None:
        """User selects 'Turn off YOLO mode' → yolo becomes False."""
        from wade.services.ai_resolution import confirm_ai_selection

        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs: object) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return items.index("Turn off YOLO mode")
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed("claude")),
            patch(_CONSOLE_KV),
        ):
            _, _, _, yolo = confirm_ai_selection(
                "claude",
                "claude-sonnet-4-6",
                tool_explicit=False,
                model_explicit=True,
                effort_explicit=True,
                resolved_yolo=True,
                yolo_explicit=False,
            )

        assert yolo is False

    def test_tool_switch_clears_yolo_for_unsupported_tool(self) -> None:
        """Switching to a tool that doesn't support yolo clears it."""
        from wade.services.ai_resolution import confirm_ai_selection

        call_count = 0

        def fake_select(title: str, items: list[str], **kwargs: object) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return items.index("Change AI tool")
            if call_count == 2:
                return items.index("opencode")
            if call_count == 3:
                return 0  # first model
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed("claude", "opencode")),
            patch("wade.data.get_models_for_tool", return_value=["gpt-4o"]),
            patch(_CONSOLE_KV),
        ):
            _, _, _, yolo = confirm_ai_selection(
                "claude",
                "claude-sonnet-4-6",
                tool_explicit=False,
                model_explicit=False,
                resolved_yolo=True,
            )

        assert yolo is False


# ---------------------------------------------------------------------------
# Gemini — headless and structured output
# ---------------------------------------------------------------------------


class TestGeminiHeadless:
    def test_gemini_supports_headless_capability(self) -> None:
        from wade.ai_tools.gemini import GeminiAdapter

        assert GeminiAdapter().capabilities().supports_headless is True

    def test_gemini_headless_flag_is_dash_p(self) -> None:
        from wade.ai_tools.gemini import GeminiAdapter

        assert GeminiAdapter().capabilities().headless_flag == "-p"

    def test_gemini_build_launch_command_headless_includes_prompt(self) -> None:
        from wade.ai_tools.gemini import GeminiAdapter

        cmd = GeminiAdapter().build_launch_command(prompt="test prompt")
        assert "-p" in cmd
        idx = cmd.index("-p")
        assert cmd[idx + 1] == "test prompt"


class TestGeminiStructuredOutput:
    def test_gemini_structured_output_args(self) -> None:
        from wade.ai_tools.gemini import GeminiAdapter

        result = GeminiAdapter().structured_output_args({"type": "object"})
        assert result == ["--output-format", "json"]

    def test_gemini_build_launch_command_with_json_schema(self) -> None:
        from wade.ai_tools.gemini import GeminiAdapter

        cmd = GeminiAdapter().build_launch_command(json_schema={"type": "object"})
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "json"
