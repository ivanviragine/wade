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
        pushed_date: str | None = None,
        review_threads: list[dict] | None = None,
        reviews: list[dict] | None = None,
        review_requests: list[dict] | None = None,
        reactions: list[dict] | None = None,
    ) -> str:
        """Build a minimal GraphQL JSON response for _fetch_review_status_page."""
        commit: dict[str, str] = {}
        if committed_date:
            commit["committedDate"] = committed_date
        if pushed_date:
            commit["pushedDate"] = pushed_date
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
                            "commits": {"nodes": [{"commit": commit}] if commit else []},
                            "reactions": {"nodes": reactions or []},
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
    def test_latest_commit_pushed_at_prefers_pushed_date(
        self,
        mock_run: MagicMock,
        _mock_nwo: MagicMock,
        _mock_comments: MagicMock,
        provider: GitHubProvider,
    ) -> None:
        """When both are present, pushedDate wins over committedDate.

        A commit can be authored locally well before it's pushed — using
        committedDate would make a bot's review look like it covers a commit
        the bot never actually saw.
        """
        mock_run.return_value = MagicMock(
            stdout=self._make_graphql_response(
                committed_date="2025-06-01T12:00:00Z",
                pushed_date="2025-06-02T08:00:00Z",
            )
        )

        status = provider.get_pr_review_status(42)

        assert status.latest_commit_pushed_at is not None
        assert status.latest_commit_pushed_at.day == 2

    @patch("wade.providers.github.GitHubProvider.get_pr_issue_comments", return_value=[])
    @patch("wade.providers.github.GitHubProvider.get_repo_nwo", return_value="owner/repo")
    @patch("wade.providers.github.run")
    def test_latest_commit_pushed_at_falls_back_to_committed_date(
        self,
        mock_run: MagicMock,
        _mock_nwo: MagicMock,
        _mock_comments: MagicMock,
        provider: GitHubProvider,
    ) -> None:
        """Falls back to committedDate when GitHub can't report a pushedDate."""
        mock_run.return_value = MagicMock(
            stdout=self._make_graphql_response(
                committed_date="2025-06-01T12:00:00Z",
                pushed_date=None,
            )
        )

        status = provider.get_pr_review_status(42)

        assert status.latest_commit_pushed_at is not None
        assert status.latest_commit_pushed_at.day == 1

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

    @patch("wade.providers.github.GitHubProvider.get_pr_issue_comments", return_value=[])
    @patch("wade.providers.github.GitHubProvider.get_repo_nwo", return_value="owner/repo")
    @patch("wade.providers.github.run")
    def test_codex_review_classified_as_bot_via_typename(
        self,
        mock_run: MagicMock,
        _mock_nwo: MagicMock,
        _mock_comments: MagicMock,
        provider: GitHubProvider,
    ) -> None:
        """chatgpt-codex-connector is classified is_bot=True via __typename == 'Bot'.

        Its login matches none of the login-heuristic patterns, so a PENDING Codex
        review must also surface as ReviewBotStatus.IN_PROGRESS.
        """
        mock_run.return_value = MagicMock(
            stdout=self._make_graphql_response(
                reviews=[
                    {
                        "author": {"login": "chatgpt-codex-connector", "__typename": "Bot"},
                        "state": "PENDING",
                        "body": "",
                        "submittedAt": "2026-08-10T10:00:00Z",
                    }
                ]
            )
        )

        status = provider.get_pr_review_status(42)

        from wade.models.review import ReviewBotStatus

        assert len(status.reviews) == 1
        assert status.reviews[0].is_bot is True
        # A pending bot review with no CodeRabbit marker → generic IN_PROGRESS.
        assert status.bot_status == ReviewBotStatus.IN_PROGRESS

    @patch("wade.providers.github.GitHubProvider.get_pr_issue_comments", return_value=[])
    @patch("wade.providers.github.GitHubProvider.get_repo_nwo", return_value="owner/repo")
    @patch("wade.providers.github.run")
    def test_login_heuristic_still_classifies_bracket_bot(
        self,
        mock_run: MagicMock,
        _mock_nwo: MagicMock,
        _mock_comments: MagicMock,
        provider: GitHubProvider,
    ) -> None:
        """A '[bot]'-suffixed author still classifies via the login fallback (no __typename)."""
        mock_run.return_value = MagicMock(
            stdout=self._make_graphql_response(
                reviews=[
                    {
                        "author": {"login": "coderabbitai[bot]"},
                        "state": "COMMENTED",
                        "body": "",
                        "submittedAt": "2026-08-10T10:00:00Z",
                    }
                ]
            )
        )

        status = provider.get_pr_review_status(42)

        assert status.reviews[0].is_bot is True

    @patch("wade.providers.github.GitHubProvider.get_repo_nwo", return_value="owner/repo")
    @patch("wade.providers.github.run")
    def test_bot_status_ts_populated_from_coderabbit_comment(
        self,
        mock_run: MagicMock,
        _mock_nwo: MagicMock,
        provider: GitHubProvider,
    ) -> None:
        """bot_status_ts comes from the matched CodeRabbit summary comment's updated_at."""
        from datetime import datetime

        from wade.models.review import PRComment

        ts = datetime(2026, 8, 10, 9, 43, 24, tzinfo=UTC)
        mock_run.return_value = MagicMock(stdout=self._make_graphql_response())

        with patch.object(
            GitHubProvider,
            "get_pr_issue_comments",
            return_value=[
                PRComment(
                    login="coderabbitai[bot]",
                    body="<!-- summarize by coderabbit.ai -->\nWalkthrough",
                    updated_at=ts,
                )
            ],
        ):
            status = provider.get_pr_review_status(42)

        from wade.models.review import ReviewBotStatus

        assert status.bot_status == ReviewBotStatus.COMPLETED
        assert status.bot_status_ts == ts

    @patch("wade.providers.github.GitHubProvider.get_repo_nwo", return_value="owner/repo")
    @patch("wade.providers.github.run")
    def test_pending_bot_overrides_completed_coderabbit_marker(
        self,
        mock_run: MagicMock,
        _mock_nwo: MagicMock,
        provider: GitHubProvider,
    ) -> None:
        """A pending Codex/Copilot review must not be masked by a CodeRabbit COMPLETED marker.

        Regression test: previously the generic pending-bot check only ran when
        no CodeRabbit signal was present at all, so a completed CodeRabbit
        summary would suppress a still-pending review from another bot.
        """
        from wade.models.review import PRComment, ReviewBotStatus

        mock_run.return_value = MagicMock(
            stdout=self._make_graphql_response(
                reviews=[
                    {
                        "author": {"login": "chatgpt-codex-connector", "__typename": "Bot"},
                        "state": "PENDING",
                        "body": "",
                        "submittedAt": "2026-08-10T10:00:00Z",
                    }
                ]
            )
        )

        with patch.object(
            GitHubProvider,
            "get_pr_issue_comments",
            return_value=[
                PRComment(
                    login="coderabbitai[bot]",
                    body="<!-- summarize by coderabbit.ai -->\nWalkthrough",
                    updated_at=None,
                )
            ],
        ):
            status = provider.get_pr_review_status(42)

        assert status.bot_status == ReviewBotStatus.IN_PROGRESS

    @patch("wade.providers.github.GitHubProvider.get_pr_issue_comments", return_value=[])
    @patch("wade.providers.github.GitHubProvider.get_repo_nwo", return_value="owner/repo")
    @patch("wade.providers.github.run")
    def test_bot_reactions_parsed_and_normalized(
        self,
        mock_run: MagicMock,
        _mock_nwo: MagicMock,
        _mock_comments: MagicMock,
        provider: GitHubProvider,
    ) -> None:
        """PR-level bot reactions are parsed; GraphQL enum content is lowercased (#448)."""
        mock_run.return_value = MagicMock(
            stdout=self._make_graphql_response(
                reactions=[
                    {"content": "THUMBS_UP", "user": {"login": "chatgpt-codex-connector[bot]"}},
                    {"content": "EYES", "user": {"login": "coderabbitai[bot]"}},
                ]
            )
        )

        status = provider.get_pr_review_status(42)

        assert len(status.bot_reactions) == 2
        assert status.bot_reactions[0].login == "chatgpt-codex-connector[bot]"
        assert status.bot_reactions[0].content == "thumbs_up"
        assert status.bot_reactions[0].is_acknowledgement
        assert status.bot_reactions[1].content == "eyes"

    @patch("wade.providers.github.GitHubProvider.get_pr_issue_comments", return_value=[])
    @patch("wade.providers.github.GitHubProvider.get_repo_nwo", return_value="owner/repo")
    @patch("wade.providers.github.run")
    def test_human_reactions_dropped(
        self,
        mock_run: MagicMock,
        _mock_nwo: MagicMock,
        _mock_comments: MagicMock,
        provider: GitHubProvider,
    ) -> None:
        """Only bot-actor reactions are stored — human reactions are ignored (#448)."""
        mock_run.return_value = MagicMock(
            stdout=self._make_graphql_response(
                reactions=[
                    {"content": "THUMBS_UP", "user": {"login": "octocat"}},
                    {"content": "ROCKET", "user": {"login": "chatgpt-codex-connector[bot]"}},
                ]
            )
        )

        status = provider.get_pr_review_status(42)

        assert [r.login for r in status.bot_reactions] == ["chatgpt-codex-connector[bot]"]

    @patch("wade.providers.github.GitHubProvider.get_pr_issue_comments", return_value=[])
    @patch("wade.providers.github.GitHubProvider.get_repo_nwo", return_value="owner/repo")
    @patch("wade.providers.github.run")
    def test_bracketless_bot_reaction_login_kept(
        self,
        mock_run: MagicMock,
        _mock_nwo: MagicMock,
        _mock_comments: MagicMock,
        provider: GitHubProvider,
    ) -> None:
        """A bracket-less known-bot reaction login is kept via login_is_known_bot (#448).

        GraphQL can surface a Bot actor without the ``[bot]`` suffix (as it does for
        review authors); the reactions filter must not drop it.
        """
        mock_run.return_value = MagicMock(
            stdout=self._make_graphql_response(
                reactions=[
                    {"content": "THUMBS_UP", "user": {"login": "chatgpt-codex-connector"}},
                ]
            )
        )

        status = provider.get_pr_review_status(42)

        assert [r.login for r in status.bot_reactions] == ["chatgpt-codex-connector"]
        assert status.bot_reactions[0].is_acknowledgement


class TestGetPrIssueComments:
    """Tests for get_pr_issue_comments() — including updated_at projection/parsing."""

    @patch("wade.providers.github.GitHubProvider.get_repo_nwo", return_value="owner/repo")
    @patch("wade.providers.github.run")
    def test_updated_at_parsed_into_prcomment(
        self,
        mock_run: MagicMock,
        _mock_nwo: MagicMock,
        provider: GitHubProvider,
    ) -> None:
        """The API's updated_at string is parsed into PRComment.updated_at as a datetime."""
        from datetime import datetime

        mock_run.return_value = MagicMock(
            stdout=json.dumps(
                [
                    {
                        "login": "coderabbitai[bot]",
                        "body": "Walkthrough",
                        "updated_at": "2026-08-10T09:43:24Z",
                    },
                    {"login": "octocat", "body": "lgtm", "updated_at": None},
                ]
            )
        )

        comments = provider.get_pr_issue_comments(42)

        assert comments[0].updated_at == datetime(2026, 8, 10, 9, 43, 24, tzinfo=UTC)
        assert comments[1].updated_at is None
