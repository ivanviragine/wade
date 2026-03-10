"""Tests for YOLO mode support across adapters, config, resolution, and build_launch_command."""

from __future__ import annotations

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
        assert config.get_yolo("work") is True

    def test_get_yolo_command_override(self) -> None:
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig

        config = ProjectConfig(
            ai=AIConfig(
                yolo=False,
                work=AICommandConfig(yolo=True),
            )
        )
        assert config.get_yolo("work") is True
        assert config.get_yolo("plan") is False

    def test_get_yolo_no_config(self) -> None:
        from wade.models.config import ProjectConfig

        config = ProjectConfig()
        assert config.get_yolo() is None
        assert config.get_yolo("work") is None


# ---------------------------------------------------------------------------
# Config loader — YAML parsing
# ---------------------------------------------------------------------------


class TestConfigLoaderYolo:
    def test_parse_yolo_global(self, tmp_path: pytest.TempPathFactory) -> None:
        from wade.config.loader import load_config

        config_file = tmp_path / ".wade.yml"  # type: ignore[operator]
        config_file.write_text("ai:\n  yolo: true\n")

        config = load_config(tmp_path)  # type: ignore[arg-type]
        assert config.ai.yolo is True

    def test_parse_yolo_per_command(self, tmp_path: pytest.TempPathFactory) -> None:
        from wade.config.loader import load_config

        config_file = tmp_path / ".wade.yml"  # type: ignore[operator]
        config_file.write_text("ai:\n  work:\n    yolo: true\n")

        config = load_config(tmp_path)  # type: ignore[arg-type]
        assert config.ai.work.yolo is True
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
        assert result == ["--approval-mode", "full-auto"]

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
        assert "--approval-mode" in cmd
        idx = cmd.index("--approval-mode")
        assert cmd[idx + 1] == "full-auto"

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
        result = resolve_yolo(True, config, "work")
        assert result is True

    def test_explicit_false(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        result = resolve_yolo(False, config, "work")
        assert result is False

    def test_none_falls_to_config(self) -> None:
        from wade.models.config import AIConfig, ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig(ai=AIConfig(yolo=True))
        result = resolve_yolo(None, config, "work")
        assert result is True

    def test_none_with_no_config_returns_false(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        result = resolve_yolo(None, config, "work")
        assert result is False

    def test_command_config_override(self) -> None:
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig(
            ai=AIConfig(
                yolo=False,
                work=AICommandConfig(yolo=True),
            )
        )
        result = resolve_yolo(None, config, "work")
        assert result is True

    def test_unsupported_tool_returns_false(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        result = resolve_yolo(True, config, "work", tool="opencode")
        assert result is False

    def test_supported_tool_returns_true(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        result = resolve_yolo(True, config, "work", tool="claude")
        assert result is True

    @patch("wade.services.ai_resolution.logger")
    def test_unsupported_tool_logs_warning(self, mock_logger: object) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        resolve_yolo(True, config, "work", tool="opencode")
        assert mock_logger.warning.called  # type: ignore[union-attr]
