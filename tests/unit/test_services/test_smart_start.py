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
    """When no PR exists, smart_start falls through to implement."""

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
    @patch("wade.services.smart_start.git_pr.get_pr_body", return_value=None)
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
        mock_pr_body: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = {"number": 99, "state": "OPEN", "isDraft": False}

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_implement.assert_called_once()

    @patch("wade.services.smart_start._run_review_pr_comments", return_value=True)
    @patch("wade.ui.prompts.select", return_value=1)
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_review_pr_comments_runs_review_service(
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
        mock_pr.return_value = {"number": 99, "state": "OPEN", "isDraft": False}

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
        mock_pr.return_value = {"number": 99, "state": "OPEN", "isDraft": False}

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        mock_merge.assert_called_once()


class TestSmartStartDraftPR:
    """When a draft PR exists, shows context-aware menu based on worktree presence."""

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.ui.prompts.select", return_value=0)
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_draft_pr_no_worktree_shows_start_implementation(
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
        """Draft PR with no worktree shows 'Start implementation' and calls _run_implement_task."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = {"number": 99, "state": "OPEN", "isDraft": True}
        mock_worktrees.return_value = []

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        # Verify that prompts.select was called with a menu containing "Start implementation"
        mock_select.assert_called_once()
        call_args = mock_select.call_args
        assert "Start implementation" in call_args[0][1]
        # Verify that "Review PR comments" and "Merge PR" are not in the menu
        assert "Review PR comments" not in call_args[0][1]
        assert "Merge PR" not in call_args[0][1]
        mock_implement.assert_called_once()

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
    def test_draft_pr_with_worktree_shows_continue_working(
        self,
        mock_config: MagicMock,
        mock_get_provider: MagicMock,
        mock_repo_root: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_worktrees: MagicMock,
        mock_select: MagicMock,
        mock_pr_body: MagicMock,
        mock_implement: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Draft PR with worktree shows 'Continue working' and calls _run_implement_task."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = {"number": 99, "state": "OPEN", "isDraft": True}

        result = smart_start("42", project_root=tmp_path)

        assert result is True
        # Verify that prompts.select was called with a menu containing "Continue working"
        mock_select.assert_called_once()
        call_args = mock_select.call_args
        assert "Continue working" in call_args[0][1]
        # Verify that "Review PR comments" and "Merge PR" are not in the menu
        assert "Review PR comments" not in call_args[0][1]
        assert "Merge PR" not in call_args[0][1]
        mock_implement.assert_called_once()

    @patch("wade.services.smart_start._run_implement_task", return_value=True)
    @patch("wade.ui.prompts.select", return_value=0)
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch("wade.services.smart_start.git_pr.get_pr_for_branch")
    @patch("wade.services.smart_start.git_branch.make_branch_name", return_value="feat/42-fix")
    @patch("wade.services.smart_start.git_repo.get_repo_root")
    @patch("wade.services.smart_start.get_provider")
    @patch("wade.services.smart_start.load_config")
    def test_draft_pr_no_review_pr_comments_or_merge_options(
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
        """Draft PR never shows 'Review PR comments' or 'Merge PR' options."""
        mock_repo_root.return_value = tmp_path
        mock_get_provider.return_value.read_task.return_value = _make_task()
        mock_pr.return_value = {"number": 99, "state": "OPEN", "isDraft": True}
        mock_worktrees.return_value = []

        smart_start("42", project_root=tmp_path)

        # Verify that prompts.select was called
        mock_select.assert_called_once()
        call_args = mock_select.call_args
        menu_options = call_args[0][1]

        # Verify that only 1 option is present (either "Start implementation" or "Continue working")
        assert len(menu_options) == 1
        # Verify that "Review PR comments" and "Merge PR" are not in the menu
        assert "Review PR comments" not in menu_options
        assert "Merge PR" not in menu_options


class TestSmartStartGitError:
    """When not in a git repo, falls through to implement."""

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
