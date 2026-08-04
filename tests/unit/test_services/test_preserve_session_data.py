"""Tests for _preserve_session_data() and its wiring into _cleanup_worktree()."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from crossby.models.ai import AIToolID

from wade.services.implementation_service import _cleanup_worktree, _preserve_session_data

_REPO = Path("/fake/repo")
_WT_PATH = Path("/fake/worktrees/feat-1-thing")
_MAIN_BRANCH = "main"


class TestPreserveSessionData:
    def test_noop_when_no_session_dirs(self, tmp_path: Path) -> None:
        """When no available tool's session_data_dirs are present, no adapter is called."""
        repo_root = tmp_path / "repo"
        wt_path = tmp_path / "wt"
        wt_path.mkdir(parents=True)

        mock_adapter = MagicMock()
        mock_adapter.session_data_dirs.return_value = [".claude"]

        with (
            patch(
                "wade.services.implementation_service.cleanup.AbstractAITool.available_tools",
                return_value=[AIToolID.CLAUDE],
            ),
            patch(
                "wade.services.implementation_service.cleanup.AbstractAITool.get",
                return_value=mock_adapter,
            ),
        ):
            # No .claude dir exists in wt_path, so detection misses and nothing is preserved.
            _preserve_session_data(repo_root, wt_path)

        mock_adapter.preserve_session_data.assert_not_called()

    def test_detects_tool_by_session_data_dirs(self, tmp_path: Path) -> None:
        """The adapter whose session_data_dirs directory exists is used to preserve."""
        repo_root = tmp_path / "repo"
        wt_path = tmp_path / "wt"
        wt_path.mkdir(parents=True)
        # Create .claude directory in worktree to trigger detection
        (wt_path / ".claude").mkdir()

        mock_adapter = MagicMock()
        mock_adapter.session_data_dirs.return_value = [".claude"]
        mock_adapter.preserve_session_data.return_value = True

        with (
            patch(
                "wade.services.implementation_service.cleanup.AbstractAITool.available_tools",
                return_value=[AIToolID.CLAUDE],
            ),
            patch(
                "wade.services.implementation_service.cleanup.AbstractAITool.get",
                return_value=mock_adapter,
            ),
        ):
            _preserve_session_data(repo_root, wt_path)

        mock_adapter.preserve_session_data.assert_called_once_with(wt_path, repo_root)

    def test_failure_does_not_propagate(self, tmp_path: Path) -> None:
        """Exception during detection/preservation is caught and logged, not raised."""
        repo_root = tmp_path / "repo"
        wt_path = tmp_path / "wt"
        wt_path.mkdir(parents=True)

        with (
            patch(
                "wade.services.implementation_service.cleanup.AbstractAITool.available_tools",
                side_effect=RuntimeError("tool discovery exploded"),
            ),
            patch("wade.services.implementation_service.cleanup.logger") as mock_logger,
        ):
            # Must not raise
            _preserve_session_data(repo_root, wt_path)

        mock_logger.warning.assert_called_once_with(
            "worktree.preserve_session_data_failed",
            worktree=str(wt_path),
            exc_info=True,
        )


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
                    "wade.services.implementation_service.cleanup._preserve_session_data",
                    side_effect=record_preserve,
                )
            )
            stack.enter_context(
                patch(
                    "wade.services.implementation_service.cleanup.git_worktree.remove_worktree",
                    side_effect=record_remove,
                )
            )
            stack.enter_context(
                patch(
                    "wade.services.implementation_service.cleanup.git_worktree.list_worktrees",
                    return_value=[],
                )
            )
            stack.enter_context(
                patch("wade.services.implementation_service.cleanup.git_worktree.prune_worktrees")
            )
            stack.enter_context(patch("wade.services.implementation_service.cleanup.console"))

            _cleanup_worktree(_REPO, _WT_PATH, _MAIN_BRANCH)

        assert call_order == ["preserve", "remove"]

    def test_removal_proceeds_when_preservation_fails(self, tmp_path: Path) -> None:
        """When preservation fails internally, removal still proceeds.

        _preserve_session_data catches all exceptions, so _cleanup_worktree
        should succeed even when tool detection/preservation raises.
        """
        repo_root = tmp_path / "repo"
        wt_path = tmp_path / "wt"
        wt_path.mkdir(parents=True)

        with ExitStack() as stack:
            # Let _preserve_session_data run but make tool discovery raise inside it.
            stack.enter_context(
                patch(
                    "wade.services.implementation_service.cleanup.AbstractAITool.available_tools",
                    side_effect=RuntimeError("tool discovery unavailable"),
                )
            )
            mock_remove = stack.enter_context(
                patch("wade.services.implementation_service.cleanup.git_worktree.remove_worktree")
            )
            stack.enter_context(
                patch(
                    "wade.services.implementation_service.cleanup.git_worktree.list_worktrees",
                    return_value=[],
                )
            )
            stack.enter_context(
                patch("wade.services.implementation_service.cleanup.git_worktree.prune_worktrees")
            )
            # A2 loss guard: treat the worktree as clean so removal is permitted
            # (this test exercises preservation-failure resilience, not the loss guard).
            stack.enter_context(
                patch(
                    "wade.services.implementation_service.cleanup.git_repo.is_clean",
                    return_value=True,
                )
            )
            stack.enter_context(patch("wade.services.implementation_service.cleanup.console"))
            stack.enter_context(patch("wade.services.implementation_service.cleanup.logger"))

            result = _cleanup_worktree(repo_root, wt_path, _MAIN_BRANCH)

        # Removal should still happen despite preservation failure
        mock_remove.assert_called_once()
        assert result is True
