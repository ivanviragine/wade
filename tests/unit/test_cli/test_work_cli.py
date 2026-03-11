"""CLI-level regression tests for implementation/worktree subcommands."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from wade.cli.implementation_session import implementation_session_app
from wade.cli.main import app
from wade.cli.worktree import worktree_app
from wade.models.session import SyncEvent, SyncEventType, SyncResult

runner = CliRunner()


def _sync_result(
    *,
    success: bool,
    conflicts: list[str] | None = None,
    events: list[SyncEvent] | None = None,
) -> SyncResult:
    return SyncResult(
        success=success,
        current_branch="feat/42-test",
        main_branch="main",
        conflicts=conflicts or [],
        events=events or [],
    )


class TestWorkSyncExitCodes:
    def test_sync_success_maps_to_exit_zero(self) -> None:
        with patch(
            "wade.services.implementation_service.sync",
            return_value=_sync_result(success=True),
        ) as mock_sync:
            result = runner.invoke(implementation_session_app, ["sync"])
        assert result.exit_code == 0
        mock_sync.assert_called_once_with(
            dry_run=False,
            main_branch=None,
            json_output=False,
            session_type="implementation",
        )

    def test_sync_conflicts_map_to_exit_two(self) -> None:
        with patch(
            "wade.services.implementation_service.sync",
            return_value=_sync_result(success=False, conflicts=["README.md"]),
        ) as mock_sync:
            result = runner.invoke(implementation_session_app, ["sync"])
        assert result.exit_code == 2
        mock_sync.assert_called_once_with(
            dry_run=False,
            main_branch=None,
            json_output=False,
            session_type="implementation",
        )

    def test_sync_preflight_error_maps_to_exit_four(self) -> None:
        preflight_event = SyncEvent(
            event=SyncEventType.ERROR,
            data={"reason": "on_main_branch"},
        )
        with patch(
            "wade.services.implementation_service.sync",
            return_value=_sync_result(success=False, events=[preflight_event]),
        ) as mock_sync:
            result = runner.invoke(implementation_session_app, ["sync"])
        assert result.exit_code == 4
        mock_sync.assert_called_once_with(
            dry_run=False,
            main_branch=None,
            json_output=False,
            session_type="implementation",
        )

    def test_sync_other_error_maps_to_exit_one(self) -> None:
        generic_error = SyncEvent(
            event=SyncEventType.ERROR,
            data={"reason": "unknown"},
        )
        with patch(
            "wade.services.implementation_service.sync",
            return_value=_sync_result(success=False, events=[generic_error]),
        ) as mock_sync:
            result = runner.invoke(implementation_session_app, ["sync"])
        assert result.exit_code == 1
        mock_sync.assert_called_once_with(
            dry_run=False,
            main_branch=None,
            json_output=False,
            session_type="implementation",
        )


class TestWorkOtherCommands:
    def test_batch_requires_numbers_when_non_interactive(self) -> None:
        with patch("wade.ui.prompts.is_tty", return_value=False):
            result = runner.invoke(app, ["implement-batch"])
        assert result.exit_code == 1
        assert "Provide at least one issue number." in result.output

    def test_remove_all_alias_sets_stale_mode(self) -> None:
        with patch("wade.services.implementation_service.remove", return_value=True) as mock_remove:
            result = runner.invoke(worktree_app, ["remove", "--all", "--force"])
        assert result.exit_code == 0
        mock_remove.assert_called_once_with(target=None, stale=True, force=True)
