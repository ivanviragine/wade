"""Tests for allowed_commands_args() across AI tool adapters."""

from __future__ import annotations

from wade.ai_tools.claude import ClaudeAdapter
from wade.ai_tools.codex import CodexAdapter
from wade.ai_tools.copilot import CopilotAdapter
from wade.ai_tools.cursor import CursorAdapter
from wade.ai_tools.gemini import GeminiAdapter
from wade.ai_tools.opencode import OpenCodeAdapter


class TestClaudeAllowedCommands:
    """Tests for ClaudeAdapter.allowed_commands_args()."""

    def test_single_pattern(self) -> None:
        adapter = ClaudeAdapter()
        result = adapter.allowed_commands_args(["wade *"])
        assert result == ["--allowedTools", "Bash(wade *)"]

    def test_multiple_patterns(self) -> None:
        adapter = ClaudeAdapter()
        result = adapter.allowed_commands_args(["wade *", "./scripts/check.sh *"])
        assert result == [
            "--allowedTools",
            "Bash(wade *)",
            "Bash(./scripts/check.sh *)",
        ]

    def test_pattern_without_args(self) -> None:
        adapter = ClaudeAdapter()
        result = adapter.allowed_commands_args(["./scripts/fmt.sh"])
        assert result == ["--allowedTools", "Bash(./scripts/fmt.sh)"]

    def test_empty_list(self) -> None:
        adapter = ClaudeAdapter()
        result = adapter.allowed_commands_args([])
        assert result == []


class TestCopilotAllowedCommands:
    """Tests for CopilotAdapter.allowed_commands_args()."""

    def test_single_pattern(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.allowed_commands_args(["wade *"])
        assert result == ["--allow-tool", "shell(wade:*)"]

    def test_multiple_patterns(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.allowed_commands_args(["wade *", "./scripts/check.sh *"])
        assert result == [
            "--allow-tool",
            "shell(wade:*)",
            "--allow-tool",
            "shell(./scripts/check.sh:*)",
        ]

    def test_pattern_without_args(self) -> None:
        adapter = CopilotAdapter()
        result = adapter.allowed_commands_args(["./scripts/fmt.sh"])
        assert result == ["--allow-tool", "shell(./scripts/fmt.sh)"]


class TestGeminiAllowedCommands:
    """Tests for GeminiAdapter.allowed_commands_args()."""

    def test_single_pattern(self) -> None:
        adapter = GeminiAdapter()
        result = adapter.allowed_commands_args(["wade *"])
        assert result == ["--allowedTools", "shell(wade:*)"]

    def test_multiple_patterns(self) -> None:
        adapter = GeminiAdapter()
        result = adapter.allowed_commands_args(["wade *", "./scripts/check.sh *"])
        assert result == [
            "--allowedTools",
            "shell(wade:*)",
            "--allowedTools",
            "shell(./scripts/check.sh:*)",
        ]


class TestCursorAllowedCommands:
    """Tests for CursorAdapter — returns empty (config-file permissions)."""

    def test_returns_empty(self) -> None:
        adapter = CursorAdapter()
        result = adapter.allowed_commands_args(["wade *"])
        assert result == []

    def test_returns_empty_with_multiple_patterns(self) -> None:
        adapter = CursorAdapter()
        result = adapter.allowed_commands_args(["wade *", "./scripts/check.sh *"])
        assert result == []


class TestCodexAllowedCommands:
    """Tests for CodexAdapter — returns empty (sandbox mode)."""

    def test_returns_empty(self) -> None:
        adapter = CodexAdapter()
        result = adapter.allowed_commands_args(["wade *"])
        assert result == []


class TestOpenCodeAllowedCommands:
    """Tests for OpenCodeAdapter — returns empty (no support)."""

    def test_returns_empty(self) -> None:
        adapter = OpenCodeAdapter()
        result = adapter.allowed_commands_args(["wade *"])
        assert result == []


class TestBuildLaunchCommandWithAllowedCommands:
    """Tests that build_launch_command passes allowed_commands through."""

    def test_claude_includes_allowed_commands_in_command(self) -> None:
        adapter = ClaudeAdapter()
        cmd = adapter.build_launch_command(
            allowed_commands=["wade *", "./scripts/test.sh *"],
        )
        assert "--allowedTools" in cmd
        assert "Bash(wade *)" in cmd
        assert "Bash(./scripts/test.sh *)" in cmd

    def test_no_allowed_commands_omits_flags(self) -> None:
        adapter = ClaudeAdapter()
        cmd = adapter.build_launch_command()
        assert "--allowedTools" not in cmd

    def test_copilot_includes_allow_tool_flags(self) -> None:
        adapter = CopilotAdapter()
        cmd = adapter.build_launch_command(
            allowed_commands=["wade *"],
        )
        assert "--allow-tool" in cmd
        assert "shell(wade:*)" in cmd
