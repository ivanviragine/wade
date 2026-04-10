"""Unit tests for catchup() — startup sync with base branch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.models.session import SyncEventType


class TestCatchupUpToDate:
    """Branch is already in sync with base — no merge needed."""

    @patch("wade.services.implementation_service.core.git_sync")
    @patch("wade.services.implementation_service.core.git_branch")
    @patch("wade.services.implementation_service.core.git_repo")
    @patch("wade.services.implementation_service.core.load_config")
    def test_returns_success_when_up_to_date(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.implementation_service import catchup

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/1-my-feature"
        mock_repo.is_clean.return_value = True
        mock_repo.has_remote.return_value = False
        mock_repo.detect_main_branch.return_value = "main"
        mock_branch.commits_ahead.return_value = 0

        result = catchup(project_root=tmp_path)

        assert result.success is True
        assert result.conflicts == []
        assert result.commits_merged == 0
        up_to_date_events = [e for e in result.events if e.event == SyncEventType.UP_TO_DATE]
        assert len(up_to_date_events) == 1
        mock_sync.merge_branch.assert_not_called()


class TestCatchupCleanMerge:
    """Branch is behind — merge succeeds without conflicts."""

    @patch("wade.services.implementation_service.core.git_sync")
    @patch("wade.services.implementation_service.core.git_branch")
    @patch("wade.services.implementation_service.core.git_repo")
    @patch("wade.services.implementation_service.core.load_config")
    def test_merges_when_behind(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        from wade.models.config import ProjectConfig
        from wade.models.session import SyncResult
        from wade.services.implementation_service import catchup

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/1-my-feature"
        mock_repo.is_clean.return_value = True
        mock_repo.has_remote.return_value = False
        mock_repo.detect_main_branch.return_value = "main"
        mock_branch.commits_ahead.return_value = 3
        mock_sync.merge_branch.return_value = SyncResult(
            success=True, current_branch="feat/1-my-feature", main_branch="main", commits_merged=3
        )

        result = catchup(project_root=tmp_path)

        assert result.success is True
        assert result.conflicts == []
        mock_sync.merge_branch.assert_called_once()
        mock_sync.abort_merge.assert_not_called()
        merged_events = [e for e in result.events if e.event == SyncEventType.MERGED]
        assert len(merged_events) == 1


class TestCatchupConflict:
    """Merge has conflicts — catchup aborts and reports."""

    @patch("wade.services.implementation_service.core.git_sync")
    @patch("wade.services.implementation_service.core.git_branch")
    @patch("wade.services.implementation_service.core.git_repo")
    @patch("wade.services.implementation_service.core.load_config")
    def test_aborts_merge_on_conflict(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        from wade.models.config import ProjectConfig
        from wade.models.session import SyncResult
        from wade.services.implementation_service import catchup

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/1-my-feature"
        mock_repo.is_clean.return_value = True
        mock_repo.has_remote.return_value = False
        mock_repo.detect_main_branch.return_value = "main"
        mock_branch.commits_ahead.return_value = 2
        mock_sync.merge_branch.return_value = SyncResult(
            success=False,
            current_branch="feat/1-my-feature",
            main_branch="main",
            conflicts=["src/foo.py", "src/bar.py"],
        )

        result = catchup(project_root=tmp_path)

        assert result.success is False
        assert result.conflicts == ["src/foo.py", "src/bar.py"]
        # Abort must be called to leave the worktree clean
        mock_sync.abort_merge.assert_called_once_with(tmp_path)
        conflict_events = [e for e in result.events if e.event == SyncEventType.CONFLICT]
        assert len(conflict_events) == 1

    @patch("wade.services.implementation_service.core.git_sync")
    @patch("wade.services.implementation_service.core.git_branch")
    @patch("wade.services.implementation_service.core.git_repo")
    @patch("wade.services.implementation_service.core.load_config")
    def test_conflict_diff_not_emitted(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        """catchup does not emit CONFLICT_DIFF (that is sync-only)."""
        from wade.models.config import ProjectConfig
        from wade.models.session import SyncResult
        from wade.services.implementation_service import catchup

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/1-my-feature"
        mock_repo.is_clean.return_value = True
        mock_repo.has_remote.return_value = False
        mock_repo.detect_main_branch.return_value = "main"
        mock_branch.commits_ahead.return_value = 1
        mock_sync.merge_branch.return_value = SyncResult(
            success=False,
            current_branch="feat/1-my-feature",
            main_branch="main",
            conflicts=["src/foo.py"],
        )

        result = catchup(project_root=tmp_path)

        diff_events = [e for e in result.events if e.event == SyncEventType.CONFLICT_DIFF]
        assert diff_events == []


class TestCatchupStackedBranch:
    """Stacked branch: catchup uses .wade/base_branch instead of main."""

    @patch("wade.services.implementation_service.core.git_sync")
    @patch("wade.services.implementation_service.core.git_branch")
    @patch("wade.services.implementation_service.core.git_repo")
    @patch("wade.services.implementation_service.core.load_config")
    def test_uses_stored_base_branch(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.implementation_service import catchup

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/20-child"
        mock_repo.is_clean.return_value = True
        mock_repo.has_remote.return_value = False
        mock_branch.branch_exists.return_value = True
        mock_branch.commits_ahead.return_value = 0

        wade_dir = tmp_path / ".wade"
        wade_dir.mkdir()
        (wade_dir / "base_branch").write_text("feat/10-parent\n")

        result = catchup(project_root=tmp_path)

        assert result.success is True
        assert result.main_branch == "feat/10-parent"
        preflight_events = [e for e in result.events if e.event == SyncEventType.PREFLIGHT_OK]
        assert preflight_events[0].data.get("main_branch") == "feat/10-parent"


class TestCatchupDirtyWorktree:
    """Dirty worktree: catchup reports pre-flight failure."""

    @patch("wade.services.implementation_service.core.git_sync")
    @patch("wade.services.implementation_service.core.git_branch")
    @patch("wade.services.implementation_service.bootstrap.git_repo")
    @patch("wade.services.implementation_service.core.git_repo")
    @patch("wade.services.implementation_service.core.load_config")
    def test_fails_on_dirty_worktree(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        _mock_bootstrap_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.implementation_service import catchup

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/1-my-feature"
        mock_repo.is_clean.return_value = False
        mock_repo.detect_main_branch.return_value = "main"

        result = catchup(project_root=tmp_path)

        assert result.success is False
        assert result.conflicts == []
        mock_sync.merge_branch.assert_not_called()
        error_events = [e for e in result.events if e.event == SyncEventType.ERROR]
        assert any(e.data.get("reason") == "dirty_worktree" for e in error_events)


class TestCatchupDryRun:
    """Dry-run mode: preview without merging."""

    @patch("wade.services.implementation_service.core.git_sync")
    @patch("wade.services.implementation_service.core.git_branch")
    @patch("wade.services.implementation_service.core.git_repo")
    @patch("wade.services.implementation_service.core.load_config")
    def test_dry_run_does_not_merge(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.implementation_service import catchup

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/1-my-feature"
        mock_repo.is_clean.return_value = True
        mock_repo.has_remote.return_value = False
        mock_repo.detect_main_branch.return_value = "main"
        mock_branch.commits_ahead.return_value = 5

        result = catchup(dry_run=True, project_root=tmp_path)

        assert result.success is True
        mock_sync.merge_branch.assert_not_called()
        dry_run_events = [e for e in result.events if e.event == SyncEventType.DRY_RUN]
        assert len(dry_run_events) == 1
