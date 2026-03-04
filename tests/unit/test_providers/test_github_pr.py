"""Tests for GitHub provider PR operations — specifically get_pr_for_branch()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from wade.git.repo import GitError
from wade.providers.github import GitHubProvider


@pytest.fixture
def provider() -> GitHubProvider:
    """Create a GitHubProvider instance for testing."""
    return GitHubProvider()


class TestGetPrForBranch:
    """Tests for get_pr_for_branch() delegating to git/pr.py."""

    @patch("wade.git.pr.get_pr_for_branch")
    def test_get_pr_for_branch_returns_pr_on_success(
        self, mock_get_pr: MagicMock, provider: GitHubProvider
    ) -> None:
        """get_pr_for_branch should return PR dict on successful call."""
        pr_data = {
            "number": 42,
            "url": "https://example/pr/42",
            "title": "Test",
            "state": "OPEN",
            "isDraft": False,
        }
        mock_get_pr.return_value = pr_data

        result = provider.get_pr_for_branch("feature-branch")

        assert result == pr_data
        assert result is not None
        assert result["number"] == 42

    @patch("wade.git.pr.get_pr_for_branch", return_value=None)
    def test_get_pr_for_branch_returns_none_when_no_pr(
        self, _mock_get_pr: MagicMock, provider: GitHubProvider
    ) -> None:
        """get_pr_for_branch should return None when no PR exists."""
        result = provider.get_pr_for_branch("feature-branch")
        assert result is None

    @patch("wade.git.pr.get_pr_for_branch")
    def test_get_pr_for_branch_propagates_errors(
        self, mock_get_pr: MagicMock, provider: GitHubProvider
    ) -> None:
        """get_pr_for_branch should propagate GitError from git/pr.py."""
        mock_get_pr.side_effect = GitError("gh CLI not found")

        with pytest.raises(GitError, match="gh CLI not found"):
            provider.get_pr_for_branch("feature-branch")
