"""Basic CLI smoke tests — version, help, subcommand structure."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

import wade
from wade.cli.main import _should_print_version_banner, app

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


class TestVersionBannerRules:
    def test_knowledge_get_suppresses_startup_banner(self) -> None:
        assert not _should_print_version_banner("knowledge", ["wade", "knowledge", "get"])

    def test_root_flags_do_not_break_knowledge_get_suppression(self) -> None:
        assert not _should_print_version_banner(
            "knowledge", ["wade", "--verbose", "knowledge", "get"]
        )

    def test_other_subcommands_keep_startup_banner(self) -> None:
        assert _should_print_version_banner("task", ["wade", "task", "list"])


class TestHelp:
    def test_root_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "wade" in result.output
        assert "task" in result.output
        assert "worktree" in result.output
        assert "knowledge" in result.output

    def test_task_help(self) -> None:
        result = runner.invoke(app, ["task", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "read" in result.output

    def test_task_help_includes_create(self) -> None:
        result = runner.invoke(app, ["task", "--help"])
        assert result.exit_code == 0
        assert "create" in result.output

    def test_worktree_help(self) -> None:
        result = runner.invoke(app, ["worktree", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "remove" in result.output

    def test_knowledge_help(self) -> None:
        result = runner.invoke(app, ["knowledge", "--help"])
        assert result.exit_code == 0
        assert "add" in result.output
        assert "get" in result.output

    def test_implementation_session_help(self) -> None:
        result = runner.invoke(app, ["implementation-session", "--help"])
        assert result.exit_code == 0
        assert "done" in result.output
        assert "sync" in result.output
        assert "check" in result.output

    def test_review_pr_comments_session_help(self) -> None:
        result = runner.invoke(app, ["review-pr-comments-session", "--help"])
        assert result.exit_code == 0
        assert "done" in result.output
        assert "fetch" in result.output
        assert "resolve" in result.output

    def test_address_reviews_session_hidden_alias(self) -> None:
        """The old address-reviews-session alias should still work."""
        result = runner.invoke(app, ["address-reviews-session", "--help"])
        assert result.exit_code == 0
        assert "done" in result.output

    def test_top_level_commands_in_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "plan" in result.output
        assert "implement" in result.output


class TestCommandBehaviorWithoutContext:
    """Verify subcommand exit codes and output when run without git/gh context."""

    def test_plan_exits_without_ai(self) -> None:
        # plan exits 1 when no AI tool is available.
        # Patch both config loading and auto-detection so the test is not
        # environment-dependent: a real .wade.yml or installed AI CLI would
        # resolve a tool and confirm_ai_selection would block on TTY input.
        from wade.models.config import ProjectConfig

        with (
            patch("wade.services.plan_service.load_config", return_value=ProjectConfig()),
            patch("wade.ai_tools.base.AbstractAITool.detect_installed", return_value=[]),
        ):
            result = runner.invoke(app, ["plan"])
        assert result.exit_code == 1
        assert "No AI tool specified and none detected" in result.output

    def test_task_create_requires_title(self) -> None:
        result = runner.invoke(app, ["task", "create"])
        assert result.exit_code == 1
        assert "Title is required" in result.output

    @patch("wade.services.task_service.create_task")
    def test_task_create_non_interactive_title(self, mock_create: patch) -> None:
        from wade.models.task import Task

        mock_create.return_value = Task(id="1", title="My Bug")
        result = runner.invoke(app, ["task", "create", "--title", "My Bug"])
        assert result.exit_code == 0
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["title"] == "My Bug"
        assert call_kwargs["body"] == ""

    @patch("wade.services.task_service.create_task")
    def test_task_create_non_interactive_with_body(self, mock_create: patch) -> None:
        from wade.models.task import Task

        mock_create.return_value = Task(id="2", title="Fix")
        result = runner.invoke(app, ["task", "create", "--title", "Fix", "--body", "Details here"])
        assert result.exit_code == 0
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["body"] == "Details here"

    @patch("wade.services.task_service.create_task")
    def test_task_create_non_interactive_body_file(self, mock_create: patch) -> None:
        import tempfile

        from wade.models.task import Task

        mock_create.return_value = Task(id="3", title="Fix")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("Body from file")
            f.flush()
            result = runner.invoke(app, ["task", "create", "--title", "Fix", "--body-file", f.name])
        assert result.exit_code == 0
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["body"] == "Body from file"

    @patch("wade.services.task_service.create_task")
    def test_task_create_non_interactive_labels(self, mock_create: patch) -> None:
        from wade.models.task import Task

        mock_create.return_value = Task(id="4", title="Fix")
        result = runner.invoke(
            app, ["task", "create", "--title", "Fix", "--label", "bug", "--label", "urgent"]
        )
        assert result.exit_code == 0
        call_kwargs = mock_create.call_args[1]
        assert "bug" in call_kwargs["extra_labels"]
        assert "urgent" in call_kwargs["extra_labels"]

    def test_task_create_body_file_not_found(self) -> None:
        result = runner.invoke(
            app, ["task", "create", "--title", "Fix", "--body-file", "/nonexistent/file.md"]
        )
        assert result.exit_code == 1
        assert "File not found: /nonexistent/file.md" in result.output

    @patch("wade.services.task_service.list_tasks", return_value=[])
    def test_task_list_exits(self, mock_list: patch) -> None:
        # task list exits 0 when no tasks are found
        result = runner.invoke(app, ["task", "list"])
        assert result.exit_code == 0
        mock_list.assert_called_once_with(state="open", show_deps=False, json_mode=False)

    def test_implementation_session_done_exits_with_error(self) -> None:
        # implementation-session done exits 1 when the branch has no issue number.
        # Mock the branch name so the test is not environment-dependent
        # (on a feature worktree the branch has an issue number and the
        # error path is different).
        with patch("wade.git.repo.get_current_branch", return_value="main"):
            result = runner.invoke(app, ["implementation-session", "done"])
        assert result.exit_code == 1
        assert "Cannot extract issue number" in result.output

    def test_implementation_session_sync_exits_with_error(self) -> None:
        # implementation-session sync on main exits 4 (preflight failure).
        # Mock branch name so this assertion is deterministic regardless of the
        # caller's checkout (main checkout vs feature worktree).
        with patch("wade.git.repo.get_current_branch", return_value="main"):
            result = runner.invoke(app, ["implementation-session", "sync"])
        assert result.exit_code == 4

    @patch("wade.git.worktree.list_worktrees", return_value=[])
    def test_worktree_list_exits(self, mock_wt: patch) -> None:
        # worktree list gracefully handles missing git context — returns empty list
        result = runner.invoke(app, ["worktree", "list"])
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
        """Selecting 'Show help' (index 5) from the menu should exit 0."""
        with patch("wade.ui.prompts.menu", return_value=5):
            result = runner.invoke(app, [])
            assert result.exit_code == 0
            assert "AI-agent-driven git workflow management CLI." in result.output
