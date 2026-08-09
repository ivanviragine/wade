"""Tests for PR title editing in git/pr.py (update_pr_title)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.pr import update_pr_title


class TestUpdatePrTitle:
    @patch("wade.git.pr._run_gh")
    def test_success(self, mock_gh: MagicMock) -> None:
        mock = MagicMock()
        mock.returncode = 0
        mock_gh.return_value = mock

        result = update_pr_title(Path("/repo"), 42, "feat: do the thing")
        assert result is True
        mock_gh.assert_called_once_with(
            "pr",
            "edit",
            "42",
            "--title",
            "feat: do the thing",
            cwd=Path("/repo"),
            check=False,
            retries=3,
        )

    @patch("wade.git.pr._run_gh")
    def test_failure(self, mock_gh: MagicMock) -> None:
        mock = MagicMock()
        mock.returncode = 1
        mock_gh.return_value = mock

        result = update_pr_title(Path("/repo"), 42, "feat: do the thing")
        assert result is False
