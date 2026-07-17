"""Integration tests for auto-stash sync/catchup — real git repos, no mocks."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from wade.models.config import ProjectConfig, ProjectSettings


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


def _commit(repo: Path, name: str, content: str, message: str) -> None:
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)


def _setup_branch_behind_main(repo: Path, branch: str = "feat/42-test") -> tuple[Path, str]:
    """Create a feature branch, add a commit to main, return (repo, branch)."""
    _git(repo, "checkout", "-b", branch)
    _git(repo, "checkout", "main")
    _commit(repo, "main-new.txt", "from main\n", "main: add new file")
    _git(repo, "checkout", branch)
    return repo, branch


# ===========================================================================
# Happy path: auto-stash, merge, restore
# ===========================================================================


class TestAutoStashHappyPath:
    """Sync with staged/unstaged user changes: stash → merge → pop."""

    def test_staged_changes_survive_sync(self, tmp_git_repo: Path) -> None:
        """Staged changes are preserved after sync merges main into feature branch."""
        from wade.services.implementation_service import sync

        repo, _ = _setup_branch_behind_main(tmp_git_repo)

        # Stage a user change on the feature branch
        (repo / "user-work.txt").write_text("my work\n")
        _git(repo, "add", "user-work.txt")
        assert _git(repo, "status", "--porcelain").startswith("A")

        with patch(
            "wade.services.implementation_service.sync.load_config",
            return_value=ProjectConfig(project=ProjectSettings(main_branch="main")),
        ):
            result = sync(project_root=repo)

        assert result.success, f"Sync failed: {[e.data for e in result.events]}"
        # Verify main-new.txt was merged in
        assert (repo / "main-new.txt").exists()
        # Verify user-work.txt was restored
        assert (repo / "user-work.txt").read_text() == "my work\n"
        # user-work.txt should still be staged after pop
        status = _git(repo, "status", "--porcelain")
        staged_line = next((ln for ln in status.splitlines() if ln.endswith("user-work.txt")), "")
        assert staged_line.startswith("A "), f"expected staged add, got: {staged_line!r}"

    def test_unstaged_changes_survive_sync(self, tmp_git_repo: Path) -> None:
        """Unstaged modifications to tracked files survive the sync."""
        from wade.services.implementation_service import sync

        repo, _ = _setup_branch_behind_main(tmp_git_repo)

        # Modify an existing tracked file without staging
        (repo / "README.md").write_text("modified by user\n")

        with patch(
            "wade.services.implementation_service.sync.load_config",
            return_value=ProjectConfig(project=ProjectSettings(main_branch="main")),
        ):
            result = sync(project_root=repo)

        assert result.success
        assert (repo / "README.md").read_text() == "modified by user\n"

    def test_autostashed_and_stash_restored_events_emitted(self, tmp_git_repo: Path) -> None:
        """AUTOSTASHED and STASH_RESTORED events appear in the result."""
        from wade.models.session import SyncEventType
        from wade.services.implementation_service import sync

        repo, _ = _setup_branch_behind_main(tmp_git_repo)
        (repo / "user-work.txt").write_text("my work\n")
        _git(repo, "add", "user-work.txt")

        with patch(
            "wade.services.implementation_service.sync.load_config",
            return_value=ProjectConfig(project=ProjectSettings(main_branch="main")),
        ):
            result = sync(project_root=repo)

        assert result.success
        assert any(e.event == SyncEventType.AUTOSTASHED for e in result.events)
        assert any(e.event == SyncEventType.STASH_RESTORED for e in result.events)

    def test_stash_name_is_wade_prefixed(self, tmp_git_repo: Path) -> None:
        """The created stash has the wade-autostash/ prefix for easy identification."""
        from wade.models.session import SyncEventType
        from wade.services.implementation_service import sync

        repo, _ = _setup_branch_behind_main(tmp_git_repo)
        (repo / "user-work.txt").write_text("my work\n")
        _git(repo, "add", "user-work.txt")

        with patch(
            "wade.services.implementation_service.sync.load_config",
            return_value=ProjectConfig(project=ProjectSettings(main_branch="main")),
        ):
            result = sync(project_root=repo)

        assert result.success
        stash_ev = next(e for e in result.events if e.event == SyncEventType.AUTOSTASHED)
        # After successful pop, the stash is gone from the list — but during sync
        # the ref should have been 'stash@{0}'
        assert "stash@{" in stash_ev.data["stash_ref"]
        assert stash_ev.data["stash_name"].startswith("wade-autostash/")


# ===========================================================================
# Untracked files
# ===========================================================================


class TestAutoStashUntrackedFiles:
    """Untracked-file behavior: collision detection and non-interference."""

    def test_untracked_non_colliding_passes(self, tmp_git_repo: Path) -> None:
        """Untracked file that main does not touch: sync proceeds."""
        from wade.services.implementation_service import sync

        repo, _ = _setup_branch_behind_main(tmp_git_repo)
        (repo / "my-notes.txt").write_text("scratch notes\n")

        with patch(
            "wade.services.implementation_service.sync.load_config",
            return_value=ProjectConfig(project=ProjectSettings(main_branch="main")),
        ):
            result = sync(project_root=repo)

        assert result.success
        assert (repo / "my-notes.txt").exists()

    def test_untracked_collision_fails_before_mutation(self, tmp_git_repo: Path) -> None:
        """Untracked file that main WOULD add: sync reports error before touching anything."""
        from wade.models.session import SyncEventType
        from wade.services.implementation_service import sync

        _git(tmp_git_repo, "checkout", "-b", "feat/42-test")
        _git(tmp_git_repo, "checkout", "main")
        # Add a file to main that the feature branch will have as untracked
        _commit(tmp_git_repo, "collision-file.txt", "from main\n", "add collision file")
        _git(tmp_git_repo, "checkout", "feat/42-test")

        # Create the untracked collision file
        (tmp_git_repo / "collision-file.txt").write_text("local untracked version\n")

        with patch(
            "wade.services.implementation_service.sync.load_config",
            return_value=ProjectConfig(project=ProjectSettings(main_branch="main")),
        ):
            result = sync(project_root=tmp_git_repo)

        assert not result.success
        assert any(e.event == SyncEventType.UNTRACKED_CONFLICT for e in result.events)
        conflict_ev = next(e for e in result.events if e.event == SyncEventType.UNTRACKED_CONFLICT)
        assert "collision-file.txt" in conflict_ev.data["paths"]
        # The local version must be unchanged (no mutation before error)
        assert (tmp_git_repo / "collision-file.txt").read_text() == "local untracked version\n"


# ===========================================================================
# --no-stash: strict old behavior
# ===========================================================================


class TestNoStashFlag:
    """--no-stash: dirty worktree always fails."""

    def test_no_stash_with_staged_changes_fails(self, tmp_git_repo: Path) -> None:
        """--no-stash: staged changes → dirty_worktree error."""
        from wade.models.session import SyncEventType
        from wade.services.implementation_service import sync

        _git(tmp_git_repo, "checkout", "-b", "feat/42-test")
        (tmp_git_repo / "user-work.txt").write_text("my work\n")
        _git(tmp_git_repo, "add", "user-work.txt")

        with patch(
            "wade.services.implementation_service.sync.load_config",
            return_value=ProjectConfig(project=ProjectSettings(main_branch="main")),
        ):
            result = sync(project_root=tmp_git_repo, no_stash=True)

        assert not result.success
        assert any(
            e.event == SyncEventType.ERROR and e.data.get("reason") == "dirty_worktree"
            for e in result.events
        )

    def test_no_stash_session_artifacts_fails(self, tmp_git_repo: Path) -> None:
        """--no-stash: even session artifacts (PLAN.md) → dirty_worktree error."""
        from wade.models.session import SyncEventType
        from wade.services.implementation_service import sync

        _git(tmp_git_repo, "checkout", "-b", "feat/42-test")
        (tmp_git_repo / "PLAN.md").write_text("# Plan\n")

        with patch(
            "wade.services.implementation_service.sync.load_config",
            return_value=ProjectConfig(project=ProjectSettings(main_branch="main")),
        ):
            result = sync(project_root=tmp_git_repo, no_stash=True)

        assert not result.success
        assert any(
            e.event == SyncEventType.ERROR and e.data.get("reason") == "dirty_worktree"
            for e in result.events
        )


# ===========================================================================
# Catchup auto-stash
# ===========================================================================


class TestCatchupAutoStash:
    """catchup() with auto-stash — real git."""

    def test_catchup_stashes_and_restores_on_clean_merge(self, tmp_git_repo: Path) -> None:
        """catchup: staged changes survive a clean merge."""
        from wade.models.session import SyncEventType
        from wade.services.implementation_service import catchup

        repo, _ = _setup_branch_behind_main(tmp_git_repo)
        (repo / "user-work.txt").write_text("catchup work\n")
        _git(repo, "add", "user-work.txt")

        with patch(
            "wade.services.implementation_service.sync.load_config",
            return_value=ProjectConfig(project=ProjectSettings(main_branch="main")),
        ):
            result = catchup(project_root=repo)

        assert result.success
        assert (repo / "user-work.txt").read_text() == "catchup work\n"
        assert any(e.event == SyncEventType.STASH_RESTORED for e in result.events)
