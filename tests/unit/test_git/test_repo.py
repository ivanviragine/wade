"""Tests for git.repo — stash helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wade.git.repo import stash, stash_pop


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
