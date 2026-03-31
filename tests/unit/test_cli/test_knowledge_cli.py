"""Unit tests for knowledge CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from wade.cli.main import app
from wade.models.config import KnowledgeConfig, ProjectConfig
from wade.services.knowledge_service import (
    KNOWLEDGE_TEMPLATE,
    resolve_ratings_path,
)

runner = CliRunner()


class TestKnowledgeGetCommand:
    def test_prints_content_when_file_exists(self, tmp_path: Path) -> None:
        content = "# Knowledge\n\n---\n\n## a1b2c3d4 | 2026-03-24 | plan\n\nSome content.\n\n---\n"
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get"])
        assert result.exit_code == 0
        assert "Some content." in result.output

    def test_exits_0_when_file_missing(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get"])
        assert result.exit_code == 0
        assert "No knowledge file found." in result.output

    def test_exits_1_when_disabled(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=False, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get"])
        assert result.exit_code == 1

    def test_exits_1_when_path_is_directory(self, tmp_path: Path) -> None:
        (tmp_path / "somedir").mkdir()
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="somedir"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get"])
        assert result.exit_code == 1
        assert "directory" in result.output.lower()

    def test_exits_1_on_os_error(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch(
                "wade.services.knowledge_service.get_annotated_knowledge",
                side_effect=OSError("Permission denied"),
            ),
        ):
            result = runner.invoke(app, ["knowledge", "get"])
        assert result.exit_code == 1
        assert "Permission denied" in result.output

    def test_help_shows_get_command(self) -> None:
        result = runner.invoke(app, ["knowledge", "--help"])
        assert result.exit_code == 0
        assert "get" in result.output

    def test_min_score_filters_output(self, tmp_path: Path) -> None:
        content = (
            KNOWLEDGE_TEMPLATE
            + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nGood.\n\n---\n"
            + "\n## f5e6d7c8 | 2026-03-20 | implementation\n\nBad.\n\n---\n"
        )
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        # Rate a1b2c3d4 up, f5e6d7c8 down
        ratings_path = resolve_ratings_path(tmp_path / "KNOWLEDGE.md")
        ratings_path.write_text(
            yaml.safe_dump({"a1b2c3d4": {"up": 2, "down": 0}, "f5e6d7c8": {"up": 0, "down": 2}}),
            encoding="utf-8",
        )
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get", "--min-score", "0"])
        assert result.exit_code == 0
        assert "Good." in result.output
        assert "Bad." not in result.output

    def test_annotates_headings_with_scores(self, tmp_path: Path) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nContent.\n\n---\n"
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        ratings_path = resolve_ratings_path(tmp_path / "KNOWLEDGE.md")
        ratings_path.write_text(
            yaml.safe_dump({"a1b2c3d4": {"up": 3, "down": 1}}),
            encoding="utf-8",
        )
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get"])
        assert result.exit_code == 0
        assert "[+3/-1]" in result.output


class TestKnowledgeRateCommand:
    def _setup_knowledge(self, tmp_path: Path) -> ProjectConfig:
        content = KNOWLEDGE_TEMPLATE + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nContent.\n\n---\n"
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        return ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )

    def test_rate_up(self, tmp_path: Path) -> None:
        config = self._setup_knowledge(tmp_path)
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "rate", "a1b2c3d4", "up"])
        assert result.exit_code == 0
        assert "+1" in result.output

    def test_rate_down(self, tmp_path: Path) -> None:
        config = self._setup_knowledge(tmp_path)
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "rate", "a1b2c3d4", "down"])
        assert result.exit_code == 0
        assert "-1" in result.output

    def test_exits_1_for_missing_id(self, tmp_path: Path) -> None:
        config = self._setup_knowledge(tmp_path)
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "rate", "nonexist", "up"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_exits_1_for_invalid_direction(self, tmp_path: Path) -> None:
        config = self._setup_knowledge(tmp_path)
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "rate", "a1b2c3d4", "sideways"])
        assert result.exit_code == 1
        assert "up" in result.output and "down" in result.output

    def test_exits_1_when_disabled(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=False, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "rate", "a1b2c3d4", "up"])
        assert result.exit_code == 1

    def test_exits_1_on_invalid_path(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="../escape.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "rate", "a1b2c3d4", "up"])
        assert result.exit_code == 1
        assert "must be inside project root" in result.output

    def test_creates_ratings_file(self, tmp_path: Path) -> None:
        config = self._setup_knowledge(tmp_path)
        ratings_path = resolve_ratings_path(tmp_path / "KNOWLEDGE.md")
        assert not ratings_path.exists()
        with patch("wade.config.loader.load_config", return_value=config):
            runner.invoke(app, ["knowledge", "rate", "a1b2c3d4", "up"])
        assert ratings_path.exists()

    def test_rate_descriptive_id_with_hyphens(self, tmp_path: Path) -> None:
        content = (
            KNOWLEDGE_TEMPLATE
            + "\n## config-sync-tool | 2026-03-24 | implementation\n\n"
            + "Descriptive ID entry.\n\n---\n"
        )
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "rate", "config-sync-tool", "up"])
        assert result.exit_code == 0
        assert "+1" in result.output

    def test_rate_descriptive_id_with_underscores(self, tmp_path: Path) -> None:
        content = (
            KNOWLEDGE_TEMPLATE
            + "\n## my_entry_name | 2026-03-24 | plan\n\nCustom underscore ID.\n\n---\n"
        )
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "rate", "my_entry_name", "down"])
        assert result.exit_code == 0
        assert "-1" in result.output


class TestKnowledgeAddSupersedes:
    def test_supersedes_flag(self, tmp_path: Path) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nOld.\n\n---\n"
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(
                app,
                ["knowledge", "add", "--session", "plan", "--supersedes", "a1b2c3d4"],
                input="Corrected info\n",
            )
        assert result.exit_code == 0
        assert "supersedes a1b2c3d4" in result.output
        # Check sidecar file has the link
        ratings_path = resolve_ratings_path(tmp_path / "KNOWLEDGE.md")
        ratings = yaml.safe_load(ratings_path.read_text(encoding="utf-8"))
        assert ratings["a1b2c3d4"]["superseded_by"] is not None

    def test_supersedes_missing_id_exits_1(self, tmp_path: Path) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nOld.\n\n---\n"
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(
                app,
                ["knowledge", "add", "--session", "plan", "--supersedes", "nonexist"],
                input="New info\n",
            )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_add_returns_entry_id_in_output(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(
                app,
                ["knowledge", "add", "--session", "implementation", "--issue", "42"],
                input="Some learning\n",
            )
        assert result.exit_code == 0
        # Output should contain an 8-char hex ID
        import re

        assert re.search(r"[0-9a-f]{8}", result.output)
