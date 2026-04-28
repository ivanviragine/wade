"""Tests for the generic delegation service."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import wade.ai_tools  # noqa: F401 — triggers adapter auto-registration
from wade.models.config import AICommandConfig
from wade.models.delegation import DelegationMode, DelegationRequest
from wade.services.delegation_service import (
    _delegate_headless,
    _delegate_interactive,
    _delegate_prompt,
    delegate,
    resolve_mode,
)

# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


class TestResolveMode:
    def test_defaults_to_prompt(self) -> None:
        cfg = AICommandConfig()
        assert resolve_mode(cfg) == DelegationMode.PROMPT

    def test_reads_mode_from_config(self) -> None:
        cfg = AICommandConfig(mode="headless")
        assert resolve_mode(cfg) == DelegationMode.HEADLESS

    def test_interactive_mode(self) -> None:
        cfg = AICommandConfig(mode="interactive")
        assert resolve_mode(cfg) == DelegationMode.INTERACTIVE

    def test_invalid_mode_defaults_to_prompt(self) -> None:
        cfg = AICommandConfig(mode="bad_value")
        assert resolve_mode(cfg) == DelegationMode.PROMPT

    def test_custom_default_no_config(self) -> None:
        cfg = AICommandConfig()
        assert resolve_mode(cfg, default=DelegationMode.INTERACTIVE) == DelegationMode.INTERACTIVE

    def test_config_mode_overrides_custom_default(self) -> None:
        cfg = AICommandConfig(mode="headless")
        assert resolve_mode(cfg, default=DelegationMode.INTERACTIVE) == DelegationMode.HEADLESS

    def test_invalid_mode_falls_back_to_custom_default(self) -> None:
        cfg = AICommandConfig(mode="bad_value")
        assert resolve_mode(cfg, default=DelegationMode.INTERACTIVE) == DelegationMode.INTERACTIVE


# ---------------------------------------------------------------------------
# Prompt mode
# ---------------------------------------------------------------------------


class TestDelegatePrompt:
    def test_returns_prompt_as_feedback(self) -> None:
        req = DelegationRequest(mode=DelegationMode.PROMPT, prompt="Review this plan.")
        result = delegate(req)
        assert result.success is True
        assert "Review this plan." in result.feedback
        assert result.mode == DelegationMode.PROMPT

    def test_prompt_mode_directly(self) -> None:
        req = DelegationRequest(mode=DelegationMode.PROMPT, prompt="Some prompt text")
        result = _delegate_prompt(req)
        assert result.success is True
        assert "Some prompt text" in result.feedback

    def test_prompt_mode_returns_raw_prompt(self) -> None:
        req = DelegationRequest(mode=DelegationMode.PROMPT, prompt="My prompt")
        result = _delegate_prompt(req)
        assert result.feedback == "My prompt"


# ---------------------------------------------------------------------------
# Headless mode
# ---------------------------------------------------------------------------


class TestDelegateHeadless:
    def test_unknown_tool_returns_failure(self) -> None:
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review code",
            ai_tool="nonexistent_tool",
        )
        result = _delegate_headless(req)
        assert result.success is False
        assert "Unknown AI tool" in result.feedback

    def test_tool_without_headless_returns_failure(self) -> None:
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review code",
            ai_tool="vscode",
        )
        result = _delegate_headless(req)
        assert result.success is False
        assert "does not support headless" in result.feedback

    @patch("wade.services.delegation_service.run")
    def test_successful_headless_run(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="All looks good!\n")
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review the code",
            ai_tool="claude",
            model="claude-haiku-4-5",
        )
        result = _delegate_headless(req)
        assert result.success is True
        assert result.feedback == "All looks good!"
        assert result.mode == DelegationMode.HEADLESS

        # Verify command was built correctly
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "--print" in cmd

    @patch("wade.services.delegation_service.run")
    def test_headless_empty_output_still_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
        )
        result = _delegate_headless(req)
        assert result.success is True
        assert result.feedback == ""
        assert result.exit_code == 0

    @patch("wade.services.delegation_service.run")
    def test_headless_nonzero_exit(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="Error occurred")
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
        )
        result = _delegate_headless(req)
        assert result.success is False
        assert result.exit_code == 1

    @patch("wade.services.delegation_service.run")
    def test_headless_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["claude"], timeout=120)
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
        )
        result = _delegate_headless(req)
        assert result.success is False
        assert "timed out" in result.feedback

    @patch("wade.services.delegation_service.run")
    def test_codex_headless_delegation(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="LGTM\n")
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review code",
            ai_tool="codex",
            model="codex-mini-latest",
        )
        result = _delegate_headless(req)
        assert result.success is True
        assert result.feedback == "LGTM"

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "codex"
        assert "exec" in cmd
        assert "Review code" in cmd

    @patch("wade.services.delegation_service.run")
    def test_headless_yolo_is_not_forwarded(self, mock_run: MagicMock) -> None:
        """yolo=True in config is silently ignored for headless delegation."""
        mock_run.return_value = MagicMock(returncode=0, stdout="done\n")
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review code",
            ai_tool="claude",
            yolo=True,
        )
        result = _delegate_headless(req)
        assert result.success is True

        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------


class TestDelegateInteractive:
    def test_unknown_tool_returns_failure(self) -> None:
        req = DelegationRequest(
            mode=DelegationMode.INTERACTIVE,
            prompt="Review code",
            ai_tool="nonexistent_tool",
        )
        result = _delegate_interactive(req)
        assert result.success is False
        assert "Unknown AI tool" in result.feedback

    @patch("wade.services.delegation_service.deliver_prompt_if_needed")
    @patch("wade.services.delegation_service.AbstractAITool.get")
    def test_successful_interactive_with_output_file(
        self,
        mock_get: MagicMock,
        mock_deliver: MagicMock,
        tmp_path: Path,
    ) -> None:
        # Set up mock adapter
        mock_adapter = MagicMock()
        mock_adapter.capabilities.return_value = MagicMock(
            blocks_until_exit=True,
            supports_headless=False,
        )
        mock_adapter.launch.return_value = 0
        mock_get.return_value = mock_adapter

        # Write output file as if the AI produced it
        output_file = tmp_path / "output.txt"
        mock_adapter.launch.side_effect = lambda **_kw: output_file.write_text("Review feedback")

        req = DelegationRequest(
            mode=DelegationMode.INTERACTIVE,
            prompt="Review",
            ai_tool="claude",
            output_file=output_file,
        )
        result = _delegate_interactive(req)
        assert result.success is True
        assert result.feedback == "Review feedback"

    @patch("wade.services.delegation_service.deliver_prompt_if_needed")
    @patch("wade.services.delegation_service.AbstractAITool.get")
    def test_interactive_no_output_file(
        self,
        mock_get: MagicMock,
        mock_deliver: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_adapter = MagicMock()
        mock_adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)
        mock_adapter.launch.return_value = 0
        mock_get.return_value = mock_adapter

        output_file = tmp_path / "nonexistent.txt"
        req = DelegationRequest(
            mode=DelegationMode.INTERACTIVE,
            prompt="Review",
            ai_tool="claude",
            output_file=output_file,
        )
        result = _delegate_interactive(req)
        assert result.success is False
        assert "No output file" in result.feedback

    @patch("wade.services.delegation_service.deliver_prompt_if_needed")
    @patch("wade.services.delegation_service.AbstractAITool.get")
    def test_interactive_yolo_is_forwarded(
        self,
        mock_get: MagicMock,
        mock_deliver: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_adapter = MagicMock()
        mock_adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)
        mock_adapter.launch.side_effect = lambda **_kw: (tmp_path / "out.txt").write_text("ok")
        mock_get.return_value = mock_adapter

        output_file = tmp_path / "out.txt"
        req = DelegationRequest(
            mode=DelegationMode.INTERACTIVE,
            prompt="Review",
            ai_tool="claude",
            output_file=output_file,
            yolo=True,
        )
        result = _delegate_interactive(req)
        assert result.success is True
        assert mock_adapter.launch.call_args.kwargs["yolo"] is True


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class TestDelegateDispatch:
    def test_dispatches_prompt(self) -> None:
        req = DelegationRequest(mode=DelegationMode.PROMPT, prompt="text")
        result = delegate(req)
        assert result.mode == DelegationMode.PROMPT
        assert result.success is True

    def test_dispatches_headless_unknown_tool(self) -> None:
        req = DelegationRequest(mode=DelegationMode.HEADLESS, prompt="text", ai_tool="bad")
        result = delegate(req)
        assert result.mode == DelegationMode.HEADLESS
        assert result.success is False
