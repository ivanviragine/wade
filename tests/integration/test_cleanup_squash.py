"""Real history and removal contracts for exact merged-PR cleanup (#492)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wade.models.config import ProjectConfig, ProjectSettings
from wade.services.implementation_service import cleanup


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def squash_repo(
    tmp_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, dict[str, object]]:
    root = tmp_git_repo.resolve()
    bare = tmp_path / "origin.git"
    git(root, "init", "--bare", str(bare))
    origin = "https://github.com/example/cleanup.git"
    git(root, "remote", "add", "origin", origin)
    git(root, "config", f"url.{bare}.insteadOf", origin)
    git(root, "push", "-u", "origin", "main")
    wt = (tmp_path / "feature").resolve()
    branch = "feat/42-squash"
    git(root, "worktree", "add", "-b", branch, str(wt))
    for text in ["one\n", "one\ntwo\n"]:
        (wt / "feature.txt").write_text(text)
        git(wt, "add", "feature.txt")
        git(wt, "commit", "-m", "feat: extend feature")
    head = git(wt, "rev-parse", "HEAD")
    git(root, "push", "-u", "origin", branch)
    git(root, "merge", "--squash", branch)
    git(root, "commit", "-m", "feat: squash feature")
    merge = git(root, "rev-parse", "HEAD")
    git(root, "push", "origin", "main")
    git(root, "push", "origin", "--delete", branch)
    record: dict[str, object] = {
        "number": 42,
        "state": "closed",
        "merged_at": "2026-10-14T12:00:00Z",
        "merge_commit_sha": merge,
        "head": {
            "ref": branch,
            "sha": head,
            "repo": {
                "full_name": "example/cleanup",
                "html_url": "https://github.com/example/cleanup",
            },
        },
        "base": {
            "ref": "main",
            "sha": merge,
            "repo": {
                "full_name": "example/cleanup",
                "html_url": "https://github.com/example/cleanup",
            },
        },
    }
    monkeypatch.setattr(
        cleanup, "load_config", lambda _: ProjectConfig(project=ProjectSettings(main_branch="main"))
    )
    monkeypatch.setattr(cleanup, "get_provider", lambda _: MagicMock())
    monkeypatch.setattr(
        "wade.git.pr._run_gh",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps([record]), ""),
    )
    return root, wt, record


def test_public_remove_accepts_multi_commit_squash(
    squash_repo: tuple[Path, Path, dict[str, object]],
) -> None:
    root, wt, _ = squash_repo
    # Equal final trees are insufficient: each original patch is unmatched.
    assert git(root, "rev-parse", "main^{tree}") == git(wt, "rev-parse", "HEAD^{tree}")
    assert len(git(root, "cherry", "main", "feat/42-squash").splitlines()) == 2
    assert all(
        line.startswith("+") for line in git(root, "cherry", "main", "feat/42-squash").splitlines()
    )
    assert cleanup.remove(target="42", force=True, project_root=root)
    assert not wt.exists()
    # Squash evidence permits worktree removal, never a force branch deletion.
    assert git(root, "rev-parse", "refs/heads/feat/42-squash")


@pytest.mark.parametrize("advance", ["unrelated", "same-file", "revert"])
def test_fresh_base_with_deleted_source_and_behind_local_main(
    squash_repo: tuple[Path, Path, dict[str, object]], advance: str
) -> None:
    root, wt, record = squash_repo
    merge = str(record["merge_commit_sha"])
    if advance == "unrelated":
        (root / "later.txt").write_text("later\n")
        git(root, "add", "later.txt")
    else:
        (root / "feature.txt").write_text("later edit\n")
        git(root, "add", "feature.txt")
    git(root, "commit", "-m", "feat: advance main")
    if advance == "revert":
        git(root, "revert", "--no-edit", "HEAD")
    git(root, "push", "origin", "main")
    git(root, "reset", "--hard", f"{merge}^")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    # A narrow default mapping must not block fetching the selected base.
    git(root, "config", "remote.origin.fetch", "+refs/heads/other:refs/remotes/origin/other")
    assert cleanup.remove(target="42", force=True, project_root=root)
    assert not wt.exists()


@pytest.mark.parametrize(
    "change", ["extra", "empty", "net-zero", "staged", "unstaged", "untracked"]
)
def test_post_merge_work_survives(
    squash_repo: tuple[Path, Path, dict[str, object]], change: str
) -> None:
    root, wt, _ = squash_repo
    original = (wt / "feature.txt").read_bytes()
    if change in {"extra", "net-zero", "staged", "unstaged"}:
        (wt / "feature.txt").write_text("private work\n")
    if change in {"extra", "net-zero", "staged"}:
        git(wt, "add", "feature.txt")
    if change in {"extra", "net-zero"}:
        git(wt, "commit", "-m", "feat: local only")
    if change == "net-zero":
        (wt / "feature.txt").write_bytes(original)
        git(wt, "add", "feature.txt")
        git(wt, "commit", "-m", "fix: restore tree")
    if change == "empty":
        git(wt, "commit", "--allow-empty", "-m", "chore: local empty commit")
    if change == "untracked":
        (wt / "private.txt").write_text("never pushed\n")
    head = git(wt, "rev-parse", "HEAD")
    files = {p.name: p.read_bytes() for p in wt.iterdir() if p.is_file() and p.name != ".git"}
    assert not cleanup.remove(target="42", force=True, project_root=root)
    assert git(wt, "rev-parse", "HEAD") == head
    assert {
        p.name: p.read_bytes() for p in wt.iterdir() if p.is_file() and p.name != ".git"
    } == files


@pytest.mark.parametrize(
    "mismatch",
    [
        "head",
        "head-repo",
        "head-host",
        "head-ref",
        "base-ref",
        "base-repo",
        "base-host",
        "merge",
        "not-merged",
    ],
)
def test_unmatched_pr_preserves_worktree(
    squash_repo: tuple[Path, Path, dict[str, object]], mismatch: str
) -> None:
    root, wt, record = squash_repo
    head, base = record["head"], record["base"]
    assert isinstance(head, dict) and isinstance(base, dict)
    if mismatch == "head":
        head["sha"] = "1" * 40
    elif mismatch.endswith("-repo"):
        ref = head if mismatch.startswith("head") else base
        ref["repo"] = {
            "full_name": "foreign/cleanup",
            "html_url": "https://github.com/foreign/cleanup",
        }
    elif mismatch.endswith("-host"):
        ref = head if mismatch.startswith("head") else base
        ref["repo"] = {
            "full_name": "example/cleanup",
            "html_url": "https://wrong.example/example/cleanup",
        }
    elif mismatch.endswith("-ref"):
        (head if mismatch.startswith("head") else base)["ref"] = "different"
    elif mismatch == "merge":
        record["merge_commit_sha"] = str(head["sha"])
    else:
        record["merged_at"] = None
    assert not cleanup.remove(target="42", force=True, project_root=root)
    assert wt.is_dir()


def test_rewritten_base_dropping_merge_is_refused(
    squash_repo: tuple[Path, Path, dict[str, object]],
) -> None:
    root, wt, _ = squash_repo
    git(root, "reset", "--hard", "HEAD^")
    git(root, "push", "--force", "origin", "main")
    assert not cleanup.remove(target="42", force=True, project_root=root)
    assert wt.is_dir()


def test_failed_fetch_never_uses_cached_squash_proof(
    squash_repo: tuple[Path, Path, dict[str, object]],
) -> None:
    root, wt, _ = squash_repo
    # Keep cached origin/main, but make the same configured origin unavailable.
    keys = git(root, "config", "--name-only", "--get-regexp", r"url\..*\.insteadof").splitlines()
    git(root, "config", "--unset", keys[0])
    git(
        root,
        "config",
        f"url.{root / 'absent.git'}.insteadOf",
        "https://github.com/example/cleanup.git",
    )
    assert not cleanup.remove(target="42", force=True, project_root=root)
    assert wt.is_dir()


@pytest.mark.parametrize("change", ["head", "tracked", "untracked"])
def test_changes_during_lookup_are_rechecked(
    squash_repo: tuple[Path, Path, dict[str, object]], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    root, wt, record = squash_repo

    def query(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if change == "head":
            git(wt, "commit", "--allow-empty", "-m", "chore: concurrent work")
        elif change == "tracked":
            (wt / "feature.txt").write_text("concurrent edit\n")
        else:
            (wt / "late.txt").write_text("concurrent file\n")
        return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")

    monkeypatch.setattr("wade.git.pr._run_gh", query)
    assert not cleanup.remove(target="42", force=True, project_root=root)
    assert wt.is_dir()


@pytest.mark.parametrize("change", ["head", "tracked", "untracked"])
def test_changes_after_preservation_are_rechecked(
    squash_repo: tuple[Path, Path, dict[str, object]], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    root, wt, _ = squash_repo

    def preserve(*args: object) -> None:
        if change == "head":
            git(wt, "commit", "--allow-empty", "-m", "chore: late work")
        elif change == "tracked":
            (wt / "feature.txt").write_text("late edit\n")
        else:
            (wt / "late.txt").write_text("late file\n")

    monkeypatch.setattr(cleanup, "_preserve_session_data", preserve)
    assert not cleanup.remove(target="42", force=True, project_root=root)
    assert wt.is_dir()


def test_classification_and_stale_cleanup_share_one_lookup(
    squash_repo: tuple[Path, Path, dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from wade.models.session import WorktreeState
    from wade.services.implementation_service.sync import classify_staleness

    root, wt, record = squash_repo
    assert classify_staleness(root, "feat/42-squash", "main") == WorktreeState.STALE_MERGED
    queries: list[tuple[str, ...]] = []

    def query(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        queries.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps([record]), "")

    monkeypatch.setattr("wade.git.pr._run_gh", query)
    provider = MagicMock()
    provider.read_task_or_none.return_value = None
    monkeypatch.setattr(cleanup, "get_provider", lambda _: provider)
    assert cleanup.remove(stale=True, force=True, project_root=root)
    assert len(queries) == 1
    assert not wt.exists()


@pytest.mark.parametrize("failed", [False, True])
def test_task_safeguards_precede_merge_lookup(
    squash_repo: tuple[Path, Path, dict[str, object]], monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    from wade.models.task import Task, TaskState

    root, wt, _ = squash_repo
    provider = MagicMock()
    if failed:
        provider.read_task_or_none.side_effect = RuntimeError("task API unavailable")
    else:
        provider.read_task_or_none.return_value = Task(
            id="42", title="open task", state=TaskState.OPEN
        )
    monkeypatch.setattr(cleanup, "get_provider", lambda _: provider)
    monkeypatch.setattr(
        "wade.git.pr._run_gh",
        lambda *a, **kw: pytest.fail("open/unknown task must not query PR proof"),
    )
    assert cleanup.remove(stale=True, force=True, project_root=root)
    assert wt.is_dir()


def test_ordinary_ancestor_cleanup_is_available_offline(
    squash_repo: tuple[Path, Path, dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, wt, _ = squash_repo
    git(root, "merge", "--no-ff", "feat/42-squash", "-m", "feat: ordinary merge")
    monkeypatch.setattr(
        "wade.git.pr._run_gh", lambda *a, **kw: pytest.fail("ancestor cleanup must stay offline")
    )
    assert cleanup.remove(target="42", force=True, project_root=root)
    assert not wt.exists()


def test_detached_head_is_not_silently_safe(
    squash_repo: tuple[Path, Path, dict[str, object]],
) -> None:
    root, wt, _ = squash_repo
    git(wt, "checkout", "--detach")
    assert not cleanup._cleanup_worktree(root, wt, "main")
    assert wt.is_dir()


def test_real_rebase_merge_with_interacting_commits(
    squash_repo: tuple[Path, Path, dict[str, object]],
) -> None:
    root, wt, record = squash_repo
    git(root, "reset", "--hard", "HEAD^")
    (root / "base.txt").write_text("new base\n")
    git(root, "add", "base.txt")
    git(root, "commit", "-m", "feat: advance before rebase")
    original_commits = git(root, "rev-list", "--reverse", "main..feat/42-squash").splitlines()
    git(root, "cherry-pick", *original_commits)
    record["merge_commit_sha"] = git(root, "rev-parse", "HEAD")
    git(root, "push", "--force", "origin", "main")
    assert cleanup.remove(target="42", force=True, project_root=root)
    assert not wt.exists()


@pytest.mark.parametrize("failure", ["missing-pr", "missing-object", "unavailable-api"])
def test_unknown_or_unmerged_history_is_preserved(
    squash_repo: tuple[Path, Path, dict[str, object]], monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from wade.git.pr import GhCliError
    from wade.models.session import WorktreeState
    from wade.services.implementation_service.sync import classify_staleness

    root, wt, record = squash_repo
    if failure == "missing-object":
        record["merge_commit_sha"] = "f" * 40
    elif failure == "missing-pr":
        monkeypatch.setattr(
            "wade.git.pr._run_gh", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "[]", "")
        )
    else:

        def unavailable(*args: str, **kwargs: object) -> None:
            raise GhCliError("HTTP 401: credentials unavailable")

        monkeypatch.setattr("wade.git.pr._run_gh", unavailable)
    # Independently measured unchanged-main behavior: trackshort omits the
    # deleted-upstream marker, so this case stays ACTIVE. Do not broaden this
    # fix into tracking-label repair, or confuse absence with merge proof.
    assert classify_staleness(root, "feat/42-squash", "main") == WorktreeState.ACTIVE
    head_before = git(wt, "rev-parse", "HEAD")
    bytes_before = (wt / "feature.txt").read_bytes()
    assert not cleanup.remove(target="42", force=True, project_root=root)
    assert wt.is_dir()
    assert git(wt, "rev-parse", "HEAD") == head_before
    assert (wt / "feature.txt").read_bytes() == bytes_before


@pytest.mark.parametrize("change", ["origin", "base"])
def test_repository_and_base_bindings_are_rechecked(
    squash_repo: tuple[Path, Path, dict[str, object]], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    root, wt, _ = squash_repo

    def preserve(*args: object) -> None:
        if change == "origin":
            git(root, "remote", "set-url", "origin", "https://github.com/wrong/cleanup.git")
        else:
            git(root, "update-ref", "refs/remotes/origin/main", "main^")

    monkeypatch.setattr(cleanup, "_preserve_session_data", preserve)
    assert not cleanup.remove(target="42", force=True, project_root=root)
    assert wt.is_dir()


def test_git_refuses_changes_after_last_cleanliness_check(
    squash_repo: tuple[Path, Path, dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from wade.git.worktree import remove_worktree

    root, wt, _ = squash_repo

    def remove_late(repo_root: Path, worktree_path: Path, force: bool = True) -> None:
        assert force is False
        (worktree_path / "last-instant.txt").write_text("user file\n")
        remove_worktree(repo_root, worktree_path, force=force)

    monkeypatch.setattr(cleanup.git_worktree, "remove_worktree", remove_late)
    assert not cleanup.remove(target="42", force=True, project_root=root)
    assert (wt / "last-instant.txt").read_text() == "user file\n"
