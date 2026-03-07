"""CLI-level regression tests for task subcommands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from wade.cli.task import task_app
from wade.models.config import ProjectConfig
from wade.models.task import Task, TaskState

runner = CliRunner()


class TestTaskDepsCLI:
    def test_deps_requires_numbers_when_non_interactive(self) -> None:
        with patch("wade.ui.prompts.is_tty", return_value=False):
            result = runner.invoke(task_app, ["deps"])
        assert result.exit_code == 1
        assert "Provide at least 2 issue numbers." in result.output

    def test_deps_check_mode_valid_graph_exits_zero(self) -> None:
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Issue 1", body="x", state=TaskState.OPEN),
            Task(id="2", title="Issue 2", body="y", state=TaskState.OPEN),
        ]
        with (
            patch("wade.config.loader.load_config", return_value=ProjectConfig()),
            patch("wade.providers.registry.get_provider", return_value=provider),
            patch(
                "wade.models.task.parse_dependency_refs",
                side_effect=[
                    {"depends_on": ["2"], "blocks": []},
                    {"depends_on": [], "blocks": ["1"]},
                ],
            ),
        ):
            result = runner.invoke(task_app, ["deps", "1", "2", "--check"])

        assert result.exit_code == 0
        assert "All dependencies are valid." in result.output
        assert provider.read_task.call_count == 2

    def test_deps_check_mode_invalid_ref_exits_one(self) -> None:
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Issue 1", body="x", state=TaskState.OPEN),
            Task(id="2", title="Issue 2", body="y", state=TaskState.OPEN),
        ]
        with (
            patch("wade.config.loader.load_config", return_value=ProjectConfig()),
            patch("wade.providers.registry.get_provider", return_value=provider),
            patch(
                "wade.models.task.parse_dependency_refs",
                side_effect=[
                    {"depends_on": ["99"], "blocks": []},
                    {"depends_on": [], "blocks": []},
                ],
            ),
        ):
            result = runner.invoke(task_app, ["deps", "1", "2", "--check"])

        assert result.exit_code == 1
        assert "not in analyzed set" in result.output
