"""Concurrency lane — A1: shared stash-stack race (#357).

Two sibling worktrees share ONE stash stack ($GIT_COMMON_DIR). A stash pushed
in worktree B shifts the positions of worktree A's stash. wade must restore its
own stash by content-addressed COMMIT SHA, never by a held positional ref, so
the position shift can't restore someone else's work. Forced interleaving via
real git — no threads.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wade.git.stash import (
    apply_stash_by_sha,
    create_named_stash,
    drop_stash_by_sha,
)

pytestmark = pytest.mark.concurrency


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _repo_with_two_worktrees(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    (repo / "file.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    wt_a = tmp_path / "wt_a"
    wt_b = tmp_path / "wt_b"
    _git(repo, "worktree", "add", "-b", "feat-a", str(wt_a))
    _git(repo, "worktree", "add", "-b", "feat-b", str(wt_b))
    return repo, wt_a, wt_b


def test_stash_restored_by_sha_survives_competing_stash(tmp_path: Path) -> None:
    _repo, wt_a, wt_b = _repo_with_two_worktrees(tmp_path)

    # Worktree A stashes its own tracked change.
    (wt_a / "file.txt").write_text("A-change\n")
    sha_a, _msg_a = create_named_stash("catchup", "feat-a", wt_a)
    assert len(sha_a) == 40
    # After stashing, A's working file is back to base.
    assert (wt_a / "file.txt").read_text() == "base\n"

    # Worktree B stashes a DIFFERENT change — same shared stack. This shifts A's
    # stash from position stash@{0} to stash@{1}; a held positional ref would now
    # point at B's work.
    (wt_b / "file.txt").write_text("B-change\n")
    sha_b, _msg_b = create_named_stash("catchup", "feat-b", wt_b)
    assert sha_b != sha_a

    # A restores by its SHA → must get A's content, never B's.
    result = apply_stash_by_sha(sha_a, wt_a)
    assert result.returncode == 0
    assert (wt_a / "file.txt").read_text() == "A-change\n"

    # And the drop resolves the current position of A's stash, not a stale one.
    assert drop_stash_by_sha(sha_a, wt_a) is True
    # B's stash is untouched and still restorable.
    assert apply_stash_by_sha(sha_b, wt_b).returncode == 0
    assert (wt_b / "file.txt").read_text() == "B-change\n"


def test_unresolvable_stash_is_reported_not_applied(tmp_path: Path) -> None:
    _repo, wt_a, _wt_b = _repo_with_two_worktrees(tmp_path)
    (wt_a / "file.txt").write_text("A-change\n")
    _sha_a, _msg = create_named_stash("catchup", "feat-a", wt_a)

    # A SHA that does not resolve to any object must fail loudly (non-zero),
    # never silently apply the wrong changes. (The all-zeros SHA is special-
    # cased by git, so use a valid-format but non-existent hash.)
    bogus = "dead" * 10
    result = apply_stash_by_sha(bogus, wt_a)
    assert result.returncode != 0
    assert drop_stash_by_sha(bogus, wt_a) is False
    # The working file is unchanged by the failed apply/drop.
    assert (wt_a / "file.txt").read_text() == "base\n"
