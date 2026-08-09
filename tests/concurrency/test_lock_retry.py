"""Concurrency lane — C3: shared git lock contention is retried, not fatal (#357).

N parallel ``wade implement`` sessions contend on the shared index/ref locks
during startup catchup. A pre-created ``index.lock`` makes the first git attempt
fail; wade's retry wrapper must detect the lock and retry (here the lock is
released between attempts to model the competing process finishing) rather than
crashing the session. Deterministic — the "release" happens in the patched
sleep, no threads.

Git-version note (#374): how ``git stash push`` reports a held index lock is
version-dependent. Verified against git 2.43.0, where it exits non-zero with
**empty stdout AND stderr** (nothing for ``_LOCK_PATTERNS`` to match), and CI's
newer git (~2.50), which prints ``could not write index``. The probe path
(``_index_lock_present``) is version-independent, so the silent-stderr
regression test below reproduces the 2.43.0 signature on any host git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import wade.git.repo as git_repo
from wade.git import stash as git_stash
from wade.git.repo import GitError, _run_git, _run_git_with_retry

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


def test_autostash_retries_on_silent_stderr_via_lock_probe(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reproduces the older-git (2.43.0) signature on any host git: `stash push`
    # under a held index.lock fails with empty stdout AND stderr, so there is
    # nothing for _LOCK_PATTERNS to match. The retry must fire via the direct
    # lock-file probe (probe_index_lock=True), not stderr matching (#374).
    (tmp_git_repo / "README.md").write_text("# changed\n")

    lock = tmp_git_repo / ".git" / "index.lock"
    lock.write_text("")  # a competing process holds the index lock

    real_run_git = _run_git
    pushes = {"n": 0}

    def fake_run_git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
        # Only the FIRST stash push simulates the silent-stderr failure; the
        # rev-parse probe calls and the retry push itself delegate to real git.
        # (Patch wade.git.repo._run_git — the name the retry wrapper and the
        # probe resolve at call time — NOT the copy imported into git_stash.)
        if args[:2] == ("stash", "push"):
            pushes["n"] += 1
            if pushes["n"] == 1:
                return subprocess.CompletedProcess(
                    ["git", *args], returncode=1, stdout="", stderr=""
                )
        return real_run_git(*args, cwd=cwd, check=check)

    monkeypatch.setattr(git_repo, "_run_git", fake_run_git)

    sleeps = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        # Model the competing process finishing: release the lock before retry.
        sleeps["n"] += 1
        lock.unlink(missing_ok=True)

    monkeypatch.setattr(git_repo.time, "sleep", fake_sleep)

    sha, _msg = git_stash.create_named_stash("catchup", "main", tmp_git_repo)

    assert sleeps["n"] >= 1  # the lock probe drove a retry despite empty stderr
    assert len(sha) == 40  # the stash was created on the retry — not fatal


def test_silent_stash_push_failure_message_is_not_blank(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A non-zero `stash push` with empty stdout+stderr must raise a GitError
    # whose message names the exit code — never the old blank
    # "git stash push failed:" reason (#374, acceptance criterion 2).
    (tmp_git_repo / "README.md").write_text("# changed\n")

    real_run_git = _run_git

    def fake_run_git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
        # Every stash push fails silently (no lock file present), so the retry
        # never fires and the failure path must still produce an actionable msg.
        if args[:2] == ("stash", "push"):
            return subprocess.CompletedProcess(["git", *args], returncode=1, stdout="", stderr="")
        return real_run_git(*args, cwd=cwd, check=check)

    monkeypatch.setattr(git_repo, "_run_git", fake_run_git)

    with pytest.raises(GitError) as excinfo:
        git_stash.create_named_stash("catchup", "main", tmp_git_repo)

    msg = str(excinfo.value)
    assert msg.rstrip().rstrip(":") != "git stash push failed"  # not a blank reason
    assert "exit 1" in msg  # exit code is surfaced
    assert "no index.lock detected" in msg  # no lock was present, so reported
