"""Tests for _preserve_session_data() and its wiring into _cleanup_worktree()."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.services.implementation_service import _cleanup_worktree, _preserve_session_data

_REPO = Path("/fake/repo")
_WT_PATH = Path("/fake/worktrees/feat-1-thing")
_MAIN_BRANCH = "main"

# _preserve_session_data uses local imports, so we patch at their source locations.
_PATCH_ENGINE = "wade.db.engine.get_or_create_engine"
_PATCH_SESSION_REPO = "wade.db.repositories.SessionRepository"


class TestPreserveSessionData:
    def test_noop_when_no_db_and_no_session_dirs(self, tmp_path: Path) -> None:
        """When DB has no sessions and no session_data_dirs match, no adapter called."""
        repo_root = tmp_path / "repo"
        wt_path = tmp_path / "wt"
        wt_path.mkdir(parents=True)

        mock_engine = MagicMock()
        mock_session_repo = MagicMock()
        mock_session_repo.get_by_worktree_path.return_value = []

        with (
            patch(_PATCH_ENGINE, return_value=mock_engine),
            patch(_PATCH_SESSION_REPO, return_value=mock_session_repo),
        ):
            # Should not raise — no adapter selected
            _preserve_session_data(repo_root, wt_path)

        mock_session_repo.get_by_worktree_path.assert_called_once_with(str(wt_path))

    def test_uses_db_tool_when_sessions_exist(self, tmp_path: Path) -> None:
        """When DB has sessions for the worktree, the matching adapter is used."""
        from datetime import datetime

        from wade.db.tables import SessionRecord

        repo_root = tmp_path / "repo"
        wt_path = tmp_path / "wt"

        session_record = SessionRecord(
            task_id="42",
            session_type="implementation",
            ai_tool="claude",
            worktree_path=str(wt_path),
            started_at=datetime(2025, 1, 1),
        )

        mock_engine = MagicMock()
        mock_session_repo = MagicMock()
        mock_session_repo.get_by_worktree_path.return_value = [session_record]

        mock_adapter = MagicMock()
        mock_adapter.preserve_session_data.return_value = True

        with (
            patch(_PATCH_ENGINE, return_value=mock_engine),
            patch(_PATCH_SESSION_REPO, return_value=mock_session_repo),
            patch(
                "wade.services.implementation_service.AbstractAITool.get",
                return_value=mock_adapter,
            ),
        ):
            _preserve_session_data(repo_root, wt_path)

        mock_adapter.preserve_session_data.assert_called_once_with(wt_path, repo_root)

    def test_falls_back_to_session_data_dirs_detection(self, tmp_path: Path) -> None:
        """When DB returns no sessions, fall back to session_data_dirs() detection."""
        repo_root = tmp_path / "repo"
        wt_path = tmp_path / "wt"
        wt_path.mkdir(parents=True)
        # Create .claude directory in worktree to trigger detection
        (wt_path / ".claude").mkdir()

        mock_engine = MagicMock()
        mock_session_repo = MagicMock()
        mock_session_repo.get_by_worktree_path.return_value = []

        mock_adapter = MagicMock()
        mock_adapter.session_data_dirs.return_value = [".claude"]
        mock_adapter.preserve_session_data.return_value = True

        from wade.models.ai import AIToolID

        with (
            patch(_PATCH_ENGINE, return_value=mock_engine),
            patch(_PATCH_SESSION_REPO, return_value=mock_session_repo),
            patch(
                "wade.services.implementation_service.AbstractAITool.available_tools",
                return_value=[AIToolID.CLAUDE],
            ),
            patch(
                "wade.services.implementation_service.AbstractAITool.get",
                return_value=mock_adapter,
            ),
        ):
            _preserve_session_data(repo_root, wt_path)

        mock_adapter.preserve_session_data.assert_called_once_with(wt_path, repo_root)

    def test_failure_does_not_propagate(self, tmp_path: Path) -> None:
        """Exception in preserve_session_data is caught and logged, not raised."""
        repo_root = tmp_path / "repo"
        wt_path = tmp_path / "wt"

        with (
            patch(_PATCH_ENGINE, side_effect=RuntimeError("db exploded")),
            patch("wade.services.implementation_service.logger") as mock_logger,
        ):
            # Must not raise
            _preserve_session_data(repo_root, wt_path)

        mock_logger.warning.assert_called_once_with(
            "worktree.preserve_session_data_failed",
            worktree=str(wt_path),
            exc_info=True,
        )

    def test_unknown_tool_id_falls_back_to_dir_detection(self, tmp_path: Path) -> None:
        """When DB has a session with an unknown tool ID, fallback detection is used."""
        from datetime import datetime

        from wade.db.tables import SessionRecord

        repo_root = tmp_path / "repo"
        wt_path = tmp_path / "wt"
        wt_path.mkdir(parents=True)

        session_record = SessionRecord(
            task_id="99",
            session_type="implementation",
            ai_tool="unknown-future-tool",
            worktree_path=str(wt_path),
            started_at=datetime(2025, 1, 1),
        )

        mock_engine = MagicMock()
        mock_session_repo = MagicMock()
        mock_session_repo.get_by_worktree_path.return_value = [session_record]

        with (
            patch(_PATCH_ENGINE, return_value=mock_engine),
            patch(_PATCH_SESSION_REPO, return_value=mock_session_repo),
            patch(
                "wade.services.implementation_service.AbstractAITool.get",
                side_effect=ValueError("Unknown AI tool"),
            ),
            patch(
                "wade.services.implementation_service.AbstractAITool.available_tools",
                return_value=[],
            ),
        ):
            # Should not raise — just skip preservation
            _preserve_session_data(repo_root, wt_path)


class TestCleanupWorktreeCallsPreservation:
    def test_preservation_called_before_removal(self) -> None:
        """_preserve_session_data() is called before git_worktree.remove_worktree()."""
        call_order: list[str] = []

        def record_preserve(*args: object, **kwargs: object) -> None:
            call_order.append("preserve")

        def record_remove(*args: object, **kwargs: object) -> None:
            call_order.append("remove")

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "wade.services.implementation_service._preserve_session_data",
                    side_effect=record_preserve,
                )
            )
            stack.enter_context(
                patch(
                    "wade.services.implementation_service.git_worktree.remove_worktree",
                    side_effect=record_remove,
                )
            )
            stack.enter_context(
                patch(
                    "wade.services.implementation_service.git_worktree.list_worktrees",
                    return_value=[],
                )
            )
            stack.enter_context(
                patch("wade.services.implementation_service.git_worktree.prune_worktrees")
            )
            stack.enter_context(patch("wade.services.implementation_service.console"))

            _cleanup_worktree(_REPO, _WT_PATH, _MAIN_BRANCH)

        assert call_order == ["preserve", "remove"]

    def test_removal_proceeds_when_db_raises(self, tmp_path: Path) -> None:
        """When the DB raises in _preserve_session_data, removal still proceeds.

        _preserve_session_data catches all exceptions, so _cleanup_worktree
        should succeed even when preservation fails internally.
        """
        repo_root = tmp_path / "repo"
        wt_path = tmp_path / "wt"
        wt_path.mkdir(parents=True)

        with ExitStack() as stack:
            # Let _preserve_session_data run but make the DB raise
            stack.enter_context(patch(_PATCH_ENGINE, side_effect=RuntimeError("db unavailable")))
            mock_remove = stack.enter_context(
                patch("wade.services.implementation_service.git_worktree.remove_worktree")
            )
            stack.enter_context(
                patch(
                    "wade.services.implementation_service.git_worktree.list_worktrees",
                    return_value=[],
                )
            )
            stack.enter_context(
                patch("wade.services.implementation_service.git_worktree.prune_worktrees")
            )
            stack.enter_context(patch("wade.services.implementation_service.console"))
            stack.enter_context(patch("wade.services.implementation_service.logger"))

            result = _cleanup_worktree(repo_root, wt_path, _MAIN_BRANCH)

        # Removal should still happen despite preservation failure
        mock_remove.assert_called_once()
        assert result is True
