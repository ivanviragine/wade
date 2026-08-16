"""Tests for ``_resume_autonomy_args`` — autonomy flags appended to resumed sessions.

crossby's ``build_resume_command()`` takes only a session id, so a resumed session
would otherwise ignore the resolved permission mode and run at the tool's default
tier (the bug: agy subagents get shell denied despite the UI showing ``yolo``).
The helper recomputes the same autonomy args a fresh launch emits.
"""

from __future__ import annotations

from crossby.ai_tools.antigravity_cli import AntigravityCLIAdapter
from crossby.ai_tools.claude import ClaudeAdapter

from wade.models.permission import PermissionMode
from wade.services.implementation_service.core import _resume_autonomy_args


class TestResumeAutonomyArgs:
    def test_agy_yolo_appends_sandbox_flags(self) -> None:
        # The exact flags a fresh yolo launch of agy emits — what actually unblocks
        # subagent shell on resume.
        args = _resume_autonomy_args(AntigravityCLIAdapter(), PermissionMode.YOLO)
        assert args == ["--dangerously-skip-permissions", "--sandbox"]

    def test_agy_default_appends_nothing(self) -> None:
        # Default tier adds no flags (parity with fresh launch), so a plain resume
        # command is unchanged.
        assert _resume_autonomy_args(AntigravityCLIAdapter(), PermissionMode.DEFAULT) == []

    def test_agy_accept_edits(self) -> None:
        assert _resume_autonomy_args(AntigravityCLIAdapter(), PermissionMode.ACCEPT_EDITS) == [
            "--mode",
            "accept-edits",
        ]

    def test_claude_yolo_nonempty_default_empty(self) -> None:
        assert _resume_autonomy_args(ClaudeAdapter(), PermissionMode.YOLO)
        assert _resume_autonomy_args(ClaudeAdapter(), PermissionMode.DEFAULT) == []
