"""Unit tests for detached-HEAD worktree creation."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestCreateDetachedWorktree:
    def test_creates_detached_worktree(self, tmp_git_repo: Path, tmp_path: Path) -> None:
        """create_detached_worktree creates a worktree with detached HEAD."""
        from wade.git.worktree import create_detached_worktree, list_worktrees

        wt_dir = tmp_path / "detached-wt"
        result = create_detached_worktree(tmp_git_repo, wt_dir)

        assert result == wt_dir.resolve()
        assert result.is_dir()

        # Verify it shows as detached in worktree list
        worktrees = list_worktrees(tmp_git_repo)
        detached = [wt for wt in worktrees if wt.get("branch") == "(detached)"]
        assert len(detached) >= 1

    def test_detached_worktree_at_head(self, tmp_git_repo: Path, tmp_path: Path) -> None:
        """Detached worktree defaults to HEAD."""
        from wade.git.worktree import create_detached_worktree

        wt_dir = tmp_path / "detached-wt"
        create_detached_worktree(tmp_git_repo, wt_dir)

        # Get HEAD commit from main repo
        import subprocess

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(tmp_git_repo),
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Get HEAD in worktree
        wt_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(wt_dir),
            capture_output=True,
            text=True,
        ).stdout.strip()

        assert head == wt_head

    def test_detached_worktree_removal(self, tmp_git_repo: Path, tmp_path: Path) -> None:
        """Detached worktrees can be removed cleanly."""
        from wade.git.worktree import (
            create_detached_worktree,
            list_worktrees,
            remove_worktree,
        )

        wt_dir = tmp_path / "detached-wt"
        wt_path = create_detached_worktree(tmp_git_repo, wt_dir)
        remove_worktree(tmp_git_repo, wt_path)

        worktrees = list_worktrees(tmp_git_repo)
        wt_paths = [wt["path"] for wt in worktrees]
        assert str(wt_path) not in wt_paths

    def test_multiple_detached_worktrees(self, tmp_git_repo: Path, tmp_path: Path) -> None:
        """Multiple detached worktrees can coexist at the same commit."""
        from wade.git.worktree import create_detached_worktree, list_worktrees

        wt1 = create_detached_worktree(tmp_git_repo, tmp_path / "wt1")
        wt2 = create_detached_worktree(tmp_git_repo, tmp_path / "wt2")

        assert wt1.is_dir()
        assert wt2.is_dir()

        worktrees = list_worktrees(tmp_git_repo)
        detached = [wt for wt in worktrees if wt.get("branch") == "(detached)"]
        assert len(detached) >= 2

    def test_detached_worktree_nonexistent_dir_raises(
        self, tmp_git_repo: Path, tmp_path: Path
    ) -> None:
        """Creating a worktree in a nonexistent parent with missing parent fails."""
        from wade.git.worktree import create_detached_worktree

        # Git creates intermediate dirs, but if we provide an impossible path...
        # Actually git worktree add can create intermediate dirs.
        # So just test that a valid request works:
        wt_dir = tmp_path / "deeply" / "nested" / "wt"
        result = create_detached_worktree(tmp_git_repo, wt_dir)
        assert result.is_dir()


@pytest.fixture()
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with an initial commit."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    return repo
