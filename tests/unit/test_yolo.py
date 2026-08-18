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

        # get_yolo now derives from the resolved permission mode, so an unset
        # config resolves to False (default tier) rather than None.
        config = ProjectConfig()
        assert config.get_yolo() is False
        assert config.get_yolo("implement") is False


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
        from crossby.ai_tools.claude import ClaudeAdapter

        result = ClaudeAdapter().yolo_args()
        assert result == ["--dangerously-skip-permissions"]

    def test_antigravity_cli_yolo_args(self) -> None:
        from crossby.ai_tools.antigravity_cli import AntigravityCLIAdapter

        # agy's --sandbox is a terminal restriction (blocks shell commands),
        # not a write sandbox, so yolo must not pair it with skip-permissions.
        result = AntigravityCLIAdapter().yolo_args()
        assert result == ["--dangerously-skip-permissions"]

    def test_codex_yolo_args(self) -> None:
        from crossby.ai_tools.codex import CodexAdapter

        # Codex yolo skips approvals but keeps the OS sandbox — NOT --yolo
        # (which would also disable the sandbox).
        result = CodexAdapter().yolo_args()
        assert result == ["-a", "never"]
        assert "--yolo" not in result

    def test_copilot_yolo_args(self) -> None:
        from crossby.ai_tools.copilot import CopilotAdapter

        result = CopilotAdapter().yolo_args()
        assert result == ["--yolo"]

    def test_cursor_yolo_args(self) -> None:
        from crossby.ai_tools.cursor import CursorAdapter

        result = CursorAdapter().yolo_args()
        assert result == ["--force"]

    def test_opencode_yolo_args_empty(self) -> None:
        from crossby.ai_tools.opencode import OpenCodeAdapter

        result = OpenCodeAdapter().yolo_args()
        assert result == []


# ---------------------------------------------------------------------------
# Adapter capabilities — supports_yolo
# ---------------------------------------------------------------------------


class TestAdapterSupportsYolo:
    def test_claude_supports_yolo(self) -> None:
        from crossby.ai_tools.claude import ClaudeAdapter

        assert ClaudeAdapter().capabilities().supports_yolo is True

    def test_antigravity_cli_supports_yolo(self) -> None:
        from crossby.ai_tools.antigravity_cli import AntigravityCLIAdapter

        assert AntigravityCLIAdapter().capabilities().supports_yolo is True

    def test_codex_supports_yolo(self) -> None:
        from crossby.ai_tools.codex import CodexAdapter

        assert CodexAdapter().capabilities().supports_yolo is True

    def test_copilot_supports_yolo(self) -> None:
        from crossby.ai_tools.copilot import CopilotAdapter

        assert CopilotAdapter().capabilities().supports_yolo is True

    def test_cursor_supports_yolo(self) -> None:
        from crossby.ai_tools.cursor import CursorAdapter

        assert CursorAdapter().capabilities().supports_yolo is True

    def test_opencode_does_not_support_yolo(self) -> None:
        from crossby.ai_tools.opencode import OpenCodeAdapter

        assert OpenCodeAdapter().capabilities().supports_yolo is False


# ---------------------------------------------------------------------------
# build_launch_command() — YOLO mode
# ---------------------------------------------------------------------------


class TestBuildLaunchCommandYolo:
    def test_claude_yolo_includes_flag(self) -> None:
        from crossby.ai_tools.claude import ClaudeAdapter

        cmd = ClaudeAdapter().build_launch_command(yolo=True)
        assert "--dangerously-skip-permissions" in cmd

    def test_antigravity_cli_yolo_includes_flag(self) -> None:
        from crossby.ai_tools.antigravity_cli import AntigravityCLIAdapter

        cmd = AntigravityCLIAdapter().build_launch_command(yolo=True)
        assert "--dangerously-skip-permissions" in cmd
        # Regression guard: agy's --sandbox blocks shell commands, so yolo
        # must never emit it.
        assert "--sandbox" not in cmd

    def test_codex_yolo_includes_flag(self) -> None:
        from crossby.ai_tools.codex import CodexAdapter

        cmd = CodexAdapter().build_launch_command(yolo=True)
        assert "-a" in cmd
        assert cmd[cmd.index("-a") + 1] == "never"
        assert "--yolo" not in cmd

    def test_copilot_yolo_includes_flag(self) -> None:
        from crossby.ai_tools.copilot import CopilotAdapter

        cmd = CopilotAdapter().build_launch_command(yolo=True)
        assert "--yolo" in cmd

    def test_cursor_yolo_includes_flag(self) -> None:
        from crossby.ai_tools.cursor import CursorAdapter

        cmd = CursorAdapter().build_launch_command(yolo=True)
        assert "--force" in cmd

    def test_yolo_false_excludes_flag(self) -> None:
        from crossby.ai_tools.claude import ClaudeAdapter

        cmd = ClaudeAdapter().build_launch_command(yolo=False)
        assert "--dangerously-skip-permissions" not in cmd

    def test_yolo_supersedes_plan_mode(self) -> None:
        """When yolo=True and plan_mode=True, YOLO flags should be used
        instead of plan_mode flags (for tools that support yolo)."""
        from crossby.ai_tools.claude import ClaudeAdapter

        cmd = ClaudeAdapter().build_launch_command(plan_mode=True, yolo=True)
        assert "--dangerously-skip-permissions" in cmd
        assert "--permission-mode" not in cmd

    def test_yolo_unsupported_falls_back_to_plan_mode(self) -> None:
        """When yolo=True but tool doesn't support it, plan_mode_args should
        still be used."""
        from crossby.ai_tools.opencode import OpenCodeAdapter

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

    def test_unsupported_tool_no_longer_gated(self) -> None:
        # The local supports_yolo gate was removed: WADE forwards the requested
        # tier and crossby owns capability-aware downgrades. So requesting yolo
        # on a tool that lacks it still resolves to True here (crossby downgrades
        # at launch, with a warning).
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        assert resolve_yolo(True, config, "implement", tool="opencode") is True

    def test_supported_tool_returns_true(self) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        result = resolve_yolo(True, config, "implement", tool="claude")
        assert result is True

    @patch("wade.services.ai_resolution.logger")
    def test_unsupported_tool_does_not_log_warning(self, mock_logger: object) -> None:
        # No local gate → no WADE-side warning (crossby warns at launch instead).
        from wade.models.config import ProjectConfig
        from wade.services.ai_resolution import resolve_yolo

        config = ProjectConfig()
        resolve_yolo(True, config, "implement", tool="opencode")
        assert not mock_logger.warning.called  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# confirm_ai_selection() — YOLO in interactive menu
# ---------------------------------------------------------------------------

_IS_TTY = "wade.ui.prompts.is_tty"
_SELECT = "wade.ui.prompts.select"
_DETECT = "wade.services.ai_resolution.AbstractAITool.detect_installed"
_CONSOLE_KV = "wade.ui.console.console.kv"


def _make_installed(*names: str) -> list:
    from crossby.models.ai import AIToolID

    return [AIToolID(n) for n in names]


class TestConfirmPermissionMode:
    """Permission-mode behaviour in confirm_ai_selection."""

    def test_explicit_skips_menu_option(self) -> None:
        """When permission_mode_explicit=True, 'Change permission mode' is hidden."""
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
                permission_mode_explicit=True,
            )

        assert len(menu_items_seen) >= 1
        assert "Change permission mode" not in menu_items_seen[0]

    def test_menu_shows_change_permission_mode_for_supported_tool(self) -> None:
        """Claude supports autonomy tiers → 'Change permission mode' appears."""
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
                permission_mode_explicit=False,
            )

        assert len(menu_items_seen) >= 1
        assert "Change permission mode" in menu_items_seen[0]

    def test_menu_excludes_permission_mode_for_unsupported_tool(self) -> None:
        """OpenCode supports no autonomy tier → no permission-mode option."""
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
                permission_mode_explicit=False,
            )

        assert len(menu_items_seen) >= 1
        assert "Change permission mode" not in menu_items_seen[0]

    def test_change_permission_mode_to_accept_edits(self) -> None:
        """Selecting 'Change permission mode' then a tier updates the result."""
        from wade.models.permission import PermissionMode
        from wade.services.ai_resolution import confirm_ai_selection

        changed = False

        def fake_select(title: str, items: list[str], **kwargs: object) -> int:
            nonlocal changed
            if title == "Select permission mode":
                changed = True
                return items.index("accept-edits")
            # Change the mode once, then Proceed — otherwise the menu re-offers
            # "Change permission mode" forever and the loop never exits.
            if not changed and "Change permission mode" in items:
                return items.index("Change permission mode")
            return 0  # Proceed

        with (
            patch(_IS_TTY, return_value=True),
            patch(_SELECT, side_effect=fake_select),
            patch(_DETECT, return_value=_make_installed("claude")),
            patch(_CONSOLE_KV),
        ):
            _, _, _, mode = confirm_ai_selection(
                "claude",
                "claude-sonnet-4-6",
                tool_explicit=False,
                model_explicit=True,
                effort_explicit=True,
                permission_mode_explicit=False,
            )

        assert mode is PermissionMode.ACCEPT_EDITS


# ---------------------------------------------------------------------------
# Antigravity CLI — headless
# ---------------------------------------------------------------------------


class TestAntigravityCLIHeadless:
    def test_antigravity_cli_supports_headless_capability(self) -> None:
        from crossby.ai_tools.antigravity_cli import AntigravityCLIAdapter

        assert AntigravityCLIAdapter().capabilities().supports_headless is True

    def test_antigravity_cli_headless_flag_is_print(self) -> None:
        from crossby.ai_tools.antigravity_cli import AntigravityCLIAdapter

        assert AntigravityCLIAdapter().capabilities().headless_flag == "--print"

    def test_antigravity_cli_build_launch_command_headless_includes_prompt(self) -> None:
        from crossby.ai_tools.antigravity_cli import AntigravityCLIAdapter

        cmd = AntigravityCLIAdapter().build_launch_command(prompt="test prompt")
        assert "--print" in cmd
        idx = cmd.index("--print")
        assert cmd[idx + 1] == "test prompt"


# ---------------------------------------------------------------------------
# Headless delegation — yolo is never forwarded
# ---------------------------------------------------------------------------


class TestHeadlessPermissionModeBehavior:
    """Verify _delegate_headless forces default autonomy regardless of request."""

    def test_headless_does_not_propagate_yolo_tier(self) -> None:
        """permission_mode=yolo must not produce autonomy flags on the headless command."""
        from unittest.mock import MagicMock, patch

        from wade.models.delegation import DelegationMode, DelegationRequest
        from wade.models.permission import PermissionMode
        from wade.services.delegation_service import _delegate_headless

        with patch("wade.services.delegation_service.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
            req = DelegationRequest(
                mode=DelegationMode.HEADLESS,
                prompt="Review code",
                ai_tool="claude",
                permission_mode=PermissionMode.YOLO,
            )
            result = _delegate_headless(req)

        assert result.success is True
        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd
        assert "--yolo" not in cmd

    def test_headless_default_also_excluded(self) -> None:
        """permission_mode=default produces no autonomy flags (baseline sanity check)."""
        from unittest.mock import MagicMock, patch

        from wade.models.delegation import DelegationMode, DelegationRequest
        from wade.models.permission import PermissionMode
        from wade.services.delegation_service import _delegate_headless

        with patch("wade.services.delegation_service.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n")
            req = DelegationRequest(
                mode=DelegationMode.HEADLESS,
                prompt="Review code",
                ai_tool="claude",
                permission_mode=PermissionMode.DEFAULT,
            )
            result = _delegate_headless(req)

        assert result.success is True
        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd
