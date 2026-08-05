"""Tests for git.repo — stash helpers, retry logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from wade.git.repo import (
    GitError,
    _run_git_with_retry,
    diff_between,
    diff_worktree,
    get_main_worktree_path,
    is_head_attached,
    stash,
    stash_pop,
)


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


class TestDiffWorktree:
    def test_unstaged_calls_git_diff(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git") as mock_run:
            mock_run.return_value.stdout = "diff --git a/f.py b/f.py\n+line\n"
            result = diff_worktree(tmp_path)
            mock_run.assert_called_once_with("diff", cwd=tmp_path)
            assert "diff --git" in result

    def test_staged_appends_flag(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git") as mock_run:
            mock_run.return_value.stdout = ""
            diff_worktree(tmp_path, staged=True)
            mock_run.assert_called_once_with("diff", "--staged", cwd=tmp_path)

    def test_propagates_git_error(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.repo._run_git", side_effect=GitError("not a repo")),
            pytest.raises(GitError),
        ):
            diff_worktree(tmp_path)


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
            mock_run.assert_called_once_with("stash", "--quiet", cwd=tmp_path, check=False)
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
            mock_run.assert_called_once_with("stash", "pop", "--quiet", cwd=tmp_path, check=False)
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
            patch("wade.git.repo.time.sleep") as mock_sleep,
        ):
            lock_err = GitError("Unable to create '/repo/.git/index.lock': File exists")
            mock_run.side_effect = [lock_err, lock_err, lock_err]
            with pytest.raises(GitError, match=r"index\.lock"):
                _run_git_with_retry("worktree", "add", cwd=tmp_path, retries=3)
            assert mock_run.call_count == 3
            # The final attempt re-raises immediately — no wasted backoff sleep
            # after the last try (only retries-1 sleeps).
            assert mock_sleep.call_count == 2

    def test_raises_on_invalid_retries(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="retries must be at least 1"):
            _run_git_with_retry("status", cwd=tmp_path, retries=0)


class TestGetMainWorktreePath:
    def test_returns_none_when_not_a_worktree(self, tmp_path: Path) -> None:
        with patch("wade.git.repo.is_worktree", return_value=False):
            result = get_main_worktree_path(tmp_path)
            assert result is None

    def test_returns_main_worktree_path(self, tmp_path: Path) -> None:
        main_path = Path("/repo/main")
        porcelain_output = (
            f"worktree {main_path}\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /repo/worktrees/feat-42\n"
            "HEAD def456\n"
            "branch refs/heads/feat/42\n"
        )
        with (
            patch("wade.git.repo.is_worktree", return_value=True),
            patch("wade.git.repo._run_git") as mock_run,
        ):
            mock_run.return_value.stdout = porcelain_output
            result = get_main_worktree_path(tmp_path)
            mock_run.assert_called_once_with("worktree", "list", "--porcelain", cwd=tmp_path)
            assert result == main_path

    def test_returns_none_on_git_error(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.repo.is_worktree", return_value=True),
            patch("wade.git.repo._run_git", side_effect=GitError("git failed")),
        ):
            result = get_main_worktree_path(tmp_path)
            assert result is None

    def test_returns_none_when_no_worktree_line(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.repo.is_worktree", return_value=True),
            patch("wade.git.repo._run_git") as mock_run,
        ):
            mock_run.return_value.stdout = "HEAD abc123\nbranch refs/heads/main\n"
            result = get_main_worktree_path(tmp_path)
            assert result is None


class TestIsHeadAttached:
    """Tests for is_head_attached against real temp repos."""

    def test_returns_true_on_attached_branch(self, tmp_git_repo: Path) -> None:
        # tmp_git_repo has an initial commit and HEAD on a branch.
        assert is_head_attached(tmp_git_repo) is True

    def test_returns_false_on_detached_head(self, tmp_git_repo: Path) -> None:
        import subprocess

        subprocess.run(
            ["git", "checkout", "--detach", "HEAD"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )
        assert is_head_attached(tmp_git_repo) is False

    def test_returns_false_outside_git_repo(self, tmp_path: Path) -> None:
        # A real directory with no repo (git symbolic-ref exits non-zero) and a
        # non-existent path (git invocation raises FileNotFoundError, which
        # _run_git turns into a synthetic non-zero result) both resolve to
        # "not attached" rather than raising.
        non_git = tmp_path / "plain"
        non_git.mkdir()
        assert is_head_attached(non_git) is False
        assert is_head_attached(tmp_path / "does-not-exist") is False
