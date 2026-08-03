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
