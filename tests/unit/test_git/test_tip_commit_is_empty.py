"""Tests for tip_commit_is_empty.

WADE's scaffold commit is an *empty* commit (:func:`create_scaffold_commit` reuses the
parent tree). An empty tip is therefore the reliable signature of a scaffold-only branch,
used to tell a rerootable scaffold apart from real work when a branch is exactly one
commit ahead of its base (#376 review).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wade.git.branch import create_branch, create_scaffold_commit, tip_commit_is_empty


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


def _commit_change(repo: Path, branch: str, filename: str, text: str) -> None:
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    run("checkout", branch)
    (repo / filename).write_text(text)
    run("add", filename)
    run("commit", "-m", f"work: {filename}")


def test_empty_scaffold_tip_is_empty(git_repo: Path) -> None:
    """A scaffold commit changes nothing → reported as empty (a rerootable scaffold)."""
    create_branch(git_repo, "feat/1-test", "main")
    create_scaffold_commit(git_repo, "feat/1-test", "chore: scaffold")

    assert tip_commit_is_empty(git_repo, "feat/1-test") is True


def test_real_commit_tip_is_not_empty(git_repo: Path) -> None:
    """A commit that touches the tree → not empty (real work a reroot would discard)."""
    create_branch(git_repo, "feat/2-test", "main")
    _commit_change(git_repo, "feat/2-test", "impl.py", "print('work')")

    assert tip_commit_is_empty(git_repo, "feat/2-test") is False


def test_amended_scaffold_tip_is_not_empty(git_repo: Path) -> None:
    """A scaffold amended with real changes → no longer empty, so not a scaffold (#376)."""
    create_branch(git_repo, "feat/3-test", "main")
    create_scaffold_commit(git_repo, "feat/3-test", "chore: scaffold")
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=git_repo, capture_output=True, text=True, check=True
    )
    run("checkout", "feat/3-test")
    (git_repo / "impl.py").write_text("print('amended')")
    run("add", "impl.py")
    # Keep the scaffold message but add tree changes — a count/message check would still
    # call this a scaffold; the tree check correctly does not.
    run("commit", "--amend", "-m", "chore: scaffold branch for #3")

    assert tip_commit_is_empty(git_repo, "feat/3-test") is False


def test_root_commit_is_not_empty(git_repo: Path) -> None:
    """A root commit has no parent → reported False (never WADE's scaffold)."""
    assert tip_commit_is_empty(git_repo, "main") is False


def test_unresolvable_ref_returns_none(git_repo: Path) -> None:
    """An unresolvable ref cannot be measured → None so callers fail closed."""
    assert tip_commit_is_empty(git_repo, "does/not/exist") is None
