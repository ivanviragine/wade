"""Tests for GitHub provider PR review-status parsing including the latest commit timestamp."""

from __future__ import annotations

import json
from datetime import UTC
from unittest.mock import MagicMock, patch

import pytest

from wade.providers.github import GitHubProvider


@pytest.fixture
def provider() -> GitHubProvider:
    """Create a GitHubProvider instance for testing."""
    return GitHubProvider()


class TestGetPrReviewStatus:
    """Tests for get_pr_review_status() — especially latest_commit_pushed_at parsing."""

    def _make_graphql_response(
        self,
        committed_date: str | None = "2025-01-15T10:30:00Z",
        review_threads: list[dict] | None = None,
        reviews: list[dict] | None = None,
        review_requests: list[dict] | None = None,
    ) -> str:
        """Build a minimal GraphQL JSON response for _fetch_review_status_page."""
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": review_threads or [],
                            },
                            "reviews": {"nodes": reviews or []},
                            "reviewRequests": {"nodes": review_requests or []},
                            "commits": {
                                "nodes": (
                                    [{"commit": {"committedDate": committed_date}}]
                                    if committed_date
                                    else []
                                )
                            },
                        }
                    }
                }
            }
        )

    @patch("wade.providers.github.GitHubProvider.get_pr_issue_comments", return_value=[])
    @patch("wade.providers.github.GitHubProvider.get_repo_nwo", return_value="owner/repo")
    @patch("wade.providers.github.run")
    def test_latest_commit_pushed_at_parsed(
        self,
        mock_run: MagicMock,
        _mock_nwo: MagicMock,
        _mock_comments: MagicMock,
        provider: GitHubProvider,
    ) -> None:
        """get_pr_review_status should parse committedDate into latest_commit_pushed_at."""

        mock_run.return_value = MagicMock(
            stdout=self._make_graphql_response(committed_date="2025-06-01T12:00:00Z")
        )

        status = provider.get_pr_review_status(42)

        assert status.latest_commit_pushed_at is not None
        assert status.latest_commit_pushed_at.year == 2025
        assert status.latest_commit_pushed_at.month == 6
        assert status.latest_commit_pushed_at.day == 1
        assert status.latest_commit_pushed_at.tzinfo == UTC

    @patch("wade.providers.github.GitHubProvider.get_pr_issue_comments", return_value=[])
    @patch("wade.providers.github.GitHubProvider.get_repo_nwo", return_value="owner/repo")
    @patch("wade.providers.github.run")
    def test_latest_commit_pushed_at_none_when_missing(
        self,
        mock_run: MagicMock,
        _mock_nwo: MagicMock,
        _mock_comments: MagicMock,
        provider: GitHubProvider,
    ) -> None:
        """latest_commit_pushed_at should be None when no commits in response."""
        mock_run.return_value = MagicMock(stdout=self._make_graphql_response(committed_date=None))

        status = provider.get_pr_review_status(42)

        assert status.latest_commit_pushed_at is None
