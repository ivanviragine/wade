"""Real Git review recovery with disposable remotes and protected local work."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from wade.git import repo as git_repo
from wade.git.repo import GitError
from wade.models.config import ProjectConfig, ProjectSettings
from wade.services.review_service import _recover_worktree


class RecoveryRepository(BaseModel):
    origin: Path
    publisher: Path
    local: Path
    sibling: Path
    worktrees: Path
    branch: str
    old_tip: str
    remote_tip: str


class ProtectedWork(BaseModel):
    main_head: str
    sibling_head: str
    main_diff: str
    sibling_diff: str
    main_untracked: bytes
    main_ignored: bytes
    sibling_untracked: bytes
    sibling_ignored: bytes
    stashes: str
    foreign_ref: str


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _identity(repo: Path) -> None:
    _git(repo, "config", "user.name", "Recovery Test")
    _git(repo, "config", "user.email", "recovery@test.invalid")


def _commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", "test: " + name)
    return _git(repo, "rev-parse", "HEAD")


def _protected(repo: RecoveryRepository) -> ProtectedWork:
    return ProtectedWork(
        main_head=_git(repo.local, "rev-parse", "HEAD"),
        sibling_head=_git(repo.sibling, "rev-parse", "HEAD"),
        main_diff=_git(repo.local, "diff", "--binary"),
        sibling_diff=_git(repo.sibling, "diff", "--binary"),
        main_untracked=(repo.local / "precious.txt").read_bytes(),
        main_ignored=(repo.local / "ignored.txt").read_bytes(),
        sibling_untracked=(repo.sibling / "precious.txt").read_bytes(),
        sibling_ignored=(repo.sibling / "ignored.txt").read_bytes(),
        stashes=_git(repo.local, "stash", "list", "--format=%H %gd %gs"),
        foreign_ref=_git(repo.local, "rev-parse", "refs/wade/recovery/unrelated"),
    )


@pytest.fixture
def recovery_repo(tmp_path: Path) -> RecoveryRepository:
    origin = tmp_path / "origin.git"
    publisher = tmp_path / "publisher"
    local = tmp_path / "local"
    sibling = tmp_path / "sibling"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "init", "-b", "main", str(publisher))
    _identity(publisher)
    _commit(publisher, "file.txt", "base\n")
    _commit(publisher, ".gitignore", "ignored.txt\n")
    _git(publisher, "remote", "add", "origin", str(origin))
    _git(publisher, "push", "origin", "main")
    branch = "feat/42-force-pushed"
    _git(publisher, "checkout", "-b", branch)
    _commit(publisher, "old-pr.txt", "old PR work\n")
    _git(publisher, "push", "origin", branch)
    _git(tmp_path, "clone", str(origin), str(local))
    _identity(local)
    _git(local, "branch", branch, "origin/" + branch)
    removed = tmp_path / "removed"
    _git(local, "worktree", "add", str(removed), branch)
    old_tip = _commit(removed, "local-only.txt", "unpublished local work\n")
    _git(local, "worktree", "remove", str(removed))
    _git(publisher, "reset", "--hard", "main")
    remote_tip = _commit(publisher, "new-pr.txt", "replacement remote work\n")
    _git(publisher, "push", "--force", "origin", branch)
    (local / "stashed.txt").write_text("keep existing stash\n")
    _git(local, "stash", "push", "-u", "-m", "preserve existing identity")
    _git(local, "update-ref", "refs/wade/recovery/unrelated", "HEAD")
    _git(local, "worktree", "add", "-b", "topic", str(sibling), "main")
    for path in (local, sibling):
        (path / "file.txt").write_text("precious tracked edit at " + path.name + "\n")
        (path / "precious.txt").write_bytes(b"precious untracked\x00bytes")
        (path / "ignored.txt").write_bytes(b"precious ignored\x00bytes")
    return RecoveryRepository(
        origin=origin,
        publisher=publisher,
        local=local,
        sibling=sibling,
        worktrees=tmp_path / "worktrees",
        branch=branch,
        old_tip=old_tip,
        remote_tip=remote_tip,
    )


def test_force_pushed_recovery_preserves_displaced_unpublished_tip_and_other_work(
    recovery_repo: RecoveryRepository,
) -> None:
    repo = recovery_repo
    config = ProjectConfig(
        project=ProjectSettings(main_branch="main", worktrees_dir=str(repo.worktrees))
    )
    protected = _protected(repo)
    fetch_errors: list[str] = []
    actual_fetch = git_repo.fetch_ref

    def observe_real_fetch(root: Path, remote: str, refspec: str, **kwargs: str) -> None:
        try:
            actual_fetch(root, remote, refspec, **kwargs)
        except GitError as exc:
            fetch_errors.append(str(exc))
            raise

    # Instrument the real fetch solely to retain its failure detail on the
    # unfixed production helper; no Git result or operation is simulated.
    with patch("wade.git.repo.fetch_ref", side_effect=observe_real_fetch):
        result = _recover_worktree(repo.local, repo.branch, config)
    assert result is not None, "\n".join(fetch_errors)
    assert result.succeeded
    assert result.path is not None
    assert _git(result.path, "rev-parse", "HEAD") == repo.remote_tip
    assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.remote_tip
    assert result.backup_ref is not None
    assert _git(repo.local, "rev-parse", result.backup_ref) == repo.old_tip
    assert (result.path / "new-pr.txt").read_text() == "replacement remote work\n"
    assert _protected(repo) == protected


def _config(repo: RecoveryRepository) -> ProjectConfig:
    return ProjectConfig(
        project=ProjectSettings(main_branch="main", worktrees_dir=str(repo.worktrees))
    )


def _target(repo: RecoveryRepository) -> Path:
    return repo.worktrees / "local" / repo.branch.replace("/", "-")


@pytest.mark.parametrize("mode", ["remote-only", "equal", "behind", "old-pr", "local-ahead"])
def test_remote_snapshot_wins_and_every_different_tip_is_preserved(
    recovery_repo: RecoveryRepository, mode: str
) -> None:
    repo = recovery_repo
    old: str | None = repo.old_tip
    if mode == "remote-only":
        _git(repo.local, "branch", "-D", repo.branch)
        old = None
    elif mode == "equal":
        _git(repo.local, "fetch", "origin", repo.branch)
        old = repo.remote_tip
        _git(repo.local, "update-ref", "refs/heads/" + repo.branch, old)
    elif mode == "behind":
        old = _git(repo.local, "rev-parse", "main")
        _git(repo.local, "update-ref", "refs/heads/" + repo.branch, old)
    elif mode == "old-pr":
        old = _git(repo.local, "rev-parse", repo.old_tip + "^")
        _git(repo.local, "update-ref", "refs/heads/" + repo.branch, old)
    else:
        remote = _git(repo.local, "rev-parse", repo.old_tip + "^")
        _git(repo.origin, "update-ref", "refs/heads/" + repo.branch, remote)
        repo = repo.model_copy(update={"remote_tip": remote})
    protected = _protected(repo)
    result = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert result.succeeded, result.detail
    assert result.failure is None
    assert result.source == "remote"
    assert result.path == _target(repo)
    assert _git(result.path, "rev-parse", "HEAD") == repo.remote_tip
    assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.remote_tip
    if old is not None and old != repo.remote_tip:
        assert result.backup_ref is not None
        assert _git(repo.local, "rev-parse", result.backup_ref) == old
    else:
        assert result.backup_ref is None
    assert _protected(repo) == protected
    # Completed recovery cannot be mistaken for a retained incomplete attempt.
    from wade.services.review_recovery import inspect_incomplete_recovery

    assert inspect_incomplete_recovery(repo.local, result.path, repo.branch) is None


@pytest.mark.parametrize("local_present", [True, False])
def test_live_remote_deletion_ignores_stale_tracking_and_uses_only_local_head(
    recovery_repo: RecoveryRepository, local_present: bool
) -> None:
    from wade.services.review_recovery import RecoveryFailure

    repo = recovery_repo
    _git(repo.local, "fetch", "origin", repo.branch)
    assert _git(repo.local, "rev-parse", "refs/remotes/origin/" + repo.branch) == repo.remote_tip
    _git(repo.publisher, "push", "origin", "--delete", repo.branch)
    if not local_present:
        _git(repo.local, "branch", "-D", repo.branch)
    protected = _protected(repo)
    result = _recover_worktree(repo.local, repo.branch, _config(repo))
    if local_present:
        assert result.succeeded, result.detail
        assert result.source == "local"
        assert result.path is not None
        assert _git(result.path, "rev-parse", "HEAD") == repo.old_tip
        assert _git(result.path, "symbolic-ref", "--short", "HEAD") == repo.branch
        assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.old_tip
        assert result.backup_ref is None
        assert "possibly stale" in result.detail
        assert "Fetch context" in result.detail
    else:
        assert not result.succeeded
        assert result.failure == RecoveryFailure.NO_BRANCH
        assert not _target(repo).exists()
    assert _protected(repo) == protected


@pytest.mark.parametrize("query_fails", [False, True])
def test_failed_fetch_never_falls_back_on_cached_or_local_refs(
    recovery_repo: RecoveryRepository, query_fails: bool
) -> None:
    from wade.services.review_recovery import RecoveryFailure

    repo = recovery_repo
    protected = _protected(repo)
    with patch("wade.git.repo.fetch_ref", side_effect=GitError("authentication denied")):
        if query_fails:
            with patch(
                "wade.git.branch.live_remote_head_exists", side_effect=GitError("network denied")
            ):
                result = _recover_worktree(repo.local, repo.branch, _config(repo))
        else:
            # The exact live remote query executes real Git against the bare origin.
            result = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert result.failure == (RecoveryFailure.QUERY if query_fails else RecoveryFailure.FETCH)
    assert "authentication denied" in result.detail
    if query_fails:
        assert "network denied" in result.detail
    assert not _target(repo).exists()
    assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.old_tip
    assert _protected(repo) == protected


def test_successful_fetch_with_disappearing_tracking_ref_refuses_local_fallback(
    recovery_repo: RecoveryRepository,
) -> None:
    from wade.services.review_recovery import RecoveryFailure

    repo = recovery_repo
    actual_fetch = git_repo.fetch_ref

    def remove_after_real_fetch(root: Path, remote: str, refspec: str, **kwargs: str) -> None:
        actual_fetch(root, remote, refspec, **kwargs)
        _git(repo.local, "update-ref", "-d", "refs/remotes/origin/" + repo.branch)

    with patch("wade.git.repo.fetch_ref", side_effect=remove_after_real_fetch):
        result = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert result.failure == RecoveryFailure.FETCHED_REF
    assert not _target(repo).exists()
    assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.old_tip


@pytest.mark.parametrize("occupied", ["file", "directory", "empty-directory", "dangling-symlink"])
def test_occupied_destination_never_changes_refs_or_files(
    recovery_repo: RecoveryRepository, occupied: str
) -> None:
    from wade.services.review_recovery import RecoveryFailure

    repo = recovery_repo
    target = _target(repo)
    target.parent.mkdir(parents=True)
    if occupied == "file":
        target.write_bytes(b"precious target")
    elif occupied in ("directory", "empty-directory"):
        target.mkdir()
        if occupied == "directory":
            (target / "precious.txt").write_bytes(b"precious target")
    else:
        target.symlink_to(repo.local.parent / "does-not-exist", target_is_directory=True)
    protected = _protected(repo)
    result = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert result.failure == RecoveryFailure.DESTINATION
    assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.old_tip
    if occupied == "file":
        assert target.read_bytes() == b"precious target"
    elif occupied == "directory":
        assert (target / "precious.txt").read_bytes() == b"precious target"
    elif occupied == "dangling-symlink":
        assert target.is_symlink()
    else:
        assert list(target.iterdir()) == []
    assert _protected(repo) == protected


@pytest.mark.parametrize("owner", ["main", "sibling"])
def test_checked_out_branch_is_never_moved_or_forced_out(
    recovery_repo: RecoveryRepository, owner: str
) -> None:
    from wade.services.review_recovery import RecoveryFailure

    repo = recovery_repo
    branch = "main" if owner == "main" else "topic"
    protected = _protected(repo)
    result = _recover_worktree(repo.local, branch, _config(repo))
    assert result.failure == RecoveryFailure.OWNERSHIP
    assert str(repo.local if owner == "main" else repo.sibling) in result.detail
    assert _protected(repo) == protected


@pytest.mark.parametrize("existing", ["correct", "wrong", "symbolic"])
def test_existing_backup_is_verified_never_overwritten(
    recovery_repo: RecoveryRepository, existing: str
) -> None:
    from wade.services.review_recovery import RecoveryFailure

    repo = recovery_repo
    backup = f"refs/wade/recovery/{repo.branch}/{repo.old_tip}"
    base = _git(repo.local, "rev-parse", "main")
    if existing == "symbolic":
        _git(repo.local, "symbolic-ref", backup, "refs/heads/main")
    else:
        _git(repo.local, "update-ref", backup, repo.old_tip if existing == "correct" else base)
    original = _git(repo.local, "rev-parse", backup)
    result = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert _git(repo.local, "rev-parse", backup) == original
    if existing == "correct":
        assert result.succeeded, result.detail
        assert result.backup_ref == backup
    else:
        assert result.failure == RecoveryFailure.PRESERVATION
        assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.old_tip
        assert result.marker is not None and result.marker.exists()


def test_symbolic_local_head_cannot_move_main_through_alignment(
    recovery_repo: RecoveryRepository,
) -> None:
    repo = recovery_repo
    _git(repo.local, "update-ref", "-d", "refs/heads/" + repo.branch)
    _git(repo.local, "symbolic-ref", "refs/heads/" + repo.branch, "refs/heads/main")
    protected = _protected(repo)
    result = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert not result.succeeded
    assert "direct ref" in result.detail
    assert _protected(repo) == protected
    assert _git(repo.local, "symbolic-ref", "refs/heads/" + repo.branch) == "refs/heads/main"


def _concurrent_tip(repo: RecoveryRepository, parent: str | None = None) -> str:
    old = parent or repo.old_tip
    tree = _git(repo.local, "rev-parse", old + "^{tree}")
    return _git(repo.local, "commit-tree", tree, "-p", old, "-m", "concurrent unpublished tip")


def _ref_absent(repo: Path, ref: str) -> bool:
    return _git(repo, "for-each-ref", "--format=%(refname)", ref) == ""


@pytest.mark.parametrize(
    "race", ["changed-tip", "locked-then-changed", "equal-tip", "backup-changed"]
)
def test_atomic_alignment_never_displaces_an_unpreserved_racing_tip(
    recovery_repo: RecoveryRepository, race: str
) -> None:
    from wade.git import branch as git_branch
    from wade.services.review_recovery import RecoveryFailure

    repo = recovery_repo
    if race == "equal-tip":
        _git(repo.local, "fetch", "origin", repo.branch)
        _git(repo.local, "update-ref", "refs/heads/" + repo.branch, repo.remote_tip)
        repo = repo.model_copy(update={"old_tip": repo.remote_tip})
    concurrent = _concurrent_tip(repo)
    backup = f"refs/wade/recovery/{repo.branch}/{repo.old_tip}"
    if race == "backup-changed":
        _git(repo.local, "update-ref", backup, repo.old_tip)
    protected = _protected(repo)
    actual = git_branch.preserve_and_align_branch

    def interleave(root: Path, branch: str, old: str, selected: str) -> str | None:
        if race == "locked-then-changed":
            lock = repo.local / ".git/refs/heads" / (repo.branch + ".lock")
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text("controlled contention")
            try:
                return actual(root, branch, old, selected)
            finally:
                lock.unlink()
                _git(repo.local, "update-ref", "refs/heads/" + branch, concurrent)
        if race == "backup-changed":
            _git(repo.local, "update-ref", backup, concurrent)
        else:
            _git(repo.local, "update-ref", "refs/heads/" + branch, concurrent)
        return actual(root, branch, old, selected)

    with patch("wade.git.branch.preserve_and_align_branch", side_effect=interleave):
        result = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert result.failure == RecoveryFailure.PRESERVATION
    assert result.marker is not None and result.marker.exists()
    if race == "backup-changed":
        assert _git(repo.local, "rev-parse", backup) == concurrent
        assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.old_tip
    else:
        assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == concurrent
        assert _ref_absent(repo.local, backup)  # Failed transaction created no partial backup.
    assert _protected(repo) == protected
    retry = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert retry.failure == RecoveryFailure.INCOMPLETE
    assert _protected(repo) == protected
    if race != "backup-changed":
        assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == concurrent


def test_local_branch_created_during_new_branch_reservation_is_not_overwritten(
    recovery_repo: RecoveryRepository,
) -> None:
    from wade.git import worktree as git_worktree
    from wade.services.review_recovery import RecoveryFailure

    repo = recovery_repo
    concurrent = _concurrent_tip(repo)
    _git(repo.local, "branch", "-D", repo.branch)
    actual = git_worktree._run_git

    def interleave(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("worktree", "add") and "-b" in args:
            _git(repo.local, "update-ref", "refs/heads/" + repo.branch, concurrent)
        return actual(*args, **kwargs)

    with patch("wade.git.worktree._run_git", side_effect=interleave):
        result = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert result.failure == RecoveryFailure.CREATION
    assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == concurrent
    assert result.marker is not None and result.marker.exists()
    assert (
        _recover_worktree(repo.local, repo.branch, _config(repo)).failure
        == RecoveryFailure.INCOMPLETE
    )


def _assert_start_refuses_incomplete(repo: RecoveryRepository) -> None:
    from unittest.mock import MagicMock

    from wade.models.task import Task
    from wade.services import review_service

    provider = MagicMock()
    provider.read_task.return_value = Task(id="42", title="force-pushed")
    with (
        patch("wade.services.review_service.load_config", return_value=_config(repo)),
        patch("wade.services.review_service.get_provider", return_value=provider),
        patch("wade.git.pr.get_pr_for_branch") as lookup,
        patch("wade.services.review_service.get_comprehensive_review_status") as reviews,
        patch("wade.services.review_service.bootstrap_worktree") as bootstrap,
        patch("wade.services.review_service.resolve_ai_tool") as launch,
    ):
        assert not review_service.start("42", project_root=repo.local)
    lookup.assert_not_called()
    reviews.assert_not_called()
    bootstrap.assert_not_called()
    launch.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        "reservation",
        "alignment",
        "untracked-collision",
        "ignored-collision",
        "population",
        "interrupt",
    ],
)
def test_partial_recovery_cannot_become_quiet_success_on_next_start(
    recovery_repo: RecoveryRepository, failure: str
) -> None:
    import json

    from wade.git import worktree as git_worktree
    from wade.models.review_recovery import RecoveryRecord

    repo = recovery_repo
    target = _target(repo)
    protected = _protected(repo)
    if failure == "ignored-collision":
        exclude = repo.local / ".git/info/exclude"
        exclude.write_text(exclude.read_text() + "\nnew-pr.txt\n")
    actual = git_worktree._run_git
    injected = False

    def fail_after_real_step(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal injected
        if args[0] == "read-tree" and failure.endswith("collision"):
            (target / "new-pr.txt").write_bytes(b"precious collision\x00bytes")
            if failure == "ignored-collision":
                assert _git(target, "check-ignore", "new-pr.txt") == "new-pr.txt"
        result = actual(*args, **kwargs)
        should_fail = (
            (failure == "reservation" and args[:2] == ("worktree", "add"))
            or (failure == "alignment" and args[0] == "read-tree")
            or (failure in ("population", "interrupt") and args[0] == "checkout-index")
        )
        if should_fail and not injected:
            injected = True
            if failure == "interrupt":
                raise KeyboardInterrupt("controlled interruption after population")
            raise GitError("controlled failure after " + failure)
        return result

    with patch("wade.git.worktree._run_git", side_effect=fail_after_real_step):
        if failure == "interrupt":
            with pytest.raises(KeyboardInterrupt):
                _recover_worktree(repo.local, repo.branch, _config(repo))
            markers = list((repo.local / ".git/.wade/review-recovery").glob("*.json"))
            assert len(markers) == 1
            marker = markers[0]
        else:
            result = _recover_worktree(repo.local, repo.branch, _config(repo))
            assert not result.succeeded
            assert result.marker is not None
            marker = result.marker
    record = RecoveryRecord.model_validate_json(marker.read_bytes())
    assert record.target == target
    assert record.selected_oid == repo.remote_tip
    assert target.is_dir() and (target / ".git").is_file()
    if failure == "reservation":
        assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.old_tip
        assert record.backup_ref is None
    else:
        assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.remote_tip
        assert record.backup_ref is not None
        assert _git(repo.local, "rev-parse", record.backup_ref) == repo.old_tip
    if failure.endswith("collision"):
        assert (target / "new-pr.txt").read_bytes() == b"precious collision\x00bytes"
    before = marker.read_bytes()
    _assert_start_refuses_incomplete(repo)
    assert marker.read_bytes() == before
    assert _protected(repo) == protected
    assert json.loads(before)["phase"] == record.phase.value


@pytest.mark.parametrize("change", ["delete", "replace", "delete-matching-path"])
def test_local_fallback_attachment_never_guesses_or_restores_matching_paths(
    recovery_repo: RecoveryRepository, change: str
) -> None:
    from wade.git import worktree as git_worktree

    repo = recovery_repo
    if change == "delete-matching-path":
        # Use a branch name which is also a tracked path in the selected tree.
        _git(repo.local, "branch", "-m", repo.branch, "file.txt")
        repo = repo.model_copy(update={"branch": "file.txt"})
    else:
        _git(repo.publisher, "push", "origin", "--delete", repo.branch)
    # B is an existing object distinct from local A; the live remote branch is absent.
    _git(repo.local, "fetch", str(repo.publisher), repo.remote_tip)
    _git(repo.local, "update-ref", "refs/remotes/origin/" + repo.branch, repo.remote_tip)
    target = _target(repo)
    concurrent = _concurrent_tip(repo)
    actual = git_worktree._run_git
    injected = False

    def change_at_attachment(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal injected
        if args[0] == "checkout" and not injected:
            injected = True
            if change == "replace":
                _git(repo.local, "update-ref", "refs/heads/" + repo.branch, concurrent)
            else:
                (target / "file.txt").write_bytes(b"precious matching-path edit\x00bytes")
                _git(repo.local, "update-ref", "-d", "refs/heads/" + repo.branch)
        return actual(*args, **kwargs)

    protected = _protected(repo)
    with patch("wade.git.worktree._run_git", side_effect=change_at_attachment):
        result = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert not result.succeeded
    assert result.marker is not None and result.marker.exists()
    if change == "replace":
        assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == concurrent
    else:
        assert _ref_absent(repo.local, "refs/heads/" + repo.branch)
        assert (target / "file.txt").read_bytes() == b"precious matching-path edit\x00bytes"
    assert _protected(repo) == protected


@pytest.mark.parametrize("failure", ["initial-write", "phase-write", "clear"])
def test_state_io_failure_never_claims_recovery_complete(
    recovery_repo: RecoveryRepository, failure: str
) -> None:
    from wade.services import review_recovery

    repo = recovery_repo
    protected = _protected(repo)
    actual_write = review_recovery.atomic_write_state_file
    count = 0

    def fail_write(*args: object, **kwargs: object) -> bool:
        nonlocal count
        count += 1
        if failure == "initial-write" or (failure == "phase-write" and count >= 3):
            return False
        return actual_write(*args, **kwargs)

    with (
        patch("wade.services.review_recovery.atomic_write_state_file", side_effect=fail_write),
        patch("wade.services.review_recovery.delete_state_file", return_value=False)
        if failure == "clear"
        else patch(
            "wade.services.review_recovery.delete_state_file",
            wraps=review_recovery.delete_state_file,
        ),
    ):
        outcome = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert not outcome.succeeded
    assert outcome.failure == review_recovery.RecoveryFailure.STATE
    assert _protected(repo) == protected
    assert outcome.marker is not None
    if failure == "initial-write":
        assert not _target(repo).exists()
        assert not outcome.marker.exists()
        assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.old_tip
    else:
        assert outcome.marker.is_file()
        before = outcome.marker.read_bytes()
        _assert_start_refuses_incomplete(repo)
        assert outcome.marker.read_bytes() == before
        if failure == "phase-write":
            assert "cannot persist failure detail" in outcome.detail
            assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.old_tip
        else:
            assert _git(_target(repo), "rev-parse", "HEAD") == repo.remote_tip


@pytest.mark.parametrize(
    "unsafe", ["corrupt", "wrong-target", "wrong-branch", "symlink", "directory"]
)
def test_unsafe_marker_blocks_existing_worktree_before_review_lookup(
    recovery_repo: RecoveryRepository, unsafe: str
) -> None:
    from wade.models.review_recovery import RecoveryRecord
    from wade.services.review_recovery import _state_location

    repo = recovery_repo
    target = _target(repo)
    _git(repo.local, "worktree", "add", str(target), repo.branch)
    common, filename = _state_location(repo.local, target)
    marker = common / ".wade/review-recovery" / filename
    marker.parent.mkdir(parents=True)
    record = RecoveryRecord(branch=repo.branch, target=target, selected_oid=repo.old_tip)
    if unsafe == "wrong-target":
        record.target = target.parent / "other"
    elif unsafe == "wrong-branch":
        record.branch = "feat/other"
    if unsafe == "directory":
        marker.mkdir()
    elif unsafe == "symlink":
        precious = common / "precious-recovery.json"
        precious.write_text(record.model_dump_json())
        marker.symlink_to(precious)
    else:
        marker.write_text("not json" if unsafe == "corrupt" else record.model_dump_json())
    protected = _protected(repo)
    _assert_start_refuses_incomplete(repo)
    assert _protected(repo) == protected
    assert marker.is_symlink() if unsafe == "symlink" else marker.exists()


def test_tracking_ref_refresh_after_capture_does_not_change_selected_snapshot(
    recovery_repo: RecoveryRepository,
) -> None:
    from wade.git import worktree as git_worktree

    repo = recovery_repo
    actual = git_worktree.recover_branch_worktree

    def refresh_tracking(*args: object, **kwargs: object) -> object:
        _git(repo.local, "update-ref", "refs/remotes/origin/" + repo.branch, repo.old_tip)
        return actual(*args, **kwargs)

    with patch("wade.git.worktree.recover_branch_worktree", side_effect=refresh_tracking):
        outcome = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert outcome.succeeded and outcome.path is not None
    assert outcome.selected_oid == repo.remote_tip
    assert _git(outcome.path, "rev-parse", "HEAD") == repo.remote_tip
    assert _git(repo.local, "rev-parse", "refs/remotes/origin/" + repo.branch) == repo.old_tip


def test_symbolic_tracking_destination_cannot_move_checked_out_main(
    recovery_repo: RecoveryRepository,
) -> None:
    repo = recovery_repo
    tracking = "refs/remotes/origin/" + repo.branch
    _git(repo.local, "update-ref", "-d", tracking)
    _git(repo.local, "symbolic-ref", tracking, "refs/heads/main")
    protected = _protected(repo)
    result = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert not result.succeeded
    assert _protected(repo) == protected
    assert _git(repo.local, "symbolic-ref", tracking) == "refs/heads/main"
    assert not _target(repo).exists()


@pytest.mark.parametrize("local_fallback", [False, True])
def test_same_named_tag_does_not_reject_attached_recovery(
    recovery_repo: RecoveryRepository, local_fallback: bool
) -> None:
    repo = recovery_repo
    _git(repo.local, "tag", repo.branch, repo.old_tip)
    if local_fallback:
        _git(repo.publisher, "push", "origin", "--delete", repo.branch)
    protected = _protected(repo)
    result = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert result.succeeded, result.detail
    assert result.path is not None
    assert _git(result.path, "symbolic-ref", "HEAD") == "refs/heads/" + repo.branch
    assert _git(result.path, "rev-parse", "HEAD") == (
        repo.old_tip if local_fallback else repo.remote_tip
    )
    assert _git(repo.local, "rev-parse", "refs/tags/" + repo.branch) == repo.old_tip
    assert _protected(repo) == protected


@pytest.mark.parametrize(
    "race", ["symbolic", "symbolic-equal-oid", "changed", "locked", "missing-pin"]
)
def test_isolated_fetch_cannot_follow_tracking_ref_races_or_fall_back(
    recovery_repo: RecoveryRepository, race: str
) -> None:
    from wade.services.review_recovery import RecoveryFailure

    repo = recovery_repo
    tracking = "refs/remotes/origin/" + repo.branch
    if race == "symbolic-equal-oid":
        _git(repo.local, "update-ref", tracking, "refs/heads/main")
    protected = _protected(repo)
    actual_fetch = git_repo.fetch_ref
    lock = repo.local / ".git" / (tracking + ".lock")
    isolated_ref = ""

    def race_fetch(root: Path, remote: str, refspec: str, **kwargs: str) -> None:
        nonlocal isolated_ref
        source, isolated_ref = refspec.split(":", 1)
        assert source == "refs/heads/" + repo.branch
        assert isolated_ref.startswith("refs/wade/review-fetch/")
        assert isolated_ref != tracking
        if race.startswith("symbolic"):
            _git(repo.local, "symbolic-ref", tracking, "refs/heads/main")
        actual_fetch(root, remote, refspec, **kwargs)
        if race == "changed":
            _git(repo.local, "update-ref", tracking, repo.old_tip)
        elif race == "locked":
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text("owned test lock")
        elif race == "missing-pin":
            _git(repo.local, "update-ref", "-d", isolated_ref)
            _git(repo.publisher, "push", "origin", "--delete", repo.branch)

    try:
        with patch("wade.git.repo.fetch_ref", side_effect=race_fetch):
            result = _recover_worktree(repo.local, repo.branch, _config(repo))
    finally:
        if race == "locked":
            lock.unlink()
    assert _protected(repo) == protected
    if result.succeeded:
        # Some Git versions may safely replace the symbolic tracking ref itself;
        # --no-deref must never move its checked-out main target.
        assert race == "symbolic-equal-oid"
        assert result.path is not None
        assert _git(result.path, "rev-parse", "HEAD") == repo.remote_tip
    else:
        assert result.failure == RecoveryFailure.FETCHED_REF
        assert not _target(repo).exists()
        assert _git(repo.local, "rev-parse", "refs/heads/" + repo.branch) == repo.old_tip
    if race.startswith("symbolic") and not result.succeeded:
        assert _git(repo.local, "symbolic-ref", tracking) == "refs/heads/main"
    elif race == "changed":
        assert _git(repo.local, "rev-parse", tracking) == repo.old_tip
    if not result.succeeded and race != "missing-pin":
        assert _git(repo.local, "rev-parse", isolated_ref) == repo.remote_tip


def test_isolated_fetch_ignores_configured_local_head_mapping_and_clears_owned_pin(
    recovery_repo: RecoveryRepository,
) -> None:
    repo = recovery_repo
    _git(repo.local, "config", "--unset-all", "remote.origin.fetch")
    _git(repo.local, "config", "--add", "remote.origin.fetch", "+refs/heads/*:refs/heads/*")
    protected = _protected(repo)
    result = _recover_worktree(repo.local, repo.branch, _config(repo))
    assert result.succeeded, result.detail
    assert result.path is not None
    assert _git(result.path, "rev-parse", "HEAD") == repo.remote_tip
    assert _git(repo.local, "rev-parse", "refs/remotes/origin/" + repo.branch) == repo.remote_tip
    assert _git(repo.local, "for-each-ref", "refs/wade/review-fetch/") == ""
    assert _protected(repo) == protected
