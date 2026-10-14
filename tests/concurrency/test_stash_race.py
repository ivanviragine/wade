"""Concurrency lane — A1: shared stash-stack race (#357).

Two sibling worktrees share ONE stash stack ($GIT_COMMON_DIR). A stash pushed
in worktree B shifts the positions of worktree A's stash. wade must restore its
own stash by content-addressed COMMIT SHA, never by a held positional ref, so
the position shift can't restore someone else's work. Forced interleaving via
real git — no threads.
"""

from __future__ import annotations

import shlex
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


def _repo_with_two_worktrees(tmp_path: Path, repo_name: str = "repo") -> tuple[Path, Path, Path]:
    repo = tmp_path / repo_name
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


@pytest.mark.parametrize("pull_succeeds", [True, False])
def test_post_merge_restores_its_stash_after_sibling_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pull_succeeds: bool,
) -> None:
    from wade.ui import console

    for width in (40, 80):
        case = tmp_path / str(width)
        case.mkdir()
        with monkeypatch.context() as local:
            local.setattr(console.out, "_width", width)
            local.setattr(console.err, "_width", width)
            _post_merge_success(case, local, capsys, pull_succeeds)


def _post_merge_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pull_succeeds: bool,
) -> None:
    from wade.services.implementation_service import lifecycle

    repo, wt_a, wt_b = _repo_with_two_worktrees(
        tmp_path, "long checkout " * 8 + "owner's [red] ; $(touch sentinel)"
    )
    (repo / "file.txt").write_text("MAIN USER WORK\n")
    owned: list[str] = []
    sibling: list[str] = []
    calls: list[Path] = []

    def pull(cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(cwd)
        assert cwd == repo
        if len(calls) == 1:
            return subprocess.CompletedProcess([], 1, "", lifecycle._LOCAL_CHANGES_MARKER)
        owned.append(_git(repo, "stash", "list", "--format=%H").splitlines()[0])
        (wt_b / "file.txt").write_text("SIBLING USER WORK\n")
        sibling.append(create_named_stash("sibling", "feat-b", wt_b)[0])
        return subprocess.CompletedProcess([], 0 if pull_succeeds else 1, "", "pull unavailable")

    monkeypatch.setattr(lifecycle.git_repo, "pull_ff_only", pull)
    # Enter through a linked worktree: all mutations must still target main.
    lifecycle._pull_main_after_merge(wt_a)

    assert (repo / "file.txt").read_text() == "MAIN USER WORK\n"
    stack = _git(repo, "stash", "list", "--format=%H").splitlines()
    assert sibling[0] in stack
    assert owned[0] in stack  # Conservative cleanup never drops another entry.
    assert apply_stash_by_sha(sibling[0], wt_b).returncode == 0
    assert (wt_b / "file.txt").read_text() == "SIBLING USER WORK\n"
    output = capsys.readouterr()
    assert "retained" in output.err.lower()
    assert owned[0] in output.err.replace("\n", "").replace(" ", "")
    commands = _recovery_commands(output.out, repo, owned[0], False)
    _execute_inspection_commands(commands, repo, tmp_path, sibling[0], owned[0])
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["conflict", "missing-object", "removed-entry"])
def test_post_merge_restore_failures_never_fall_back_to_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    from wade.ui import console

    for width in (40, 80):
        case = tmp_path / str(width)
        case.mkdir()
        with monkeypatch.context() as local:
            local.setattr(console.out, "_width", width)
            local.setattr(console.err, "_width", width)
            _post_merge_failure(case, local, capsys, failure)


def _post_merge_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    from wade.services.implementation_service import lifecycle

    repo, wt_a, wt_b = _repo_with_two_worktrees(
        tmp_path, "long checkout " * 8 + "owner's [red] ; $(touch sentinel)"
    )
    (repo / "file.txt").write_text("MAIN USER WORK\n")
    owned: list[str] = []
    sibling: list[str] = []
    pulls = 0

    def pull(cwd: Path) -> subprocess.CompletedProcess[str]:
        nonlocal pulls
        pulls += 1
        assert cwd == repo
        if pulls == 1:
            return subprocess.CompletedProcess([], 1, "", lifecycle._LOCAL_CHANGES_MARKER)
        owned.append(_git(repo, "stash", "list", "--format=%H").splitlines()[0])
        (wt_b / "file.txt").write_text("SIBLING USER WORK\n")
        sibling.append(create_named_stash("sibling", "feat-b", wt_b)[0])
        if failure == "conflict":
            (repo / "file.txt").write_text("PULLED MAIN CHANGE\n")
            _git(repo, "add", "file.txt")
            _git(repo, "commit", "-m", "simulate pulled content")
        else:
            # Removing the reflog entry alone still leaves the object resolvable.
            _git(repo, "stash", "drop", "stash@{1}")
            if failure == "missing-object":
                _git(repo, "prune", "--expire=now")
                probe = subprocess.run(
                    ["git", "cat-file", "-e", owned[0]], cwd=repo, capture_output=True
                )
                assert probe.returncode != 0
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(lifecycle.git_repo, "pull_ff_only", pull)
    lifecycle._pull_main_after_merge(wt_a)

    content = (repo / "file.txt").read_text()
    assert "SIBLING USER WORK" not in content
    stack = _git(repo, "stash", "list", "--format=%H").splitlines()
    assert sibling[0] in stack
    if failure == "conflict":
        assert "MAIN USER WORK" in content and "PULLED MAIN CHANGE" in content
        assert _git(repo, "ls-files", "-u")
        assert owned[0] in stack
    elif failure == "missing-object":
        assert content == "base\n"
        assert owned[0] not in stack
    else:
        assert content == "MAIN USER WORK\n"
        assert owned[0] not in stack
    assert apply_stash_by_sha(sibling[0], wt_b).returncode == 0
    assert (wt_b / "file.txt").read_text() == "SIBLING USER WORK\n"
    output = capsys.readouterr()
    assert owned[0] in output.err.replace("\n", "").replace(" ", "")
    commands = _recovery_commands(output.out, repo, owned[0], failure != "removed-entry")
    _execute_inspection_commands(commands, repo, tmp_path, sibling[0], owned[0])
    if failure == "removed-entry":
        assert "any remaining entry" in " ".join(output.err.split())
    assert "stash drop " + owned[0] not in output.out + output.err
    assert pulls == 2


def _recovery_commands(output: str, repo: Path, sha: str, failed: bool) -> list[str]:
    """Select only emitted standalone commands, never decoy debug text."""
    decoy = f"debug cwd={repo} stash apply {sha}"
    lines = [line for line in (output + "\n" + decoy).splitlines() if line.startswith("git ")]
    argv = [["git", "-C", str(repo), "stash", "list", "--format=%H %gd %gs"]]
    if failed:
        argv = [
            ["git", "-C", str(repo), "status"],
            ["git", "-C", str(repo), "stash", "apply", sha],
            *argv,
        ]
    assert len(lines) == len(argv)
    assert [shlex.split(line) for line in lines] == argv
    return lines


def _execute_inspection_commands(
    commands: list[str], repo: Path, outside: Path, sibling_sha: str, owned_sha: str
) -> None:
    before = (
        _git(repo, "stash", "list", "--format=%H %gd %gs"),
        _git(repo, "status", "--porcelain"),
        (repo / "file.txt").read_bytes(),
        _git(repo, "ls-files", "-u"),
    )
    for command in commands:
        argv = shlex.split(command)
        if argv[3:5] == ["stash", "apply"]:
            # Never retry an unresolved conflict or unavailable object.
            assert argv[-1] == owned_sha
            continue
        result = subprocess.run(
            ["sh", "-c", command], cwd=outside, capture_output=True, text=True, check=True
        )
        if argv[3:5] == ["stash", "list"]:
            assert result.stdout == before[0]
            assert sibling_sha in result.stdout
            assert "stash@{" in result.stdout and "wade-autostash/" in result.stdout
        else:
            assert "On branch main" in result.stdout
    after = (
        _git(repo, "stash", "list", "--format=%H %gd %gs"),
        _git(repo, "status", "--porcelain"),
        (repo / "file.txt").read_bytes(),
        _git(repo, "ls-files", "-u"),
    )
    assert after == before
    assert not (outside / "sentinel").exists()


@pytest.mark.parametrize("width", [40, 80])
def test_wrapped_hint_cannot_pass_command_assertions_using_debug_decoys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], width: int
) -> None:
    from wade.ui import console

    repo = tmp_path / ("long checkout with spaces " * 8)
    monkeypatch.setattr(console.out, "_width", width)
    console.hint(f"Inspect git -C {shlex.quote(str(repo))} status and git stash apply owned")
    legacy = capsys.readouterr().out
    assert "\n" in legacy
    with pytest.raises(AssertionError):
        _recovery_commands(legacy, repo, "owned", True)
