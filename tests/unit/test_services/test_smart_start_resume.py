"""Tests for the resume session sub-menu in smart_start."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.models.session import SessionRecord
from wade.models.task import Task, TaskState
from wade.services.smart_start import (
    _get_latest_resumable_session,
    _run_continue_working,
    smart_start,
)
from wade.utils.markdown import build_sessions_block

_SESSION_BODY = build_sessions_block(
    [
        {"phase": "implementation", "ai_tool": "claude", "session_id": "abc-123-456"},
    ]
)

_MULTI_SESSION_BODY = build_sessions_block(
    [
        {"phase": "implementation", "ai_tool": "gemini", "session_id": "gem-old"},
        {"phase": "implementation", "ai_tool": "claude", "session_id": "abc-latest"},
    ]
)


def _make_task() -> Task:
    return Task(id="42", title="Fix the widget", state=TaskState.OPEN, body="")


class TestGetLatestResumableSession:
    """Tests for _get_latest_resumable_session helper."""

    @patch("wade.services.smart_start.git_pr.get_pr_body", return_value=None)
    def test_returns_none_when_no_pr_body(self, mock_body: MagicMock, tmp_path: Path) -> None:
        result = _get_latest_resumable_session(tmp_path, 99)
        assert result is None

    @patch("wade.services.smart_start.git_pr.get_pr_body", return_value="No sessions here")
    def test_returns_none_when_no_sessions_block(
        self, mock_body: MagicMock, tmp_path: Path
    ) -> None:
        result = _get_latest_resumable_session(tmp_path, 99)
        assert result is None

    @patch("wade.services.smart_start.git_pr.get_pr_body")
    def test_returns_session_when_tool_supports_resume(
        self, mock_body: MagicMock, tmp_path: Path
    ) -> None:
        mock_body.return_value = _SESSION_BODY
        result = _get_latest_resumable_session(tmp_path, 99)
        assert result is not None
        assert isinstance(result, SessionRecord)
        assert result.ai_tool == "claude"
        assert result.session_id == "abc-123-456"

    @patch("wade.services.smart_start.git_pr.get_pr_body")
    def test_returns_latest_resumable_skipping_unsupported(
        self, mock_body: MagicMock, tmp_path: Path
    ) -> None:
        """When latest session is cursor (no resume) and previous is claude, returns claude."""
        # Body has cursor (unsupported) as last, claude as second-to-last
        body = build_sessions_block(
            [
                {"phase": "implementation", "ai_tool": "claude", "session_id": "claude-sess"},
                {"phase": "implementation", "ai_tool": "cursor", "session_id": "cursor-sess"},
            ]
        )
        mock_body.return_value = body
        result = _get_latest_resumable_session(tmp_path, 99)
        assert result is not None
        assert isinstance(result, SessionRecord)
        assert result.ai_tool == "claude"
        assert result.session_id == "claude-sess"

    @patch("wade.services.smart_start.git_pr.get_pr_body")
    def test_returns_none_when_only_unsupported_tools(
        self, mock_body: MagicMock, tmp_path: Path
    ) -> None:
        body = build_sessions_block(
            [
                {"phase": "implementation", "ai_tool": "cursor", "session_id": "cursor-sess"},
            ]
        )
        mock_body.return_value = body
        result = _get_latest_resumable_session(tmp_path, 99)
        assert result is None

    @patch("wade.services.smart_start.git_pr.get_pr_body")
    def test_prefers_latest_resumable_session(self, mock_body: MagicMock, tmp_path: Path) -> None:
        mock_body.return_value = _MULTI_SESSION_BODY
        result = _get_latest_resumable_session(tmp_path, 99)
        assert result is not None
        assert result.session_id == "abc-latest"


class TestRunContinueWorking:
    """Tests for the _run_continue_working sub-menu dispatch."""

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.services.smart_start._get_latest_resumable_session", return_value=None)
    def test_no_resumable_session_skips_submenu(
        self,
        mock_get_session: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When no resumable session, directly calls implement_task (no sub-menu)."""
        result = _run_continue_working(
            target="42",
            ai_tool=None,
            model=None,
            project_root=tmp_path,
            detach=False,
            cd_only=False,
            ai_explicit=False,
            model_explicit=False,
            repo_root=tmp_path,
            pr_number=99,
        )
        assert result is True
        mock_implement.assert_called_once()
        # Verify no resume params passed
        call_kwargs = mock_implement.call_args[1]
        assert call_kwargs.get("resume_session_id") is None
        assert call_kwargs.get("resume_ai_tool") is None

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.ui.prompts.select", return_value=0)
    @patch("wade.services.smart_start._get_latest_resumable_session")
    def test_resume_option_selected_passes_session_id(
        self,
        mock_get_session: MagicMock,
        mock_select: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Selecting 'Resume last session' passes resume_session_id and resume_ai_tool."""
        mock_get_session.return_value = SessionRecord(
            phase="implementation",
            ai_tool="claude",
            session_id="abc-123",
        )

        result = _run_continue_working(
            target="42",
            ai_tool=None,
            model=None,
            project_root=tmp_path,
            detach=False,
            cd_only=False,
            ai_explicit=False,
            model_explicit=False,
            repo_root=tmp_path,
            pr_number=99,
        )
        assert result is True
        call_kwargs = mock_implement.call_args[1]
        assert call_kwargs["resume_session_id"] == "abc-123"
        assert call_kwargs["resume_ai_tool"] == "claude"

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.ui.prompts.select", return_value=1)
    @patch("wade.services.smart_start._get_latest_resumable_session")
    def test_new_session_option_selected_no_resume_params(
        self,
        mock_get_session: MagicMock,
        mock_select: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Selecting 'Start new session' calls implement_task without resume params."""
        mock_get_session.return_value = SessionRecord(
            phase="implementation",
            ai_tool="claude",
            session_id="abc-123",
        )

        result = _run_continue_working(
            target="42",
            ai_tool=None,
            model=None,
            project_root=tmp_path,
            detach=False,
            cd_only=False,
            ai_explicit=False,
            model_explicit=False,
            repo_root=tmp_path,
            pr_number=99,
        )
        assert result is True
        call_kwargs = mock_implement.call_args[1]
        assert call_kwargs.get("resume_session_id") is None
        assert call_kwargs.get("resume_ai_tool") is None

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.ui.prompts.select", return_value=0)
    @patch("wade.services.smart_start._get_latest_resumable_session")
    def test_resume_submenu_shows_session_info(
        self,
        mock_get_session: MagicMock,
        mock_select: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Sub-menu labels include tool name and truncated session ID."""
        mock_get_session.return_value = SessionRecord(
            phase="implementation",
            ai_tool="claude",
            session_id="a-very-long-session-identifier-here",
        )

        _run_continue_working(
            target="42",
            ai_tool=None,
            model=None,
            project_root=tmp_path,
            detach=False,
            cd_only=False,
            ai_explicit=False,
            model_explicit=False,
            repo_root=tmp_path,
            pr_number=99,
        )

        # Check that select was called with labels containing tool name
        mock_select.assert_called_once()
        call_args = mock_select.call_args
        labels = call_args[0][1]
        assert len(labels) == 2
        assert "claude" in labels[0]
        assert "Resume last session" in labels[0]
        assert labels[1] == "Start new session"


class TestSmartStartResumeIntegration:
    """Integration tests: smart_start → continue working → resume sub-menu."""

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.services.smart_start.git_pr.get_pr_body")
    @patch("wade.ui.prompts.select")
    @patch(
        "wade.git.worktree.list_worktrees",
        return_value=[{"branch": "feat/42-fix", "path": "/tmp/wt"}],
    )
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_full_flow_resume_selected(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr_for_branch: MagicMock,
        mock_worktrees: MagicMock,
        mock_select: MagicMock,
        mock_pr_body: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Full flow: draft PR with worktree + sessions → resume sub-menu → resume."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr_for_branch.return_value = {"number": 99, "state": "OPEN", "isDraft": True}
        mock_pr_body.return_value = _SESSION_BODY
        # First select: "Continue working" (index 0 in main menu)
        # Second select: "Resume last session" (index 0 in sub-menu)
        mock_select.side_effect = [0, 0]

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        # Verify _run_implement_task was called with resume params
        call_kwargs = mock_implement.call_args[1]
        assert call_kwargs["resume_session_id"] == "abc-123-456"
        assert call_kwargs["resume_ai_tool"] == "claude"

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.services.smart_start.git_pr.get_pr_body")
    @patch("wade.ui.prompts.select")
    @patch(
        "wade.git.worktree.list_worktrees",
        return_value=[{"branch": "feat/42-fix", "path": "/tmp/wt"}],
    )
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_full_flow_new_session_selected(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr_for_branch: MagicMock,
        mock_worktrees: MagicMock,
        mock_select: MagicMock,
        mock_pr_body: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Full flow: sessions exist → sub-menu → 'Start new session' → no resume params."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr_for_branch.return_value = {"number": 99, "state": "OPEN", "isDraft": True}
        mock_pr_body.return_value = _SESSION_BODY
        # First select: "Continue working", second: "Start new session"
        mock_select.side_effect = [0, 1]

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        call_kwargs = mock_implement.call_args[1]
        assert call_kwargs.get("resume_session_id") is None
        assert call_kwargs.get("resume_ai_tool") is None

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.services.smart_start.git_pr.get_pr_body", return_value=None)
    @patch("wade.ui.prompts.select", return_value=0)
    @patch(
        "wade.git.worktree.list_worktrees",
        return_value=[{"branch": "feat/42-fix", "path": "/tmp/wt"}],
    )
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_full_flow_no_sessions_skips_submenu(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr_for_branch: MagicMock,
        mock_worktrees: MagicMock,
        mock_select: MagicMock,
        mock_pr_body: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When no sessions exist, 'Continue working' goes straight to new session."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr_for_branch.return_value = {"number": 99, "state": "OPEN", "isDraft": True}

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        # Only one select call (main menu), no sub-menu
        mock_select.assert_called_once()
        call_kwargs = mock_implement.call_args[1]
        assert call_kwargs.get("resume_session_id") is None

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.services.smart_start.git_pr.get_pr_body")
    @patch("wade.ui.prompts.select")
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_full_flow_no_worktree_resume_selected(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr_for_branch: MagicMock,
        mock_worktrees: MagicMock,
        mock_select: MagicMock,
        mock_pr_body: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """No local worktree + non-draft PR with sessions → resume sub-menu → resume."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        # Non-draft PR so "Continue working" is shown even without a worktree
        mock_pr_for_branch.return_value = {"number": 99, "state": "OPEN", "isDraft": False}
        mock_pr_body.return_value = _SESSION_BODY
        # First select: "Continue working" (index 0), second: "Resume last session" (index 0)
        mock_select.side_effect = [0, 0]

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        call_kwargs = mock_implement.call_args[1]
        assert call_kwargs["resume_session_id"] == "abc-123-456"
        assert call_kwargs["resume_ai_tool"] == "claude"
