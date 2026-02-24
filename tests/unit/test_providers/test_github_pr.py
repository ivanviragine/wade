"""Tests for GitHub provider PR operations — specifically get_pr_for_branch()."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ghaiw.providers.github import GitHubProvider
from ghaiw.utils.process import CommandError


@pytest.fixture
def provider() -> GitHubProvider:
    """Create a GitHubProvider instance for testing."""
    return GitHubProvider()


class TestGetPrForBranch:
    """Tests for get_pr_for_branch() error handling and logging."""

    @patch("ghaiw.providers.github.run")
    def test_get_pr_for_branch_returns_pr_on_success(
        self, mock_run: MagicMock, provider: GitHubProvider
    ) -> None:
        """get_pr_for_branch should return PR dict on successful gh call."""
        # Arrange
        pr_data = {"number": 42, "body": "Test PR body"}
        mock_run.return_value = subprocess.CompletedProcess(
            args=["gh"],
            returncode=0,
            stdout=json.dumps(pr_data),
            stderr="",
        )

        # Act
        result = provider.get_pr_for_branch("feature-branch")

        # Assert
        assert result == pr_data
        assert result["number"] == 42
        assert result["body"] == "Test PR body"

    @patch("ghaiw.providers.github.logger")
    @patch("ghaiw.providers.github.run")
    def test_get_pr_for_branch_logs_warning_on_subprocess_error(
        self, mock_run: MagicMock, mock_logger: MagicMock, provider: GitHubProvider
    ) -> None:
        """get_pr_for_branch should log WARNING and return None on CommandError."""
        # Arrange
        error = CommandError(
            command=["gh", "pr", "view", "feature-branch"],
            returncode=1,
            stderr="no pull requests found",
        )
        mock_run.side_effect = error

        # Act
        result = provider.get_pr_for_branch("feature-branch")

        # Assert
        assert result is None
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "get_pr_for_branch failed"
        assert call_args[1]["branch"] == "feature-branch"
        assert "error" in call_args[1]

    @patch("ghaiw.providers.github.logger")
    @patch("ghaiw.providers.github.run")
    def test_get_pr_for_branch_logs_warning_on_json_error(
        self, mock_run: MagicMock, mock_logger: MagicMock, provider: GitHubProvider
    ) -> None:
        """get_pr_for_branch should log WARNING and return None on JSONDecodeError."""
        # Arrange
        mock_run.return_value = subprocess.CompletedProcess(
            args=["gh"],
            returncode=0,
            stdout="invalid json {{{",
            stderr="",
        )

        # Act
        result = provider.get_pr_for_branch("feature-branch")

        # Assert
        assert result is None
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "get_pr_for_branch: invalid JSON response"
        assert call_args[1]["branch"] == "feature-branch"
        assert "error" in call_args[1]

    @patch("ghaiw.providers.github.run")
    def test_get_pr_for_branch_propagates_unexpected_errors(
        self, mock_run: MagicMock, provider: GitHubProvider
    ) -> None:
        """get_pr_for_branch should propagate unexpected exceptions (not catch them)."""
        # Arrange
        mock_run.side_effect = OSError("Unexpected OS error")

        # Act & Assert
        with pytest.raises(OSError, match="Unexpected OS error"):
            provider.get_pr_for_branch("feature-branch")
