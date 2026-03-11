"""Tests for PR-SUMMARY.md path resolution in _done_via_pr()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.models.config import ProjectConfig, ProjectSettings
from wade.models.task import Task
from wade.services.implementation_service import _done_via_pr


class TestPrSummaryPathResolution:
    """Test PR-SUMMARY.md path resolution from worktree root."""

    def test_pr_summary_found_in_worktree(self, tmp_path: Path) -> None:
        """When PR-SUMMARY.md exists in worktree root, _done_via_pr uses it."""
        # Arrange
        worktree_path = tmp_path / "wt-42"
        worktree_path.mkdir()
        pr_summary = worktree_path / "PR-SUMMARY.md"
        pr_summary.write_text("Added login feature with OAuth support.\n")

        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        task = Task(id="42", title="Add auth", body="## Tasks\n- Login\n")

        config = ProjectConfig(
            project=ProjectSettings(main_branch="main"),
        )

        # Mock git and provider operations
        with (
            patch("wade.services.implementation_service.get_provider") as mock_get_provider,
            patch("wade.services.implementation_service.git_repo._run_git"),
            patch("wade.services.implementation_service.git_pr.create_pr") as mock_create_pr,
            patch("wade.services.implementation_service.remove_in_progress_label"),
        ):
            mock_provider = MagicMock()
            mock_provider.read_task.return_value = task
            mock_get_provider.return_value = mock_provider
            mock_create_pr.return_value = {"url": "https://github.com/test/pr/1"}

            # Act
            result = _done_via_pr(
                repo_root=repo_root,
                branch="feat/42-add-auth",
                issue_number="42",
                main_branch="main",
                close_issue=True,
                draft=False,
                config=config,
                worktree_path=worktree_path,
            )

            # Assert
            assert result is True
            # Verify that the worktree PR-SUMMARY.md was used
            call_args = mock_create_pr.call_args
            pr_body = call_args.kwargs["body"]
            assert "OAuth support" in pr_body

    def test_pr_summary_missing_warns(self, tmp_path: Path, capsys: object) -> None:
        """When PR-SUMMARY.md is missing, _done_via_pr warns but still succeeds."""
        # Arrange
        worktree_path = tmp_path / "wt-42"
        worktree_path.mkdir()
        # No PR-SUMMARY.md

        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        task = Task(id="42", title="Add auth", body="## Tasks\n- Login\n")

        config = ProjectConfig(
            project=ProjectSettings(main_branch="main"),
        )

        with (
            patch("wade.services.implementation_service.get_provider") as mock_get_provider,
            patch("wade.services.implementation_service.git_repo._run_git"),
            patch("wade.services.implementation_service.git_pr.create_pr") as mock_create_pr,
            patch("wade.services.implementation_service.remove_in_progress_label"),
        ):
            mock_provider = MagicMock()
            mock_provider.read_task.return_value = task
            mock_get_provider.return_value = mock_provider
            mock_create_pr.return_value = {"url": "https://github.com/test/pr/1"}

            # Act
            result = _done_via_pr(
                repo_root=repo_root,
                branch="feat/42-add-auth",
                issue_number="42",
                main_branch="main",
                close_issue=True,
                draft=False,
                config=config,
                worktree_path=worktree_path,
            )

            # Assert — still succeeds, just no summary in PR body
            assert result is True
            call_args = mock_create_pr.call_args
            pr_body = call_args.kwargs["body"]
            # No summary section when file is missing
            assert "## Summary" not in pr_body
