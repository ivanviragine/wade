"""Tests for worktree removal with force parameter."""

from pathlib import Path
from unittest.mock import patch

from ghaiw.git.worktree import remove_worktree


def test_remove_worktree_default_uses_force(tmp_path: Path) -> None:
    """Default behavior should include --force flag."""
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "worktree"
    repo_root.mkdir()
    worktree_path.mkdir()

    with patch("ghaiw.git.worktree._run_git") as mock_run_git:
        remove_worktree(repo_root, worktree_path)

        # Verify _run_git was called with --force
        mock_run_git.assert_called_once()
        args = mock_run_git.call_args[0]
        assert "worktree" in args
        assert "remove" in args
        assert "--force" in args
        assert str(worktree_path) in args


def test_remove_worktree_force_false_omits_flag(tmp_path: Path) -> None:
    """When force=False, --force flag should be omitted."""
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "worktree"
    repo_root.mkdir()
    worktree_path.mkdir()

    with patch("ghaiw.git.worktree._run_git") as mock_run_git:
        remove_worktree(repo_root, worktree_path, force=False)

        # Verify _run_git was called without --force
        mock_run_git.assert_called_once()
        args = mock_run_git.call_args[0]
        assert "worktree" in args
        assert "remove" in args
        assert "--force" not in args
        assert str(worktree_path) in args


def test_remove_worktree_force_true_explicit(tmp_path: Path) -> None:
    """When force=True explicitly, --force flag should be included."""
    repo_root = tmp_path / "repo"
    worktree_path = tmp_path / "worktree"
    repo_root.mkdir()
    worktree_path.mkdir()

    with patch("ghaiw.git.worktree._run_git") as mock_run_git:
        remove_worktree(repo_root, worktree_path, force=True)

        # Verify _run_git was called with --force
        mock_run_git.assert_called_once()
        args = mock_run_git.call_args[0]
        assert "worktree" in args
        assert "remove" in args
        assert "--force" in args
        assert str(worktree_path) in args
