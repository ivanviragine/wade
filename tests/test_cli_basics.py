"""Basic CLI smoke tests — version, help, subcommand structure."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

import wade
from wade.cli.main import app

runner = CliRunner()


class TestVersion:
    def test_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "wade" in result.output
        assert wade.__version__ in result.output

    def test_version_short_flag(self) -> None:
        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        assert wade.__version__ in result.output


class TestHelp:
    def test_root_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "wade" in result.output
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
        # plan-task exits 1 when no AI tool is available.
        # Patch both config loading and auto-detection so the test is not
        # environment-dependent: a real .wade.yml or installed AI CLI would
        # resolve a tool and confirm_ai_selection would block on TTY input.
        from wade.models.config import ProjectConfig

        with (
            patch("wade.services.plan_service.load_config", return_value=ProjectConfig()),
            patch("wade.ai_tools.base.AbstractAITool.detect_installed", return_value=[]),
        ):
            result = runner.invoke(app, ["plan-task"])
        assert result.exit_code == 1

    def test_new_task_requires_title(self) -> None:
        result = runner.invoke(app, ["new-task"])
        assert result.exit_code == 1
        assert "Title is required" in result.output

    @patch("wade.services.task_service.create_task")
    def test_new_task_non_interactive_title(self, mock_create: patch) -> None:
        from wade.models.task import Task

        mock_create.return_value = Task(id="1", title="My Bug")
        result = runner.invoke(app, ["new-task", "--title", "My Bug"])
        assert result.exit_code == 0
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["title"] == "My Bug"
        assert call_kwargs["body"] == ""

    @patch("wade.services.task_service.create_task")
    def test_new_task_non_interactive_with_body(self, mock_create: patch) -> None:
        from wade.models.task import Task

        mock_create.return_value = Task(id="2", title="Fix")
        result = runner.invoke(app, ["new-task", "--title", "Fix", "--body", "Details here"])
        assert result.exit_code == 0
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["body"] == "Details here"

    @patch("wade.services.task_service.create_task")
    def test_new_task_non_interactive_body_file(self, mock_create: patch) -> None:
        import tempfile

        from wade.models.task import Task

        mock_create.return_value = Task(id="3", title="Fix")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("Body from file")
            f.flush()
            result = runner.invoke(app, ["new-task", "--title", "Fix", "--body-file", f.name])
        assert result.exit_code == 0
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["body"] == "Body from file"

    @patch("wade.services.task_service.create_task")
    def test_new_task_non_interactive_labels(self, mock_create: patch) -> None:
        from wade.models.task import Task

        mock_create.return_value = Task(id="4", title="Fix")
        result = runner.invoke(
            app, ["new-task", "--title", "Fix", "--label", "bug", "--label", "urgent"]
        )
        assert result.exit_code == 0
        call_kwargs = mock_create.call_args[1]
        assert "bug" in call_kwargs["extra_labels"]
        assert "urgent" in call_kwargs["extra_labels"]

    def test_new_task_body_file_not_found(self) -> None:
        result = runner.invoke(
            app, ["new-task", "--title", "Fix", "--body-file", "/nonexistent/file.md"]
        )
        assert result.exit_code == 1

    @patch("wade.services.task_service.list_tasks", return_value=[])
    def test_task_list_exits(self, mock_list: patch) -> None:
        # task list exits 0 when no tasks are found
        result = runner.invoke(app, ["task", "list"])
        assert result.exit_code == 0

    def test_work_done_exits_with_error(self) -> None:
        # work done exits 1 when the branch has no issue number.
        # Mock the branch name so the test is not environment-dependent
        # (on a feature worktree the branch has an issue number and the
        # error path is different).
        with patch("wade.git.repo.get_current_branch", return_value="main"):
            result = runner.invoke(app, ["work", "done"])
        assert result.exit_code == 1
        assert "Cannot extract issue number" in result.output

    def test_work_sync_exits_with_error(self) -> None:
        # work sync outside a worktree exits 4 (preflight failure)
        result = runner.invoke(app, ["work", "sync"])
        assert result.exit_code == 4

    @patch("wade.git.worktree.list_worktrees", return_value=[])
    def test_work_list_exits(self, mock_wt: patch) -> None:
        # work list gracefully handles missing git context — returns empty list
        result = runner.invoke(app, ["work", "list"])
        assert result.exit_code == 0
        assert "No active wade worktrees" in result.output


class TestInteractiveMenu:
    """Verify that wade with no args invokes the interactive menu."""

    def test_no_args_invokes_menu(self) -> None:
        """Running wade with no subcommand should call _interactive_main_menu."""
        with patch("wade.cli.main._interactive_main_menu") as mock_menu:
            runner.invoke(app, [])
            mock_menu.assert_called_once()

    def test_help_selection_exits_cleanly(self) -> None:
        """Selecting 'Show help' (index 4) from the menu should exit 0."""
        with patch("wade.ui.prompts.menu", return_value=4):
            result = runner.invoke(app, [])
            assert result.exit_code == 0
