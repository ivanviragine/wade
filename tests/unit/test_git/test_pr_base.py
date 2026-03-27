"""Tests for PR base branch operations in git/pr.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wade.git.pr import get_pr_base_branch, update_pr_base


class TestUpdatePrBase:
    @patch("wade.git.pr._run_gh")
    def test_success(self, mock_gh: object) -> None:
        from unittest.mock import MagicMock

        mock = MagicMock()
        mock.returncode = 0
        mock_gh_fn = mock_gh  # type: ignore[assignment]
        mock_gh_fn.return_value = mock

        result = update_pr_base(Path("/repo"), 42, "feat/10-parent")
        assert result is True
        mock_gh_fn.assert_called_once_with(
            "pr",
            "edit",
            "42",
            "--base",
            "feat/10-parent",
            cwd=Path("/repo"),
            check=False,
        )

    @patch("wade.git.pr._run_gh")
    def test_failure(self, mock_gh: object) -> None:
        from unittest.mock import MagicMock

        mock = MagicMock()
        mock.returncode = 1
        mock_gh_fn = mock_gh  # type: ignore[assignment]
        mock_gh_fn.return_value = mock

        result = update_pr_base(Path("/repo"), 42, "feat/10-parent")
        assert result is False


class TestGetPrBaseBranch:
    @patch("wade.git.pr._run_gh")
    def test_returns_base(self, mock_gh: object) -> None:
        from unittest.mock import MagicMock

        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = '{"baseRefName": "feat/10-parent"}'
        mock_gh_fn = mock_gh  # type: ignore[assignment]
        mock_gh_fn.return_value = mock

        result = get_pr_base_branch(Path("/repo"), 42)
        assert result == "feat/10-parent"

    @patch("wade.git.pr._run_gh")
    def test_returns_none_on_failure(self, mock_gh: object) -> None:
        from unittest.mock import MagicMock

        mock = MagicMock()
        mock.returncode = 1
        mock_gh_fn = mock_gh  # type: ignore[assignment]
        mock_gh_fn.return_value = mock

        result = get_pr_base_branch(Path("/repo"), 42)
        assert result is None

    @patch("wade.git.pr._run_gh")
    def test_returns_none_on_empty_base(self, mock_gh: object) -> None:
        from unittest.mock import MagicMock

        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = '{"baseRefName": ""}'
        mock_gh_fn = mock_gh  # type: ignore[assignment]
        mock_gh_fn.return_value = mock

        result = get_pr_base_branch(Path("/repo"), 42)
        assert result is None
