"""Tests for git.repo — stash helpers, retry logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from wade.git.repo import GitError, _run_git_with_retry, diff_between, stash, stash_pop


class TestDiffBetween:
    def test_calls_git_diff_three_dot(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "diff --git a/f.py b/f.py\n+line\n"
            result = diff_between(tmp_path, "main", "HEAD")
            mock_run.assert_called_once_with("diff", "main...HEAD", cwd=tmp_path, check=False)
            assert "diff --git" in result

    def test_returns_empty_on_failure(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git") as mock_run:
            mock_run.return_value.returncode = 128
            mock_run.return_value.stdout = ""
            result = diff_between(tmp_path, "main", "HEAD")
            assert result == ""


class TestStash:
    def test_stash_calls_git_stash(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git") as mock_run:
            mock_run.return_value.returncode = 0
            result = stash(tmp_path)
            mock_run.assert_called_once_with("stash", "--quiet", cwd=tmp_path, check=False)
            assert result.returncode == 0

    def test_stash_returns_failure(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git") as mock_run:
            mock_run.return_value.returncode = 1
            result = stash(tmp_path)
            assert result.returncode == 1


class TestStashPop:
    def test_stash_pop_calls_git_stash_pop(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git") as mock_run:
            mock_run.return_value.returncode = 0
            result = stash_pop(tmp_path)
            mock_run.assert_called_once_with("stash", "pop", "--quiet", cwd=tmp_path, check=False)
            assert result.returncode == 0

    def test_stash_pop_returns_failure(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git") as mock_run:
            mock_run.return_value.returncode = 1
            result = stash_pop(tmp_path)
            assert result.returncode == 1


class TestRunGitWithRetry:
    """Tests for _run_git_with_retry — lock contention retry logic."""

    def test_succeeds_on_first_attempt(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git") as mock_run:
            mock_run.return_value.returncode = 0
            result = _run_git_with_retry("status", cwd=tmp_path)
            assert mock_run.call_count == 1
            assert result.returncode == 0

    def test_retries_on_index_lock(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.repo._run_git") as mock_run,
            patch("wade.git.repo.time.sleep") as mock_sleep,
        ):
            mock_run.side_effect = [
                GitError("Unable to create '/repo/.git/index.lock': File exists"),
                GitError("Unable to create '/repo/.git/index.lock': File exists"),
                type("FakeResult", (), {"returncode": 0})(),
            ]
            result = _run_git_with_retry("worktree", "add", cwd=tmp_path, base_delay=0.1)
            assert mock_run.call_count == 3
            assert result.returncode == 0
            assert mock_sleep.call_count == 2
            # Exponential backoff: 0.1, 0.2
            mock_sleep.assert_any_call(0.1)
            mock_sleep.assert_any_call(0.2)

    def test_raises_immediately_on_non_lock_error(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git") as mock_run:
            mock_run.side_effect = GitError("fatal: A branch named 'x' already exists.")
            with pytest.raises(GitError, match="already exists"):
                _run_git_with_retry("branch", "x", cwd=tmp_path)
            assert mock_run.call_count == 1  # No retry

    def test_raises_after_all_retries_exhausted(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.repo._run_git") as mock_run,
            patch("wade.git.repo.time.sleep"),
        ):
            lock_err = GitError("Unable to create '/repo/.git/index.lock': File exists")
            mock_run.side_effect = [lock_err, lock_err, lock_err]
            with pytest.raises(GitError, match=r"index\.lock"):
                _run_git_with_retry("worktree", "add", cwd=tmp_path, retries=3)
            assert mock_run.call_count == 3
