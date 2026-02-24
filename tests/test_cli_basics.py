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
        assert "ghaiwpy" in result.output
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
        assert "plan" in result.output
        assert "create" in result.output
        assert "list" in result.output

    def test_work_help(self) -> None:
        result = runner.invoke(app, ["work", "--help"])
        assert result.exit_code == 0
        assert "start" in result.output
        assert "done" in result.output
        assert "sync" in result.output


class TestCommandBehaviorWithoutContext:
    """Verify subcommand exit codes and output when run without git/gh context."""

    def test_task_plan_exits_without_ai(self) -> None:
        # task plan exits 1 when no AI tool / gh available
        result = runner.invoke(app, ["task", "plan"])
        assert result.exit_code == 1

    def test_task_create_requires_plan_file(self) -> None:
        result = runner.invoke(app, ["task", "create"])
        assert result.exit_code == 1

    def test_task_list_exits(self) -> None:
        # task list gracefully handles missing gh auth — returns empty list
        result = runner.invoke(app, ["task", "list"])
        assert result.exit_code == 0
        assert "No tasks found" in result.output

    def test_work_done_exits_with_error(self) -> None:
        # work done requires git context; exits 1 without it
        result = runner.invoke(app, ["work", "done"])
        assert result.exit_code == 1

    def test_work_sync_exits_with_error(self) -> None:
        # work sync outside a worktree exits 4 (preflight failure)
        result = runner.invoke(app, ["work", "sync"])
        assert result.exit_code == 4

    def test_work_list_exits(self) -> None:
        # work list gracefully handles missing git context — returns empty list
        result = runner.invoke(app, ["work", "list"])
        assert result.exit_code == 0
        assert "No active ghaiw worktrees" in result.output


class TestInteractiveMenu:
    """Verify that ghaiwpy with no args invokes the interactive menu."""

    def test_no_args_invokes_menu(self) -> None:
        """Running ghaiwpy with no subcommand should call _interactive_main_menu."""
        with patch("ghaiw.cli.main._interactive_main_menu") as mock_menu:
            runner.invoke(app, [])
            mock_menu.assert_called_once()

    def test_help_selection_exits_cleanly(self) -> None:
        """Selecting 'Show help' (index 4) from the menu should exit 0."""
        with patch("ghaiw.ui.prompts.menu", return_value=4):
            result = runner.invoke(app, [])
            assert result.exit_code == 0
