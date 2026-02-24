"""Tests for get_conflicted_files() in git.sync module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghaiw.git.repo import GitError
from ghaiw.git.sync import get_conflicted_files


def test_get_conflicted_files_raises_on_subprocess_error(tmp_path: Path) -> None:
    """get_conflicted_files() should raise GitError when git command fails."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with patch("ghaiw.git.sync._run_git") as mock_run_git:
        # Mock a failed git command
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stderr = "fatal: not a git repository"
        mock_run_git.return_value = mock_result

        # Should raise GitError, not return empty list
        with pytest.raises(GitError) as exc_info:
            get_conflicted_files(repo_root)

        assert "git diff --name-only --diff-filter=U failed" in str(exc_info.value)
        assert "exit 128" in str(exc_info.value)
        assert "fatal: not a git repository" in str(exc_info.value)


def test_get_conflicted_files_returns_list_on_success(tmp_path: Path) -> None:
    """get_conflicted_files() should return list of conflicted files on success."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with patch("ghaiw.git.sync._run_git") as mock_run_git:
        # Mock a successful git command with conflict output
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "file1.py\nfile2.txt\ndir/file3.md\n"
        mock_run_git.return_value = mock_result

        result = get_conflicted_files(repo_root)

        assert result == ["file1.py", "file2.txt", "dir/file3.md"]


def test_get_conflicted_files_empty_on_no_conflicts(tmp_path: Path) -> None:
    """get_conflicted_files() should return empty list when no conflicts exist."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with patch("ghaiw.git.sync._run_git") as mock_run_git:
        # Mock a successful git command with no output
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_run_git.return_value = mock_result

        result = get_conflicted_files(repo_root)

        assert result == []


def test_get_conflicted_files_filters_empty_lines(tmp_path: Path) -> None:
    """get_conflicted_files() should filter out empty lines from output."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with patch("ghaiw.git.sync._run_git") as mock_run_git:
        # Mock output with trailing newlines and blank lines
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "file1.py\n\nfile2.txt\n\n"
        mock_run_git.return_value = mock_result

        result = get_conflicted_files(repo_root)

        assert result == ["file1.py", "file2.txt"]
        assert "" not in result


def test_get_conflicted_files_calls_git_with_correct_args(tmp_path: Path) -> None:
    """get_conflicted_files() should call git diff with correct arguments."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with patch("ghaiw.git.sync._run_git") as mock_run_git:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_run_git.return_value = mock_result

        get_conflicted_files(repo_root)

        # Verify _run_git was called with correct arguments
        mock_run_git.assert_called_once_with(
            "diff",
            "--name-only",
            "--diff-filter=U",
            cwd=repo_root,
            check=False,
        )
