"""Tests for prompt delivery helper — clipboard fallback for non-CLI tools."""

from __future__ import annotations

from unittest.mock import patch

from crossby.ai_tools import AbstractAITool

from wade.services.prompt_delivery import deliver_prompt_if_needed


class TestDeliverPromptIfNeeded:
    def test_noop_for_supported_tool(self) -> None:
        """Tools that support initial messages should not trigger clipboard."""
        adapter = AbstractAITool.get("claude")
        with patch("wade.services.prompt_delivery.copy_to_clipboard") as mock_clip:
            deliver_prompt_if_needed(adapter, "test prompt")
            mock_clip.assert_not_called()

    def test_clipboard_success_for_unsupported_tool(self) -> None:
        """VS Code adapter should copy prompt to clipboard and show hint."""
        adapter = AbstractAITool.get("vscode")
        with (
            patch(
                "wade.services.prompt_delivery.copy_to_clipboard", return_value=True
            ) as mock_clip,
            patch("wade.services.prompt_delivery.console") as mock_console,
        ):
            deliver_prompt_if_needed(adapter, "test prompt")
            mock_clip.assert_called_once_with("test prompt")
            mock_console.hint.assert_called_once()
            assert "clipboard" in mock_console.hint.call_args[0][0].lower()
            assert "VS Code" in mock_console.hint.call_args[0][0]

    def test_clipboard_failure_shows_prompt(self) -> None:
        """When clipboard fails, the full prompt should be shown in a panel."""
        adapter = AbstractAITool.get("antigravity")
        with (
            patch("wade.services.prompt_delivery.copy_to_clipboard", return_value=False),
            patch("wade.services.prompt_delivery.console") as mock_console,
        ):
            deliver_prompt_if_needed(adapter, "my full prompt text")
            mock_console.warn.assert_called_once()
            mock_console.panel.assert_called_once_with("my full prompt text", title="Prompt")

    def test_all_terminal_cli_tools_are_noop(self) -> None:
        """All tools with supports_initial_message=True should be a no-op."""
        for tool_id in ("claude", "copilot", "gemini", "codex", "opencode"):
            adapter = AbstractAITool.get(tool_id)
            with patch("wade.services.prompt_delivery.copy_to_clipboard") as mock_clip:
                deliver_prompt_if_needed(adapter, "test")
                mock_clip.assert_not_called(), f"{tool_id} should not trigger clipboard"
