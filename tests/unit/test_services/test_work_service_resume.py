"""Tests for work_service resume session launch path."""

from __future__ import annotations

from wade.ai_tools.claude import ClaudeAdapter
from wade.ai_tools.gemini import GeminiAdapter


class TestImplementationServiceResumeLaunch:
    """Verify that resume-capable adapters produce correct resume commands."""

    def test_resume_calls_build_resume_command(self) -> None:
        """When resume_session_id is set, build_resume_command returns a command."""
        adapter = ClaudeAdapter()
        cmd = adapter.build_resume_command("my-session-id")
        assert cmd == ["claude", "--resume", "my-session-id"]

    def test_resume_fallback_when_unsupported(self) -> None:
        """When the tool doesn't support resume, build_resume_command returns None."""
        adapter = GeminiAdapter()
        cmd = adapter.build_resume_command("my-session-id")
        assert cmd is None


class TestResumeCommandIntegration:
    """Integration-level tests for the resume command path."""

    def test_claude_resume_command_format(self) -> None:
        """Claude resume command follows expected format."""
        adapter = ClaudeAdapter()
        cmd = adapter.build_resume_command("session-uuid-123")
        assert cmd is not None
        assert cmd[0] == "claude"
        assert "--resume" in cmd
        assert "session-uuid-123" in cmd

    def test_resume_command_preserves_session_id(self) -> None:
        """Session ID is passed through unchanged to the command."""
        adapter = ClaudeAdapter()
        session_id = "a" * 100  # long session ID
        cmd = adapter.build_resume_command(session_id)
        assert cmd is not None
        assert session_id in cmd
