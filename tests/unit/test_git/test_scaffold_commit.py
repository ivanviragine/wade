"""Tests for create_scaffold_commit."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wade.git.branch import commits_ahead, create_branch, create_scaffold_commit


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit on main."""
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    run("init", "-b", "main")
    run("config", "user.email", "test@test.com")
    run("config", "user.name", "Test")
    (tmp_path / "README.md").write_text("init")
    run("add", ".")
    run("commit", "-m", "initial")
    return tmp_path


def test_scaffold_commit_adds_one_commit(git_repo: Path) -> None:
    """Scaffold commit should put the branch exactly 1 commit ahead of main."""
    create_branch(git_repo, "feat/1-test", "main")
    assert commits_ahead(git_repo, "feat/1-test", "main") == 0

    create_scaffold_commit(git_repo, "feat/1-test", "chore: scaffold")

    assert commits_ahead(git_repo, "feat/1-test", "main") == 1


def test_scaffold_commit_does_not_change_tree(git_repo: Path) -> None:
    """Scaffold commit should not alter the file tree (empty commit)."""
    create_branch(git_repo, "feat/2-test", "main")

    main_tree = subprocess.run(
        ["git", "rev-parse", "main^{tree}"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    create_scaffold_commit(git_repo, "feat/2-test", "chore: scaffold")

    branch_tree = subprocess.run(
        ["git", "rev-parse", "feat/2-test^{tree}"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert main_tree == branch_tree


def test_scaffold_commit_preserves_current_branch(git_repo: Path) -> None:
    """Scaffold commit should not switch the checked-out branch."""
    create_branch(git_repo, "feat/3-test", "main")

    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    create_scaffold_commit(git_repo, "feat/3-test", "chore: scaffold")

    after = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert current == after
