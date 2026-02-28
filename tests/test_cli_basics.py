"""Basic CLI smoke tests — version, help, subcommand structure."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

import ghaiw
from ghaiw.cli.main import app

runner = CliRunner()


class TestVersion:
    def test_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "ghaiw" in result.output
        assert ghaiw.__version__ in result.output

    def test_version_short_flag(self) -> None:
        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        assert ghaiw.__version__ in result.output


class TestHelp:
    def test_root_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "ghaiw" in result.output
        assert "task" in result.output
        assert "work" in result.output

    def test_task_help(self) -> None:
        result = runner.invoke(app, ["task", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "read" in result.output
        # plan and create are now top-level commands, not under task
        assert "plan" not in result.output
        assert "create" not in result.output

    def test_work_help(self) -> None:
        result = runner.invoke(app, ["work", "--help"])
        assert result.exit_code == 0
        assert "done" in result.output
        assert "sync" in result.output
        # start is now top-level implement-task, not under work
        assert "start" not in result.output

    def test_top_level_commands_in_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "plan-task" in result.output
        assert "new-task" in result.output
        assert "implement-task" in result.output


class TestCommandBehaviorWithoutContext:
    """Verify subcommand exit codes and output when run without git/gh context."""

    def test_plan_task_exits_without_ai(self) -> None:
        # plan-task exits 1 when no AI tool / gh available
        result = runner.invoke(app, ["plan-task"])
        assert result.exit_code == 1

    def test_new_task_requires_title(self) -> None:
        result = runner.invoke(app, ["new-task"])
        assert result.exit_code == 1
        assert "Title is required" in result.output

    @patch("ghaiw.services.task_service.list_tasks", return_value=[])
    def test_task_list_exits(self, mock_list: patch) -> None:
        # task list exits 0 when no tasks are found
        result = runner.invoke(app, ["task", "list"])
        assert result.exit_code == 0

    def test_work_done_exits_with_error(self) -> None:
        # work done requires git context; exits 1 without it
        result = runner.invoke(app, ["work", "done"])
        assert result.exit_code == 1
        assert "Cannot extract issue number" in result.output

    def test_work_sync_exits_with_error(self) -> None:
        # work sync outside a worktree exits 4 (preflight failure)
        result = runner.invoke(app, ["work", "sync"])
        assert result.exit_code == 4

    @patch("ghaiw.git.worktree.list_worktrees", return_value=[])
    def test_work_list_exits(self, mock_wt: patch) -> None:
        # work list gracefully handles missing git context — returns empty list
        result = runner.invoke(app, ["work", "list"])
        assert result.exit_code == 0
        assert "No active ghaiw worktrees" in result.output


class TestInteractiveMenu:
    """Verify that ghaiw with no args invokes the interactive menu."""

    def test_no_args_invokes_menu(self) -> None:
        """Running ghaiw with no subcommand should call _interactive_main_menu."""
        with patch("ghaiw.cli.main._interactive_main_menu") as mock_menu:
            runner.invoke(app, [])
            mock_menu.assert_called_once()

    def test_help_selection_exits_cleanly(self) -> None:
        """Selecting 'Show help' (index 4) from the menu should exit 0."""
        with patch("ghaiw.ui.prompts.menu", return_value=4):
            result = runner.invoke(app, [])
            assert result.exit_code == 0
