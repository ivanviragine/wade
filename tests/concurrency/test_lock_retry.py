"""Concurrency lane — C3: shared git lock contention is retried, not fatal (#357).

N parallel ``wade implement`` sessions contend on the shared index/ref locks
during startup catchup. A pre-created ``index.lock`` makes the first git attempt
fail; wade's retry wrapper must detect the lock and retry (here the lock is
released between attempts to model the competing process finishing) rather than
crashing the session. Deterministic — the "release" happens in the patched
sleep, no threads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import wade.git.repo as git_repo
from wade.git import stash as git_stash
from wade.git.repo import GitError, _run_git_with_retry

pytestmark = pytest.mark.concurrency


def test_autostash_survives_index_lock_contention(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A tracked change to stash.
    (tmp_git_repo / "README.md").write_text("# changed\n")

    lock = tmp_git_repo / ".git" / "index.lock"
    lock.write_text("")  # a competing process holds the index lock

    sleeps = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        # Model the competing process finishing: release the lock between the
        # failed attempt and the retry.
        sleeps["n"] += 1
        lock.unlink(missing_ok=True)

    monkeypatch.setattr(git_repo.time, "sleep", fake_sleep)

    # create_named_stash goes through _run_git_with_retry; with the lock present
    # the first attempt fails ("Unable to create '.../index.lock'"), the retry
    # (after the lock is released) succeeds.
    sha, _msg = git_stash.create_named_stash("catchup", "main", tmp_git_repo)

    assert sleeps["n"] >= 1  # a retry actually happened
    assert len(sha) == 40  # the stash was created — not a fatal failure


def test_persistent_lock_eventually_raises_after_retries(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A lock that never clears: retries are exhausted and the error is surfaced
    # (fatal only AFTER retrying, never on the first attempt).
    lock = tmp_git_repo / ".git" / "index.lock"
    lock.write_text("")

    attempts = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        attempts["n"] += 1  # lock stays held

    monkeypatch.setattr(git_repo.time, "sleep", fake_sleep)

    (tmp_git_repo / "README.md").write_text("# changed\n")
    with pytest.raises(GitError):
        _run_git_with_retry("add", "README.md", cwd=tmp_git_repo, retries=3, base_delay=0.001)
    assert attempts["n"] >= 2  # it retried before giving up
