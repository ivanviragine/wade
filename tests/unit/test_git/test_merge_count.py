"""Regression test for the real merged-commit count (#357, C5a)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.sync import merge_branch


def _proc(returncode: int, stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    return m


class TestMergeCount:
    @patch("wade.git.sync.get_current_branch", return_value="feat/1-x")
    @patch("wade.git.sync._run_git_with_retry")
    @patch("wade.git.sync._run_git")
    def test_reports_real_commit_count(
        self, mock_run: MagicMock, mock_retry: MagicMock, _branch: MagicMock
    ) -> None:
        # rev-list --count current..branch reports 4 commits to merge.
        mock_run.return_value = _proc(0, stdout="4\n")
        mock_retry.return_value = _proc(0)  # merge succeeds

        result = merge_branch(Path("/repo"), "main")
        assert result.success is True
        assert result.commits_merged == 4  # not the old 0/1 heuristic

    @patch("wade.git.sync.get_current_branch", return_value="feat/1-x")
    @patch("wade.git.sync._run_git_with_retry")
    @patch("wade.git.sync._run_git")
    def test_zero_when_already_up_to_date(
        self, mock_run: MagicMock, mock_retry: MagicMock, _branch: MagicMock
    ) -> None:
        mock_run.return_value = _proc(0, stdout="0\n")
        mock_retry.return_value = _proc(0)
        result = merge_branch(Path("/repo"), "main")
        assert result.commits_merged == 0
