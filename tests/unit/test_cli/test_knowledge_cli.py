"""Unit tests for knowledge CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from wade.cli.main import app
from wade.models.config import KnowledgeConfig, ProjectConfig

runner = CliRunner()


class TestKnowledgeGetCommand:
    def test_prints_content_when_file_exists(self, tmp_path: Path) -> None:
        content = "# Knowledge\n\nSome content.\n"
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get"])
        assert result.exit_code == 0
        assert content in result.output

    def test_exits_0_when_file_missing(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get"])
        assert result.exit_code == 0

    def test_exits_1_when_disabled(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=False, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get"])
        assert result.exit_code == 1

    def test_help_shows_get_command(self) -> None:
        result = runner.invoke(app, ["knowledge", "--help"])
        assert result.exit_code == 0
        assert "get" in result.output
