"""Tests for sync reading stacked base branch metadata."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.models.session import SyncEventType


class TestSyncReadsBaseBranchMetadata:
    """Verify that sync() uses .wade/base_branch when present."""

    @patch("wade.services.implementation_service.sync.git_sync")
    @patch("wade.services.implementation_service.sync.git_branch")
    @patch("wade.services.implementation_service.sync.git_repo")
    @patch("wade.services.implementation_service.sync.load_config")
    def test_sync_uses_stored_base_branch(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.implementation_service import sync

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/20-child"
        mock_repo.is_clean.return_value = True
        mock_repo.has_remote.return_value = False

        # No commits behind → up to date
        mock_branch.commits_ahead.return_value = 0

        # Write .wade/base_branch metadata
        wade_dir = tmp_path / ".wade"
        wade_dir.mkdir()
        (wade_dir / "base_branch").write_text("feat/10-parent\n")

        result = sync(project_root=tmp_path)

        assert result.success is True
        # The resolved main should be the stored base branch
        assert result.main_branch == "feat/10-parent"
        # Verify events used the stacked base
        preflight_events = [e for e in result.events if e.event == SyncEventType.PREFLIGHT_OK]
        assert len(preflight_events) == 1
        assert preflight_events[0].data.get("main_branch") == "feat/10-parent"

    @patch("wade.services.implementation_service.sync.git_sync")
    @patch("wade.services.implementation_service.sync.git_branch")
    @patch("wade.services.implementation_service.sync.git_repo")
    @patch("wade.services.implementation_service.sync.load_config")
    def test_sync_ignores_missing_metadata(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.implementation_service import sync

        mock_config.return_value = ProjectConfig(
            project=ProjectConfig.model_fields["project"].default
        )
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/20-child"
        mock_repo.is_clean.return_value = True
        mock_repo.has_remote.return_value = False
        mock_repo.detect_main_branch.return_value = "main"
        mock_branch.commits_ahead.return_value = 0

        # No .wade directory — should fall back to main
        result = sync(project_root=tmp_path)

        assert result.success is True
        assert result.main_branch == "main"

    @patch("wade.services.implementation_service.sync.git_sync")
    @patch("wade.services.implementation_service.sync.git_branch")
    @patch("wade.services.implementation_service.sync.git_repo")
    @patch("wade.services.implementation_service.sync.load_config")
    def test_sync_uses_remote_only_stored_base(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A plan-declared base that exists only on origin must still be honored (#376)."""
        from wade.models.config import ProjectConfig
        from wade.services.implementation_service import sync

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/20-child"
        mock_repo.is_clean.return_value = True
        mock_repo.has_remote.return_value = False
        mock_branch.commits_ahead.return_value = 0
        # Base exists ONLY as origin/develop, never checked out locally.
        mock_branch.branch_exists.return_value = False
        mock_branch.remote_ref_exists.return_value = True

        wade_dir = tmp_path / ".wade"
        wade_dir.mkdir()
        (wade_dir / "base_branch").write_text("develop\n")

        result = sync(project_root=tmp_path)

        assert result.success is True
        assert result.main_branch == "develop"

    @patch("wade.services.implementation_service.sync.git_sync")
    @patch("wade.services.implementation_service.sync.git_branch")
    @patch("wade.services.implementation_service.sync.git_repo")
    @patch("wade.services.implementation_service.sync.load_config")
    def test_sync_honors_uncached_stored_base(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A stored base is authoritative even when cached neither locally nor on
        origin (single-branch / narrow-refspec clone). It must be honored, NOT
        silently swapped for main — the fetch/merge path surfaces an unresolvable
        base as an error instead (#376)."""
        from wade.models.config import ProjectConfig
        from wade.services.implementation_service import sync

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/20-child"
        mock_repo.is_clean.return_value = True
        mock_repo.has_remote.return_value = False
        mock_repo.detect_main_branch.return_value = "main"
        mock_branch.commits_ahead.return_value = 0
        # Neither the local branch nor origin/<base> is cached — yet the stored
        # base must still be the resolved target, not a fall-back to main.
        mock_branch.branch_exists.return_value = False
        mock_branch.remote_ref_exists.return_value = False

        wade_dir = tmp_path / ".wade"
        wade_dir.mkdir()
        (wade_dir / "base_branch").write_text("develop\n")

        result = sync(project_root=tmp_path)

        assert result.success is True
        assert result.main_branch == "develop"

    @patch("wade.services.implementation_service.sync.git_sync")
    @patch("wade.services.implementation_service.sync.git_branch")
    @patch("wade.services.implementation_service.sync.git_repo")
    @patch("wade.services.implementation_service.sync.load_config")
    def test_sync_unresolvable_stored_base_errors_not_falls_back(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When even a fetch cannot resolve the stored base, sync surfaces a
        behind-count error against that base rather than silently merging into
        main (#376)."""
        from wade.git.repo import GitError
        from wade.models.config import ProjectConfig
        from wade.services.implementation_service import sync

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/20-child"
        mock_repo.is_clean.return_value = True
        # has_remote True → _resolve_merge_ref fetches and returns origin/develop,
        # but the ref is unresolvable so commits_ahead raises.
        mock_repo.has_remote.return_value = True
        mock_branch.commits_ahead.side_effect = GitError("bad revision 'origin/develop'")

        wade_dir = tmp_path / ".wade"
        wade_dir.mkdir()
        (wade_dir / "base_branch").write_text("develop\n")

        result = sync(project_root=tmp_path)

        assert result.success is False
        assert result.main_branch == "develop"
        errors = [e for e in result.events if e.event == SyncEventType.ERROR]
        assert any(e.data.get("reason") == "behind_count_failed" for e in errors)

    @patch("wade.services.implementation_service.sync.git_sync")
    @patch("wade.services.implementation_service.sync.git_branch")
    @patch("wade.services.implementation_service.sync.git_repo")
    @patch("wade.services.implementation_service.sync.load_config")
    def test_explicit_main_branch_overrides_metadata(
        self,
        mock_config: MagicMock,
        mock_repo: MagicMock,
        mock_branch: MagicMock,
        mock_sync: MagicMock,
        tmp_path: Path,
    ) -> None:
        from wade.models.config import ProjectConfig
        from wade.services.implementation_service import sync

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = tmp_path
        mock_repo.get_current_branch.return_value = "feat/20-child"
        mock_repo.is_clean.return_value = True
        mock_repo.has_remote.return_value = False
        mock_branch.commits_ahead.return_value = 0

        # Write metadata that should be ignored
        wade_dir = tmp_path / ".wade"
        wade_dir.mkdir()
        (wade_dir / "base_branch").write_text("feat/10-parent\n")

        # Explicit --main-branch should take precedence
        result = sync(main_branch="develop", project_root=tmp_path)

        assert result.success is True
        assert result.main_branch == "develop"
