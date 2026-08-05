"""Tests for git.branch.list_branch_names — remote+local listing with retry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.git.branch import list_branch_names
from wade.git.repo import GitError


class TestListBranchNames:
    def test_unions_local_and_remote(self, tmp_path: Path) -> None:
        remote = MagicMock(stdout="origin/main\norigin/feat/1-x\n")
        local = MagicMock(stdout="main\nfeat/1-x\n")
        with patch("wade.git.branch._run_git_with_retry", side_effect=[remote, local]) as mock_run:
            result = list_branch_names(tmp_path)
        assert result == {"origin/main", "origin/feat/1-x", "main", "feat/1-x"}
        # First call lists remotes, second lists locals.
        assert mock_run.call_args_list[0].args == (
            "branch",
            "-r",
            "--format=%(refname:short)",
        )
        assert mock_run.call_args_list[1].args == ("branch", "--format=%(refname:short)")

    def test_empty_repo_returns_empty_set(self, tmp_path: Path) -> None:
        empty = MagicMock(stdout="")
        with patch("wade.git.branch._run_git_with_retry", side_effect=[empty, empty]):
            result = list_branch_names(tmp_path)
        assert result == set()

    def test_propagates_git_error(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.branch._run_git_with_retry", side_effect=GitError("lock")),
            pytest.raises(GitError),
        ):
            list_branch_names(tmp_path)
