"""Regression tests for gh retry + create_pr number handling (#357, B4/B5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.git.pr import GhCliError, _is_transient_gh_error, create_pr, merge_pr


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestTransientDetection:
    @pytest.mark.parametrize(
        "stderr",
        [
            "You have exceeded a secondary rate limit",
            "connection reset by peer",
            "HTTP 502: Bad Gateway",
            "HTTP 503 Service Unavailable",
            "i/o timeout",
        ],
    )
    def test_transient_errors(self, stderr: str) -> None:
        assert _is_transient_gh_error(stderr) is True

    @pytest.mark.parametrize(
        "stderr",
        [
            "HTTP 404: Not Found",
            "HTTP 422: Unprocessable Entity (a pull request already exists)",
            "gh auth login required",
            "no pull requests found for branch",
        ],
    )
    def test_permanent_errors(self, stderr: str) -> None:
        assert _is_transient_gh_error(stderr) is False


class TestRunGhRetry:
    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr.subprocess.run")
    def test_does_not_retry_permanent_404(self, mock_run: MagicMock, _sleep: MagicMock) -> None:
        # update_pr_body uses retries=3, but a 404 is permanent → one call only.
        mock_run.return_value = _proc(1, stderr="HTTP 404: Not Found")
        from wade.git.pr import update_pr_body

        ok = update_pr_body(Path("/repo"), 42, "body")
        assert ok is False
        assert mock_run.call_count == 1

    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr.subprocess.run")
    def test_retries_transient_5xx(self, mock_run: MagicMock, _sleep: MagicMock) -> None:
        # Two transient failures then success.
        mock_run.side_effect = [
            _proc(1, stderr="HTTP 502: Bad Gateway"),
            _proc(1, stderr="HTTP 502: Bad Gateway"),
            _proc(0, stdout=""),
        ]
        from wade.git.pr import update_pr_body

        ok = update_pr_body(Path("/repo"), 42, "body")
        assert ok is True
        assert mock_run.call_count == 3


class TestMergePrStateGuard:
    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr._get_pr_state", return_value="MERGED")
    @patch("wade.git.pr.subprocess.run")
    def test_already_merged_is_success_not_retried(
        self, mock_run: MagicMock, _state: MagicMock, _sleep: MagicMock
    ) -> None:
        # First merge attempt "fails" transiently, but the PR is already MERGED
        # remotely → treat as success, never re-attempt the irreversible merge.
        mock_run.return_value = _proc(1, stderr="connection reset")
        merge_pr(Path("/repo"), 7)  # must not raise
        assert mock_run.call_count == 1  # no retry once state == MERGED

    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr._get_pr_state", return_value="OPEN")
    @patch("wade.git.pr.subprocess.run")
    def test_permanent_failure_raises(
        self, mock_run: MagicMock, _state: MagicMock, _sleep: MagicMock
    ) -> None:
        mock_run.return_value = _proc(1, stderr="HTTP 422: not mergeable")
        with pytest.raises(GhCliError):
            merge_pr(Path("/repo"), 7)
        assert mock_run.call_count == 1  # permanent → no retry


class TestCreatePrNumber:
    @patch("wade.git.pr._get_pr_info_from_url", return_value=None)
    @patch("wade.git.pr.subprocess.run")
    def test_parses_number_from_url_fallback(self, mock_run: MagicMock, _info: MagicMock) -> None:
        mock_run.return_value = _proc(0, stdout="https://github.com/o/r/pull/123")
        result = create_pr(Path("/repo"), "t", "b", "main")
        assert result == {"number": 123, "url": "https://github.com/o/r/pull/123"}

    @patch("wade.git.pr._get_pr_info_from_url", return_value=None)
    @patch("wade.git.pr.subprocess.run")
    def test_returns_none_when_number_undeterminable(
        self, mock_run: MagicMock, _info: MagicMock
    ) -> None:
        # No parseable /pull/<n> in the output → never fabricate #0.
        mock_run.return_value = _proc(0, stdout="created (no url)")
        result = create_pr(Path("/repo"), "t", "b", "main")
        assert result is None
