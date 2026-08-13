"""Tests for reset_branch — force-move a local branch ref without a checkout (#376)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wade.git.branch import (
    commits_ahead,
    create_branch,
    create_scaffold_commit,
    reset_branch,
)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A minimal repo with ``main`` and a diverged ``develop`` (one extra commit)."""
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    run("init", "-b", "main")
    run("config", "user.email", "test@test.com")
    run("config", "user.name", "Test")
    (tmp_path / "README.md").write_text("init")
    run("add", ".")
    run("commit", "-m", "initial")
    # develop diverges from main by one commit.
    run("branch", "develop")
    run("checkout", "develop")
    (tmp_path / "feature.txt").write_text("develop-only")
    run("add", ".")
    run("commit", "-m", "develop work")
    run("checkout", "main")
    return tmp_path


def test_reset_branch_reroots_scaffold_onto_new_base(git_repo: Path) -> None:
    # A scaffold branch cut from develop carries develop's extra commit relative to
    # main. Re-rooting it on main and re-scaffolding drops that foreign ancestry.
    create_branch(git_repo, "feat/1-x", "develop")
    create_scaffold_commit(git_repo, "feat/1-x", "chore: scaffold")
    assert commits_ahead(git_repo, "feat/1-x", "main") == 2  # develop work + scaffold

    reset_branch(git_repo, "feat/1-x", "main")
    create_scaffold_commit(git_repo, "feat/1-x", "chore: scaffold")

    # Now only the scaffold commit separates the branch from main — develop's
    # commit is gone from the branch's ancestry.
    assert commits_ahead(git_repo, "feat/1-x", "main") == 1


def test_reset_branch_does_not_switch_checked_out_branch(git_repo: Path) -> None:
    create_branch(git_repo, "feat/2-x", "develop")

    reset_branch(git_repo, "feat/2-x", "main")

    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current == "main"
