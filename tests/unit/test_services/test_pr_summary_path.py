"""Tests for PR-SUMMARY.md path resolution in _done_via_pr()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ghaiw.models.config import ProjectConfig, ProjectSettings
from ghaiw.models.task import Task
from ghaiw.services.work_service import _done_via_pr


class TestPrSummaryPathResolution:
    """Test PR-SUMMARY.md path resolution: worktree first, then /tmp fallback."""

    def test_pr_summary_found_in_worktree(self, tmp_path: Path) -> None:
        """When PR-SUMMARY.md exists in worktree root, _done_via_pr uses that path."""
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
            patch("ghaiw.services.work_service.get_provider") as mock_get_provider,
            patch("ghaiw.services.work_service.git_repo._run_git"),
            patch("ghaiw.services.work_service.git_pr.create_pr") as mock_create_pr,
            patch("ghaiw.services.work_service.remove_in_progress_label"),
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

    def test_pr_summary_fallback_to_tmp(self, tmp_path: Path) -> None:
        """When PR-SUMMARY.md NOT in worktree, falls back to /tmp/PR-SUMMARY-{issue}.md."""
        # Arrange
        worktree_path = tmp_path / "wt-42"
        worktree_path.mkdir()
        # No PR-SUMMARY.md in worktree

        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Create /tmp version
        tmp_summary = Path("/tmp/PR-SUMMARY-42.md")
        tmp_summary.write_text("Fixed bug in login flow.\n")

        task = Task(id="42", title="Add auth", body="## Tasks\n- Login\n")

        config = ProjectConfig(
            project=ProjectSettings(main_branch="main"),
        )

        # Mock git and provider operations
        with (
            patch("ghaiw.services.work_service.get_provider") as mock_get_provider,
            patch("ghaiw.services.work_service.git_repo._run_git"),
            patch("ghaiw.services.work_service.git_pr.create_pr") as mock_create_pr,
            patch("ghaiw.services.work_service.remove_in_progress_label"),
        ):
            mock_provider = MagicMock()
            mock_provider.read_task.return_value = task
            mock_get_provider.return_value = mock_provider
            mock_create_pr.return_value = {"url": "https://github.com/test/pr/1"}

            try:
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
                # Verify that the /tmp PR-SUMMARY.md was used
                call_args = mock_create_pr.call_args
                pr_body = call_args.kwargs["body"]
                assert "Fixed bug in login flow" in pr_body
            finally:
                # Cleanup
                if tmp_summary.exists():
                    tmp_summary.unlink()

    def test_pr_summary_worktree_takes_precedence(self, tmp_path: Path) -> None:
        """When BOTH exist, worktree version is used (takes precedence)."""
        # Arrange
        worktree_path = tmp_path / "wt-42"
        worktree_path.mkdir()
        pr_summary_wt = worktree_path / "PR-SUMMARY.md"
        pr_summary_wt.write_text("Worktree version: Added login feature.\n")

        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Create /tmp version (should be ignored)
        tmp_summary = Path("/tmp/PR-SUMMARY-42.md")
        tmp_summary.write_text("Tmp version: This should NOT be used.\n")

        task = Task(id="42", title="Add auth", body="## Tasks\n- Login\n")

        config = ProjectConfig(
            project=ProjectSettings(main_branch="main"),
        )

        # Mock git and provider operations
        with (
            patch("ghaiw.services.work_service.get_provider") as mock_get_provider,
            patch("ghaiw.services.work_service.git_repo._run_git"),
            patch("ghaiw.services.work_service.git_pr.create_pr") as mock_create_pr,
            patch("ghaiw.services.work_service.remove_in_progress_label"),
        ):
            mock_provider = MagicMock()
            mock_provider.read_task.return_value = task
            mock_get_provider.return_value = mock_provider
            mock_create_pr.return_value = {"url": "https://github.com/test/pr/1"}

            try:
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
                # Verify that the worktree version was used, NOT the /tmp version
                call_args = mock_create_pr.call_args
                pr_body = call_args.kwargs["body"]
                assert "Worktree version" in pr_body
                assert "This should NOT be used" not in pr_body
            finally:
                # Cleanup
                if tmp_summary.exists():
                    tmp_summary.unlink()
