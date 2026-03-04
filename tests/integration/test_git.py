"""Integration tests for the wade.git layer.

These tests exercise real git operations against temporary repositories
created by the ``tmp_git_repo`` fixture from conftest.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wade.git import (
    GitError,
    branch_exists,
    commits_ahead,
    create_branch,
    create_worktree,
    delete_branch,
    detect_main_branch,
    get_conflicted_files,
    get_current_branch,
    get_remote_url,
    get_repo_root,
    is_clean,
    is_git_repo,
    is_worktree,
    list_worktrees,
    make_branch_name,
    prune_worktrees,
    remove_worktree,
)
from wade.git.sync import abort_merge, merge_branch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    """Run a git command in *repo* and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _commit_file(repo: Path, name: str, content: str, message: str) -> None:
    """Write a file, add it, and commit."""
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)


# ===========================================================================
# Repo introspection
# ===========================================================================


class TestIsGitRepo:
    def test_true_for_git_repo(self, tmp_git_repo: Path) -> None:
        assert is_git_repo(tmp_git_repo) is True

    def test_false_for_plain_dir(self, tmp_path: Path) -> None:
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert is_git_repo(plain) is False


class TestGetRepoRoot:
    def test_returns_repo_root(self, tmp_git_repo: Path) -> None:
        root = get_repo_root(tmp_git_repo)
        assert root == tmp_git_repo.resolve()

    def test_from_subdirectory(self, tmp_git_repo: Path) -> None:
        subdir = tmp_git_repo / "a" / "b"
        subdir.mkdir(parents=True)
        root = get_repo_root(subdir)
        assert root == tmp_git_repo.resolve()

    def test_raises_for_non_repo(self, tmp_path: Path) -> None:
        plain = tmp_path / "nope"
        plain.mkdir()
        with pytest.raises(GitError):
            get_repo_root(plain)


class TestGetCurrentBranch:
    def test_default_branch(self, tmp_git_repo: Path) -> None:
        branch = get_current_branch(tmp_git_repo)
        # The conftest uses whatever the global default is (usually main or master)
        assert branch in ("main", "master")

    def test_feature_branch(self, tmp_git_repo: Path) -> None:
        _git(tmp_git_repo, "checkout", "-b", "feat/test")
        assert get_current_branch(tmp_git_repo) == "feat/test"


class TestDetectMainBranch:
    def test_detects_main(self, tmp_git_repo: Path) -> None:
        # The conftest creates a repo; the default branch is whatever git uses.
        # Ensure we detect it.
        detected = detect_main_branch(tmp_git_repo)
        assert detected in ("main", "master")

    def test_detects_master_fallback(self, tmp_path: Path) -> None:
        """Create a repo with only a 'master' branch."""
        repo = tmp_path / "master-repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "master"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        (repo / "f.txt").write_text("x")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-m", "init")
        assert detect_main_branch(repo) == "master"

    def test_raises_when_neither_exists(self, tmp_path: Path) -> None:
        repo = tmp_path / "weird-repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "develop"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo,
            capture_output=True,
            check=True,
        )
        (repo / "f.txt").write_text("x")
        _git(repo, "add", "f.txt")
        _git(repo, "commit", "-m", "init")
        with pytest.raises(GitError, match="Neither"):
            detect_main_branch(repo)


class TestIsClean:
    def test_clean_after_commit(self, tmp_git_repo: Path) -> None:
        assert is_clean(tmp_git_repo) is True

    def test_dirty_with_unstaged_change(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "README.md").write_text("modified\n")
        assert is_clean(tmp_git_repo) is False

    def test_dirty_with_untracked_file(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "new.txt").write_text("new\n")
        assert is_clean(tmp_git_repo) is False

    def test_dirty_with_staged_change(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "README.md").write_text("staged\n")
        _git(tmp_git_repo, "add", "README.md")
        assert is_clean(tmp_git_repo) is False


class TestGetRemoteUrl:
    def test_none_when_no_remote(self, tmp_git_repo: Path) -> None:
        assert get_remote_url(tmp_git_repo) is None

    def test_returns_url_when_remote_exists(self, tmp_git_repo: Path, tmp_path: Path) -> None:
        # Create a bare repo to act as a remote
        bare = tmp_path / "bare.git"
        subprocess.run(
            ["git", "init", "--bare", str(bare)],
            capture_output=True,
            check=True,
        )
        _git(tmp_git_repo, "remote", "add", "origin", str(bare))
        url = get_remote_url(tmp_git_repo)
        assert url == str(bare)


class TestIsWorktree:
    def test_main_checkout_is_not_worktree(self, tmp_git_repo: Path) -> None:
        assert is_worktree(tmp_git_repo) is False

    def test_linked_worktree_is_detected(self, tmp_git_repo: Path, tmp_path: Path) -> None:
        wt_dir = tmp_path / "wt"
        create_worktree(tmp_git_repo, "wt-branch", wt_dir)
        assert is_worktree(wt_dir) is True


# ===========================================================================
# Branch operations
# ===========================================================================


class TestMakeBranchName:
    def test_simple(self) -> None:
        name = make_branch_name("feat", 42, "Add user auth")
        assert name == "feat/42-add-user-auth"

    def test_special_characters_slugified(self) -> None:
        name = make_branch_name("fix", 7, "Fix: OAuth 2.0 (breaking!)")
        assert name.startswith("fix/7-")
        # Should not contain special chars
        assert ":" not in name
        assert "(" not in name
        assert "!" not in name

    def test_long_title_truncated(self) -> None:
        name = make_branch_name("feat", 1, "a " * 100)
        # The slug part should not exceed 50 chars
        slug_part = name.split("/", 1)[1].split("-", 1)[1]
        assert len(slug_part) <= 50

    def test_empty_title(self) -> None:
        name = make_branch_name("feat", 99, "")
        assert name == "feat/99-"


class TestBranchExists:
    def test_existing_branch(self, tmp_git_repo: Path) -> None:
        main = detect_main_branch(tmp_git_repo)
        assert branch_exists(tmp_git_repo, main) is True

    def test_nonexistent_branch(self, tmp_git_repo: Path) -> None:
        assert branch_exists(tmp_git_repo, "does-not-exist") is False


class TestCreateDeleteBranch:
    def test_create_and_delete(self, tmp_git_repo: Path) -> None:
        create_branch(tmp_git_repo, "new-branch")
        assert branch_exists(tmp_git_repo, "new-branch") is True

        delete_branch(tmp_git_repo, "new-branch")
        assert branch_exists(tmp_git_repo, "new-branch") is False

    def test_create_from_start_point(self, tmp_git_repo: Path) -> None:
        main = detect_main_branch(tmp_git_repo)
        create_branch(tmp_git_repo, "from-main", start_point=main)
        assert branch_exists(tmp_git_repo, "from-main") is True

    def test_force_delete_unmerged_branch(self, tmp_git_repo: Path) -> None:
        _git(tmp_git_repo, "checkout", "-b", "unmerged")
        _commit_file(tmp_git_repo, "x.txt", "x", "unmerged work")
        main = detect_main_branch(tmp_git_repo)
        _git(tmp_git_repo, "checkout", main)

        # Normal delete should fail for unmerged branch
        with pytest.raises(GitError):
            delete_branch(tmp_git_repo, "unmerged", force=False)

        # Force delete should succeed
        delete_branch(tmp_git_repo, "unmerged", force=True)
        assert branch_exists(tmp_git_repo, "unmerged") is False


class TestCommitsAhead:
    def test_zero_when_same(self, tmp_git_repo: Path) -> None:
        main = detect_main_branch(tmp_git_repo)
        assert commits_ahead(tmp_git_repo, main, main) == 0

    def test_counts_new_commits(self, tmp_git_repo: Path) -> None:
        main = detect_main_branch(tmp_git_repo)
        _git(tmp_git_repo, "checkout", "-b", "feature")
        _commit_file(tmp_git_repo, "a.txt", "a", "first")
        _commit_file(tmp_git_repo, "b.txt", "b", "second")

        assert commits_ahead(tmp_git_repo, "feature", main) == 2
        assert commits_ahead(tmp_git_repo, main, "feature") == 0


# ===========================================================================
# Worktree operations
# ===========================================================================


class TestCreateWorktree:
    def test_creates_worktree(self, tmp_git_repo: Path, tmp_path: Path) -> None:
        wt_dir = tmp_path / "my-worktree"
        main = detect_main_branch(tmp_git_repo)
        result = create_worktree(tmp_git_repo, "wt-branch", wt_dir, base_branch=main)
        assert result == wt_dir.resolve()
        assert result.is_dir()
        # The README from the main branch should be present
        assert (result / "README.md").exists()

    def test_worktree_has_correct_branch(self, tmp_git_repo: Path, tmp_path: Path) -> None:
        wt_dir = tmp_path / "wt2"
        main = detect_main_branch(tmp_git_repo)
        create_worktree(tmp_git_repo, "my-feature", wt_dir, base_branch=main)
        branch = get_current_branch(wt_dir)
        assert branch == "my-feature"


class TestListWorktrees:
    def test_lists_main_checkout(self, tmp_git_repo: Path) -> None:
        worktrees = list_worktrees(tmp_git_repo)
        assert len(worktrees) >= 1
        # The main checkout should be listed
        paths = [wt["path"] for wt in worktrees]
        assert str(tmp_git_repo.resolve()) in [str(Path(p).resolve()) for p in paths]

    def test_lists_created_worktrees(self, tmp_git_repo: Path, tmp_path: Path) -> None:
        main = detect_main_branch(tmp_git_repo)
        wt1 = tmp_path / "wt-a"
        wt2 = tmp_path / "wt-b"
        create_worktree(tmp_git_repo, "branch-a", wt1, base_branch=main)
        create_worktree(tmp_git_repo, "branch-b", wt2, base_branch=main)

        worktrees = list_worktrees(tmp_git_repo)
        branches = [wt.get("branch", "") for wt in worktrees]
        assert "branch-a" in branches
        assert "branch-b" in branches


class TestRemoveWorktree:
    def test_remove_worktree(self, tmp_git_repo: Path, tmp_path: Path) -> None:
        wt_dir = tmp_path / "to-remove"
        main = detect_main_branch(tmp_git_repo)
        create_worktree(tmp_git_repo, "temp-branch", wt_dir, base_branch=main)
        assert wt_dir.exists()

        remove_worktree(tmp_git_repo, wt_dir)
        assert not wt_dir.exists()

        # Should no longer appear in worktree list
        worktrees = list_worktrees(tmp_git_repo)
        branches = [wt.get("branch", "") for wt in worktrees]
        assert "temp-branch" not in branches


class TestPruneWorktrees:
    def test_prune_does_not_error_on_clean_repo(self, tmp_git_repo: Path) -> None:
        # Should not raise even if there's nothing to prune
        prune_worktrees(tmp_git_repo)


# ===========================================================================
# Sync operations
# ===========================================================================


class TestMergeBranch:
    def test_merge_no_conflicts(self, tmp_git_repo: Path) -> None:
        main = detect_main_branch(tmp_git_repo)

        # Create a feature branch with a new file
        _git(tmp_git_repo, "checkout", "-b", "feature-merge")
        _commit_file(tmp_git_repo, "feature.txt", "feature content", "add feature")

        # Switch back to main and make a non-conflicting change
        _git(tmp_git_repo, "checkout", main)
        _commit_file(tmp_git_repo, "main-only.txt", "main content", "main change")

        # Switch to feature and merge main
        _git(tmp_git_repo, "checkout", "feature-merge")
        result = merge_branch(tmp_git_repo, main)

        assert result.success is True
        assert result.current_branch == "feature-merge"
        assert result.main_branch == main
        assert result.conflicts == []
        assert result.commits_merged >= 1

    def test_merge_already_up_to_date(self, tmp_git_repo: Path) -> None:
        main = detect_main_branch(tmp_git_repo)
        # Merging main into itself should be a no-op
        result = merge_branch(tmp_git_repo, main)
        assert result.success is True
        assert result.commits_merged == 0

    def test_merge_with_conflicts(self, tmp_git_repo: Path) -> None:
        main = detect_main_branch(tmp_git_repo)

        # Create conflicting changes on main and feature
        _git(tmp_git_repo, "checkout", "-b", "conflict-branch")
        _commit_file(tmp_git_repo, "conflict.txt", "feature version", "feature conflict")

        _git(tmp_git_repo, "checkout", main)
        _commit_file(tmp_git_repo, "conflict.txt", "main version", "main conflict")

        _git(tmp_git_repo, "checkout", "conflict-branch")
        result = merge_branch(tmp_git_repo, main)

        assert result.success is False
        assert "conflict.txt" in result.conflicts

        # Clean up the merge state
        abort_merge(tmp_git_repo)


class TestGetConflictedFiles:
    def test_no_conflicts_returns_empty(self, tmp_git_repo: Path) -> None:
        assert get_conflicted_files(tmp_git_repo) == []

    def test_returns_conflicted_files(self, tmp_git_repo: Path) -> None:
        main = detect_main_branch(tmp_git_repo)

        _git(tmp_git_repo, "checkout", "-b", "conflict-test")
        _commit_file(tmp_git_repo, "c.txt", "branch version", "branch edit")

        _git(tmp_git_repo, "checkout", main)
        _commit_file(tmp_git_repo, "c.txt", "main version", "main edit")

        _git(tmp_git_repo, "checkout", "conflict-test")
        # Attempt merge (will fail with conflicts)
        subprocess.run(
            ["git", "merge", "--no-edit", main],
            cwd=tmp_git_repo,
            capture_output=True,
        )

        conflicts = get_conflicted_files(tmp_git_repo)
        assert "c.txt" in conflicts

        # Clean up
        abort_merge(tmp_git_repo)


class TestAbortMerge:
    def test_abort_active_merge(self, tmp_git_repo: Path) -> None:
        main = detect_main_branch(tmp_git_repo)

        _git(tmp_git_repo, "checkout", "-b", "abort-test")
        _commit_file(tmp_git_repo, "d.txt", "branch", "branch")

        _git(tmp_git_repo, "checkout", main)
        _commit_file(tmp_git_repo, "d.txt", "main", "main")

        _git(tmp_git_repo, "checkout", "abort-test")
        subprocess.run(
            ["git", "merge", "--no-edit", main],
            cwd=tmp_git_repo,
            capture_output=True,
        )

        # Should not raise
        abort_merge(tmp_git_repo)
        # Working tree should be clean after abort
        assert is_clean(tmp_git_repo) is True
