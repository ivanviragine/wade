"""Tests for build_resume_command() and supports_resume capability across adapters."""

from __future__ import annotations

from wade.ai_tools.claude import ClaudeAdapter
from wade.ai_tools.codex import CodexAdapter
from wade.ai_tools.copilot import CopilotAdapter
from wade.ai_tools.gemini import GeminiAdapter
from wade.ai_tools.opencode import OpenCodeAdapter


class TestResumeCommandSupported:
    """Adapters that support resume should return the correct command."""

    def test_claude_resume_command(self) -> None:
        adapter = ClaudeAdapter()
        cmd = adapter.build_resume_command("abc-123")
        assert cmd == ["claude", "--resume", "abc-123"]

    def test_claude_supports_resume_capability(self) -> None:
        adapter = ClaudeAdapter()
        assert adapter.capabilities().supports_resume is True

    def test_copilot_resume_command(self) -> None:
        adapter = CopilotAdapter()
        cmd = adapter.build_resume_command("sess-456")
        assert cmd == ["copilot", "--resume=sess-456"]

    def test_copilot_supports_resume_capability(self) -> None:
        adapter = CopilotAdapter()
        assert adapter.capabilities().supports_resume is True

    def test_codex_resume_command(self) -> None:
        adapter = CodexAdapter()
        cmd = adapter.build_resume_command("xyz-789")
        assert cmd == ["codex", "resume", "xyz-789"]

    def test_codex_supports_resume_capability(self) -> None:
        adapter = CodexAdapter()
        assert adapter.capabilities().supports_resume is True

    def test_opencode_resume_command(self) -> None:
        adapter = OpenCodeAdapter()
        cmd = adapter.build_resume_command("oc-111")
        assert cmd == ["opencode", "-s", "oc-111"]

    def test_opencode_supports_resume_capability(self) -> None:
        adapter = OpenCodeAdapter()
        assert adapter.capabilities().supports_resume is True

    def test_gemini_resume_command(self) -> None:
        adapter = GeminiAdapter()
        cmd = adapter.build_resume_command("sess-1")
        assert cmd == ["gemini", "--resume", "sess-1"]

    def test_gemini_supports_resume_capability(self) -> None:
        adapter = GeminiAdapter()
        assert adapter.capabilities().supports_resume is True


class TestResumeCommandUnsupported:
    """Adapters without resume support should return None."""

    def test_base_default_returns_none(self) -> None:
        # Use an adapter that doesn't override build_resume_command
        from wade.ai_tools.base import AbstractAITool
        from wade.models.ai import AIToolID

        # VSCode doesn't override build_resume_command
        adapter = AbstractAITool.get(AIToolID.VSCODE)
        assert adapter.build_resume_command("any-id") is None

    def test_vscode_does_not_support_resume(self) -> None:
        from wade.ai_tools.base import AbstractAITool
        from wade.models.ai import AIToolID

        adapter = AbstractAITool.get(AIToolID.VSCODE)
        assert adapter.capabilities().supports_resume is False

    def test_cursor_does_not_support_resume(self) -> None:
        from wade.ai_tools.base import AbstractAITool
        from wade.models.ai import AIToolID

        adapter = AbstractAITool.get(AIToolID.CURSOR)
        assert adapter.capabilities().supports_resume is False
