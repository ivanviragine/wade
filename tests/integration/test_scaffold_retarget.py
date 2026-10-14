"""Scaffold retarget safety with real Git indexes, trees, and linked worktrees."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from wade.git import branch
from wade.git.pr import PRLookup, PRRef
from wade.models.config import ProjectConfig
from wade.services.implementation_service.draft_pr import (
    bootstrap_draft_pr,
    reroot_scaffold_branch_for_retarget,
)

_D = "wade.services.implementation_service.draft_pr"
_BRANCH = "feat/488-preserve"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _snapshot(root: Path) -> dict[str, tuple[str, bytes | str]]:
    result: dict[str, tuple[str, bytes | str]] = {}
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[name] = ("symlink", os.readlink(path))
        elif path.is_dir():
            result[name] = ("directory", "")
        else:
            result[name] = ("file", path.read_bytes())
    return result


def _setup(repo: Path, parent: Path, target: str, *, mixed: bool = False) -> Path:
    (repo / ".gitignore").write_text("ignored*\ncache/\n.wade/\n")
    if mixed:
        (repo / "mixed").mkdir()
        (repo / "mixed/tracked").write_text("tracked baseline\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline ignores")
    _git(repo, "checkout", "-b", "develop")
    if mixed:
        _git(repo, "rm", "mixed/tracked")
    destination = repo / target
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("destination content\n")
    _git(repo, "add", "-f", target)
    _git(repo, "commit", "-m", "destination tree")
    _git(repo, "checkout", "main")
    branch.create_branch(repo, _BRANCH, "main")
    branch.create_scaffold_commit(repo, _BRANCH, "chore: scaffold branch for #488")
    worktree = parent / "worktree"
    _git(repo, "worktree", "add", str(worktree), _BRANCH)
    return worktree


@pytest.mark.parametrize(
    ("target", "local", "kind"),
    [
        ("precious", "precious", "file"),
        ("ignored-data", "ignored-data", "file"),
        ("cache", "cache/nested/precious", "file"),
        ("local/child", "local", "file"),
        ("mixed", "mixed/ignored-data", "mixed"),
        ("local/child", "local", "symlink"),
        ("precious", "precious", "dangling"),
        ("local", "local", "empty-directory"),
        ("local", "local", "repository"),
        ("space ' quote\tline\nfile", "space ' quote\tline\nfile", "file"),
    ],
)
def test_collision_refuses_before_any_mutation(
    tmp_git_repo: Path, tmp_path: Path, target: str, local: str, kind: str
) -> None:
    worktree = _setup(tmp_git_repo, tmp_path, target, mixed=kind == "mixed")
    path = worktree / local
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind in {"symlink", "dangling"}:
        outside = tmp_path / "outside"
        if kind == "symlink":
            outside.mkdir()
            (outside / "precious").write_bytes(b"outside user work\x00\n")
        path.symlink_to(outside, target_is_directory=True)
    elif kind == "empty-directory":
        path.mkdir()
    elif kind == "repository":
        path.mkdir()
        _git(path, "init")
        (path / "precious").write_bytes(b"nested user work\x00\n")
    else:
        path.write_bytes(b"precious user work\x00\r\n")
    before = _snapshot(worktree)
    head = _git(worktree, "rev-parse", "HEAD")
    index = _git(worktree, "write-tree")
    with patch(f"{_D}.git_repo.push_branch") as push:
        assert not reroot_scaffold_branch_for_retarget(
            tmp_git_repo, _BRANCH, "main", "develop", "488"
        )
    assert _snapshot(worktree) == before
    assert _git(worktree, "rev-parse", "HEAD") == head
    assert _git(worktree, "write-tree") == index
    push.assert_not_called()


def test_noncolliding_generated_and_user_files_allow_retarget(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    worktree = _setup(tmp_git_repo, tmp_path, "shared/from-base")
    names = ["shared/user", "shared-prefix", "ignored-data", ".wade/session/generated"]
    for name in names:
        path = worktree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"preserve me\x00\n")
    with patch(f"{_D}.git_repo.push_branch") as push:
        assert reroot_scaffold_branch_for_retarget(tmp_git_repo, _BRANCH, "main", "develop", "488")
    assert _git(worktree, "rev-parse", "HEAD^") == _git(tmp_git_repo, "rev-parse", "develop")
    assert branch.tip_commit_is_empty(worktree, "HEAD")
    assert all((worktree / name).read_bytes() == b"preserve me\x00\n" for name in names)
    push.assert_called_once()


@pytest.mark.parametrize("staged", [False, True])
def test_tracked_changes_still_refuse(tmp_git_repo: Path, tmp_path: Path, staged: bool) -> None:
    worktree = _setup(tmp_git_repo, tmp_path, "new-base-file")
    (worktree / "README.md").write_text("valuable tracked edit\n")
    if staged:
        _git(worktree, "add", "README.md")
    before = _snapshot(worktree)
    head = _git(worktree, "rev-parse", "HEAD")
    with patch(f"{_D}.git_repo.push_branch") as push:
        assert not reroot_scaffold_branch_for_retarget(
            tmp_git_repo, _BRANCH, "main", "develop", "488"
        )
    assert _snapshot(worktree) == before
    assert _git(worktree, "rev-parse", "HEAD") == head
    push.assert_not_called()


def test_bootstrap_refusal_preserves_pr_base_and_existing_pin(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    worktree = _setup(tmp_git_repo, tmp_path, "precious")
    (worktree / "precious").write_bytes(b"user bytes\n")
    (worktree / ".wade").mkdir()
    pin = worktree / ".wade/base_branch"
    pin.write_text("main\n")
    head = _git(worktree, "rev-parse", "HEAD")
    lookup = PRLookup(
        found=True,
        pr=PRRef(number=526, url="https://example/526", state="OPEN", baseRefName="main"),
    )
    with (
        patch(f"{_D}.git_branch.make_branch_name", return_value=_BRANCH),
        patch(f"{_D}.git_pr.get_pr_for_branch", return_value=lookup),
        patch(f"{_D}.git_pr.update_pr_base") as edit,
        patch(f"{_D}.git_repo.push_branch") as push,
    ):
        assert (
            bootstrap_draft_pr(
                "488", "preserve", "plan", ProjectConfig(), tmp_git_repo, base_branch="develop"
            )
            is None
        )
    assert (worktree / "precious").read_bytes() == b"user bytes\n"
    assert pin.read_text() == "main\n"
    assert _git(worktree, "rev-parse", "HEAD") == head
    edit.assert_not_called()
    push.assert_not_called()


def test_case_alias_collision_is_preserved(tmp_git_repo: Path, tmp_path: Path) -> None:
    worktree = _setup(tmp_git_repo, tmp_path, "precious")
    local = worktree / "PRECIOUS"
    local.write_bytes(b"case alias user data\n")
    if not (worktree / "precious").exists():
        pytest.skip("filesystem uses case-sensitive names")
    head = _git(worktree, "rev-parse", "HEAD")
    with patch(f"{_D}.git_repo.push_branch") as push:
        assert not reroot_scaffold_branch_for_retarget(
            tmp_git_repo, _BRANCH, "main", "develop", "488"
        )
    assert local.read_bytes() == b"case alias user data\n"
    assert _git(worktree, "rev-parse", "HEAD") == head
    push.assert_not_called()


def test_hardlink_to_tracked_file_is_still_untracked_data(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    worktree = _setup(tmp_git_repo, tmp_path, "precious")
    os.link(worktree / "README.md", worktree / "precious")
    before = _snapshot(worktree)
    with patch(f"{_D}.git_repo.push_branch") as push:
        assert not reroot_scaffold_branch_for_retarget(
            tmp_git_repo, _BRANCH, "main", "develop", "488"
        )
    assert _snapshot(worktree) == before
    push.assert_not_called()


@pytest.mark.parametrize("failure", ["inventory", "filesystem"])
def test_inspection_failure_refuses_without_mutation(
    tmp_git_repo: Path, tmp_path: Path, failure: str
) -> None:
    from wade.git.repo import GitError

    worktree = _setup(tmp_git_repo, tmp_path, "new-file")
    head = _git(worktree, "rev-parse", "HEAD")
    operation = (
        "wade.git.branch._reset_path_inventory"
        if failure == "inventory"
        else "wade.git.branch.os.scandir"
    )
    error = (
        GitError("cannot inspect tree")
        if failure == "inventory"
        else PermissionError("cannot inspect directory")
    )
    with patch(operation, side_effect=error), patch(f"{_D}.git_repo.push_branch") as push:
        assert not reroot_scaffold_branch_for_retarget(
            tmp_git_repo, _BRANCH, "main", "develop", "488"
        )
    assert _git(worktree, "rev-parse", "HEAD") == head
    assert not (worktree / "new-file").exists()
    push.assert_not_called()


@pytest.mark.parametrize("collision", [False, True])
def test_rollback_guards_its_own_destination(
    tmp_git_repo: Path, tmp_path: Path, collision: bool
) -> None:
    from wade.services.implementation_service.draft_pr import _restore_scaffold_head

    worktree = _setup(tmp_git_repo, tmp_path, "new-file")
    old = _git(worktree, "rev-parse", "HEAD")
    _git(tmp_git_repo, "checkout", "develop")
    _git(tmp_git_repo, "rm", "README.md")
    _git(tmp_git_repo, "commit", "-m", "remove old-base path")
    _git(tmp_git_repo, "checkout", "main")
    with patch(f"{_D}.git_repo.push_branch"):
        assert reroot_scaffold_branch_for_retarget(tmp_git_repo, _BRANCH, "main", "develop", "488")
    current = _git(worktree, "rev-parse", "HEAD")
    (worktree / ".wade").mkdir()
    generated = worktree / ".wade/generated"
    generated.write_bytes(b"keep generated data\n")
    if collision:
        (worktree / "README.md").write_bytes(b"new user data before rollback\n")
    with patch(f"{_D}.git_repo.push_branch") as push, patch(f"{_D}.console") as console:
        _restore_scaffold_head(tmp_git_repo, _BRANCH, old, 526)
    assert generated.read_bytes() == b"keep generated data\n"
    if collision:
        assert _git(worktree, "rev-parse", "HEAD") == current
        assert (worktree / "README.md").read_bytes() == b"new user data before rollback\n"
        push.assert_not_called()
        assert "remote branch may be rerooted" in console.error.call_args.args[0]
    else:
        assert _git(worktree, "rev-parse", "HEAD") == old
        assert (worktree / "README.md").read_text() == "# Test Repo\n"
        push.assert_called_once()
