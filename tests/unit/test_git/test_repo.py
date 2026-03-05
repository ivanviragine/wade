"""Tests for git.repo — stash helpers, retry logic, exclude helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.git.repo import (
    GitError,
    _run_git_with_retry,
    list_untracked_from,
    stash,
    stash_pop,
    write_worktree_exclude,
)


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


class TestListUntrackedFrom:
    """Tests for list_untracked_from."""

    def _ok(self) -> MagicMock:
        m = MagicMock()
        m.returncode = 0
        return m

    def _fail(self) -> MagicMock:
        m = MagicMock()
        m.returncode = 1
        return m

    def test_empty_input_returns_empty(self, tmp_path: Path) -> None:
        result = list_untracked_from(tmp_path, [])
        assert result == []

    def test_tracked_file_not_returned(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git", return_value=self._ok()):
            result = list_untracked_from(tmp_path, [".wade.yml"])
        assert result == []

    def test_untracked_file_returned(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git", return_value=self._fail()):
            result = list_untracked_from(tmp_path, [".wade.yml"])
        assert result == [".wade.yml"]

    def test_nonexistent_file_returned(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git", return_value=self._fail()):
            result = list_untracked_from(tmp_path, ["nonexistent.txt"])
        assert result == ["nonexistent.txt"]

    def test_mixed_tracked_and_untracked(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git", side_effect=[self._ok(), self._fail(), self._ok()]):
            result = list_untracked_from(tmp_path, ["a.txt", "b.txt", "c.txt"])
        assert result == ["b.txt"]

    def test_calls_ls_files_error_unmatch_per_candidate(self, tmp_path: Path) -> None:
        with patch("wade.git.repo._run_git", return_value=self._ok()) as mock_git:
            list_untracked_from(tmp_path, [".wade.yml", "AGENTS.md"])
        assert mock_git.call_count == 2
        mock_git.assert_any_call(
            "ls-files", "--error-unmatch", ".wade.yml", cwd=tmp_path, check=False
        )
        mock_git.assert_any_call(
            "ls-files", "--error-unmatch", "AGENTS.md", cwd=tmp_path, check=False
        )


class TestWriteWorktreeExclude:
    """Tests for write_worktree_exclude."""

    def test_writes_exclude_file(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        with patch("wade.git.repo.get_git_dir", return_value=str(git_dir)):
            result = write_worktree_exclude(tmp_path, [".wade.yml"])
        assert result is True
        exclude = git_dir / "info" / "exclude"
        assert exclude.exists()
        assert ".wade.yml\n" in exclude.read_text(encoding="utf-8")

    def test_deduplicates_existing_patterns(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git"
        info_dir = git_dir / "info"
        info_dir.mkdir(parents=True)
        exclude = info_dir / "exclude"
        exclude.write_text(".wade.yml\n", encoding="utf-8")
        with patch("wade.git.repo.get_git_dir", return_value=str(git_dir)):
            result = write_worktree_exclude(tmp_path, [".wade.yml", "AGENTS.md"])
        assert result is True
        content = exclude.read_text(encoding="utf-8")
        assert content.count(".wade.yml") == 1
        assert "AGENTS.md" in content

    def test_empty_patterns_is_noop(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        with patch("wade.git.repo.get_git_dir", return_value=str(git_dir)):
            result = write_worktree_exclude(tmp_path, [])
        assert result is True
        assert not (git_dir / "info" / "exclude").exists()

    def test_invalid_path_returns_false(self, tmp_path: Path) -> None:
        with patch("wade.git.repo.get_git_dir", return_value=None):
            result = write_worktree_exclude(tmp_path, [".wade.yml"])
        assert result is False

    def test_creates_info_dir_if_missing(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        with patch("wade.git.repo.get_git_dir", return_value=str(git_dir)):
            write_worktree_exclude(tmp_path, ["some-file"])
        assert (git_dir / "info").is_dir()

    def test_excluded_files_absent_from_git_status(self, tmp_git_repo: Path) -> None:
        """After writing exclude, untracked file should not appear in git status."""
        untracked_file = tmp_git_repo / "secret.txt"
        untracked_file.write_text("secret\n", encoding="utf-8")

        write_worktree_exclude(tmp_git_repo, ["secret.txt"])

        import subprocess

        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=tmp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "secret.txt" not in result.stdout
