"""Tests that ``done`` commands print the doc-pass advisory on success (#360)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from wade.cli.main import app

runner = CliRunner()

# Console word-wraps long lines, so match a short, unwrapped substring rather
# than the full DOC_PASS_ADVISORY sentence.
ADVISORY_MARKER = "Documentation pass not confirmed"


class TestImplementationSessionDoneDocPassAdvisory:
    @patch("wade.services.implementation_service.done", return_value=True)
    def test_advisory_shown_on_success(self, _mock_done: MagicMock) -> None:
        result = runner.invoke(app, ["implementation-session", "done"])
        assert result.exit_code == 0
        assert ADVISORY_MARKER in result.output

    @patch("wade.services.implementation_service.done", return_value=False)
    def test_advisory_hidden_when_done_fails(self, _mock_done: MagicMock) -> None:
        result = runner.invoke(app, ["implementation-session", "done"])
        assert result.exit_code == 1
        assert ADVISORY_MARKER not in result.output


class TestReviewPrCommentsSessionDoneDocPassAdvisory:
    @patch("wade.services.review_service.get_review_status", return_value=None)
    @patch("wade.services.implementation_service.done", return_value=True)
    def test_advisory_shown_on_success(
        self, _mock_done: MagicMock, _mock_status: MagicMock
    ) -> None:
        result = runner.invoke(app, ["review-pr-comments-session", "done"])
        assert result.exit_code == 0
        assert ADVISORY_MARKER in result.output

    @patch("wade.services.implementation_service.done", return_value=False)
    def test_advisory_hidden_when_done_fails(self, _mock_done: MagicMock) -> None:
        result = runner.invoke(app, ["review-pr-comments-session", "done"])
        assert result.exit_code == 1
        assert ADVISORY_MARKER not in result.output

    @patch("wade.services.review_service.get_review_status")
    @patch("wade.services.implementation_service.done", return_value=True)
    def test_stale_commit_suppresses_session_complete(
        self, _mock_done: MagicMock, mock_status: MagicMock
    ) -> None:
        """#403: a bot review older than the latest commit must not print SESSION COMPLETE."""
        from datetime import UTC, datetime, timedelta

        from wade.models.review import PRReview, PRReviewStatus, ReviewBotStatus, ReviewState

        commit = datetime(2026, 8, 10, 10, 4, 20, tzinfo=UTC)
        mock_status.return_value = PRReviewStatus(
            bot_status=ReviewBotStatus.COMPLETED,
            bot_status_ts=commit - timedelta(minutes=20),
            reviews=[PRReview(author="alice", state=ReviewState.APPROVED)],
            latest_commit_pushed_at=commit,
        )
        result = runner.invoke(app, ["review-pr-comments-session", "done"])
        assert result.exit_code == 0
        assert "SESSION COMPLETE" not in result.output
        # The stale-coverage warning is surfaced instead ("reviewed" survives wrapping).
        assert "reviewed" in result.output
