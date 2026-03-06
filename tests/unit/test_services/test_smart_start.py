"""Tests for smart_start service — PR-state-aware issue routing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.repo import GitError
from wade.models.task import Task, TaskState
from wade.services.smart_start import smart_start


def _make_task() -> Task:
    return Task(id="42", title="Fix the widget", state=TaskState.OPEN, body="")


class TestSmartStartNoPR:
    """When no PR exists, smart_start falls through to implement-task."""

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch", return_value=None)
    @patch("wade.services.smart_start.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_no_pr_runs_implement_task(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_implement.assert_called_once()


class TestSmartStartMergedPR:
    """When PR is merged, shows info message."""

    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_merged_pr_returns_true(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = {"number": 99, "state": "MERGED"}

        result = smart_start("42", project_root=tmp_path)

        assert result is True


class TestSmartStartOpenPR:
    """When an open PR exists, presents a contextual menu."""

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.ui.prompts.select", return_value=0)
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_continue_working_runs_implement_task(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_worktrees: MagicMock,
        mock_select: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = {"number": 99, "state": "OPEN"}

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_implement.assert_called_once()

    @patch("wade.services.smart_start._run_address_reviews", return_value=True)
    @patch("wade.ui.prompts.select", return_value=1)
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_address_reviews_runs_review_service(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_worktrees: MagicMock,
        mock_select: MagicMock,
        mock_review: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = {"number": 99, "state": "OPEN"}

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_review.assert_called_once()

    @patch("wade.services.smart_start._merge_pr")
    @patch("wade.ui.prompts.select", return_value=2)
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_merge_calls_merge_pr(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_worktrees: MagicMock,
        mock_select: MagicMock,
        mock_merge: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        provider = mock_get_provider.return_value
        provider.read_task.return_value = _make_task()
        mock_pr.return_value = {"number": 99, "state": "OPEN"}

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_merge.assert_called_once()


class TestSmartStartGitError:
    """When not in a git repo, falls through to implement-task."""

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.services.smart_start.git_repo.get_repo_root", side_effect=GitError("nope"))
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_git_error_falls_through(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_implement.assert_called_once()
