"""Unit tests for knowledge CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from wade.cli.main import app
from wade.models.config import KnowledgeConfig, ProjectConfig
from wade.services.knowledge_service import (
    KNOWLEDGE_TEMPLATE,
    resolve_ratings_path,
)

runner = CliRunner()


def _write_ratings(ratings_path: Path, counters: dict[str, dict[str, int]]) -> None:
    """Write a JSONL vote log that folds to the given per-entry counter dict.

    One seed record per entry — the append-only equivalent of the old counter YAML.
    """
    import json

    lines = [
        json.dumps(
            {
                "id": entry_id,
                "seed": True,
                "up": counts.get("up", 0),
                "down": counts.get("down", 0),
                "stale": counts.get("stale", 0),
                "superseded_by": counts.get("superseded_by"),
            },
            sort_keys=True,
        )
        for entry_id, counts in counters.items()
    ]
    ratings_path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


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
        _write_ratings(
            ratings_path,
            {"a1b2c3d4": {"up": 2, "down": 0}, "f5e6d7c8": {"up": 0, "down": 2}},
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
        _write_ratings(ratings_path, {"a1b2c3d4": {"up": 3, "down": 1}})
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get"])
        assert result.exit_code == 0
        assert "[+3/-1]" in result.output

    def test_search_on_empty_file_prints_no_results(self, tmp_path: Path) -> None:
        # Create knowledge file with only template header
        (tmp_path / "KNOWLEDGE.md").write_text(KNOWLEDGE_TEMPLATE, encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get", "--search", "foo"])
        assert result.exit_code == 0
        assert "No entries matched your search." in result.output

    def test_tag_filter_on_empty_file_prints_no_results(self, tmp_path: Path) -> None:
        # Create knowledge file with only template header
        (tmp_path / "KNOWLEDGE.md").write_text(KNOWLEDGE_TEMPLATE, encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get", "--tag", "foo"])
        assert result.exit_code == 0
        assert "No entries matched your search." in result.output

    def test_search_with_no_matches_prints_no_results(self, tmp_path: Path) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nDocker stuff.\n\n---\n"
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get", "--search", "nonexistent"])
        assert result.exit_code == 0
        assert "No entries matched your search." in result.output
        assert "Docker stuff." not in result.output

    def test_tag_filter_with_no_matches_prints_no_results(self, tmp_path: Path) -> None:
        content = (
            KNOWLEDGE_TEMPLATE
            + "\n## a1b2c3d4 | 2026-03-24 | plan | tags: git\n\nGit stuff.\n\n---\n"
        )
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get", "--tag", "docker"])
        assert result.exit_code == 0
        assert "No entries matched your search." in result.output
        assert "Git stuff." not in result.output

    def test_search_finds_plain_entries(self, tmp_path: Path) -> None:
        content = (
            KNOWLEDGE_TEMPLATE
            + "\n## Git Worktree Tips\n\nAlways isolate work in worktrees.\n\n---\n\n"
            + "## Docker Tips\n\nUnrelated.\n\n---\n"
        )
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get", "--search", "worktree", "--no-filter"])
        assert result.exit_code == 0
        assert "Worktree" in result.output
        assert "Unrelated." not in result.output

    def test_plain_entry_not_score_annotated(self, tmp_path: Path) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## My Plain Entry\n\nPlain content.\n\n---\n"
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get"])
        assert result.exit_code == 0
        assert "Plain content." in result.output
        assert "[+" not in result.output

    def test_stale_filter_hides_entry_by_default(self, tmp_path: Path) -> None:
        content = (
            KNOWLEDGE_TEMPLATE
            + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nStale content.\n\n---\n"
            + "\n## f5e6d7c8 | 2026-03-20 | implementation\n\nFresh content.\n\n---\n"
        )
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        ratings_path = resolve_ratings_path(tmp_path / "KNOWLEDGE.md")
        _write_ratings(ratings_path, {"a1b2c3d4": {"up": 0, "down": 0, "stale": 2}})
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get"])
        assert result.exit_code == 0
        assert "Stale content." not in result.output
        assert "Fresh content." in result.output

    def test_no_filter_bypasses_stale_filter(self, tmp_path: Path) -> None:
        content = (
            KNOWLEDGE_TEMPLATE + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nStale content.\n\n---\n"
        )
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        ratings_path = resolve_ratings_path(tmp_path / "KNOWLEDGE.md")
        _write_ratings(ratings_path, {"a1b2c3d4": {"up": 0, "down": 0, "stale": 5}})
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get", "--no-filter"])
        assert result.exit_code == 0
        assert "Stale content." in result.output

    def test_stale_annotation_shown_in_heading(self, tmp_path: Path) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nContent.\n\n---\n"
        (tmp_path / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        ratings_path = resolve_ratings_path(tmp_path / "KNOWLEDGE.md")
        _write_ratings(ratings_path, {"a1b2c3d4": {"up": 2, "down": 0, "stale": 1}})
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "get", "--no-filter"])
        assert result.exit_code == 0
        assert "[+2/-0/stale:1]" in result.output


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

    def test_rate_stale(self, tmp_path: Path) -> None:
        config = self._setup_knowledge(tmp_path)
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "rate", "a1b2c3d4", "stale"])
        assert result.exit_code == 0
        assert "stale" in result.output.lower()

    def test_exits_1_for_invalid_direction_rejects_non_stale(self, tmp_path: Path) -> None:
        config = self._setup_knowledge(tmp_path)
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "rate", "a1b2c3d4", "sideways"])
        assert result.exit_code == 1
        assert "stale" in result.output


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
        # The vote log folds to a supersede link for the old id.
        from wade.services.knowledge_service import read_ratings

        ratings_path = resolve_ratings_path(tmp_path / "KNOWLEDGE.md")
        ratings = read_ratings(ratings_path)
        assert ratings["a1b2c3d4"].superseded_by is not None

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


class TestKnowledgeEnableCommand:
    def test_enables_knowledge_and_creates_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\n", encoding="utf-8")

        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=False, path="KNOWLEDGE.md"),
            config_path=str(config_path),
        )
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.config.loader.find_config_file", return_value=config_path),
        ):
            result = runner.invoke(app, ["knowledge", "enable"])

        assert result.exit_code == 0
        assert "Knowledge capture enabled" in result.output

        # Verify config was updated
        updated_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert updated_config["knowledge"]["enabled"] is True

    def test_enables_knowledge_with_custom_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\n", encoding="utf-8")

        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=False, path="KNOWLEDGE.md"),
            config_path=str(config_path),
        )
        # The CLI handler reads Path.cwd() to derive project_root, so chdir
        # into tmp_path to keep the created knowledge file out of the real repo.
        monkeypatch.chdir(tmp_path)
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.config.loader.find_config_file", return_value=config_path),
        ):
            result = runner.invoke(app, ["knowledge", "enable", "--path", "docs/LEARNINGS.md"])

        assert result.exit_code == 0
        assert "Knowledge capture enabled" in result.output
        assert "docs/LEARNINGS.md" in result.output

        updated_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert updated_config["knowledge"]["path"] == "docs/LEARNINGS.md"

    def test_enable_fails_when_no_config(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=False, path="KNOWLEDGE.md"),
        )
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.config.loader.find_config_file", return_value=None),
        ):
            result = runner.invoke(app, ["knowledge", "enable"])

        assert result.exit_code == 1
        assert ".wade.yml not found" in result.output

    def test_enable_rejects_absolute_path(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\n", encoding="utf-8")

        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=False, path="KNOWLEDGE.md"),
            config_path=str(config_path),
        )
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.config.loader.find_config_file", return_value=config_path),
        ):
            result = runner.invoke(app, ["knowledge", "enable", "--path", "/etc/passwd"])

        assert result.exit_code == 1
        assert "must be inside project root" in result.output


class TestKnowledgeDisableCommand:
    def test_disables_knowledge(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nknowledge:\n  enabled: true\n  path: KNOWLEDGE.md\n",
            encoding="utf-8",
        )
        knowledge_file = tmp_path / "KNOWLEDGE.md"
        knowledge_file.write_text("# Knowledge\n", encoding="utf-8")

        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
            config_path=str(config_path),
        )
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.config.loader.find_config_file", return_value=config_path),
        ):
            result = runner.invoke(app, ["knowledge", "disable"])

        assert result.exit_code == 0
        assert "Knowledge capture disabled" in result.output

        # Verify config was updated
        updated_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert updated_config["knowledge"]["enabled"] is False

        # Verify knowledge file still exists
        assert knowledge_file.exists()

    def test_disable_fails_when_no_config(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.config.loader.find_config_file", return_value=None),
        ):
            result = runner.invoke(app, ["knowledge", "disable"])

        assert result.exit_code == 1
        assert ".wade.yml not found" in result.output


class TestThrowawaySessionGate:
    """`add` / `tag` are refused in a detached-HEAD (plan / task deps) worktree."""

    def _config(self, tmp_path: Path) -> ProjectConfig:
        (tmp_path / "KNOWLEDGE.md").write_text(
            KNOWLEDGE_TEMPLATE + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nOld.\n\n---\n",
            encoding="utf-8",
        )
        return ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )

    def test_add_blocked_in_plan_session_with_plan_hint(self, tmp_path: Path) -> None:
        config = self._config(tmp_path)
        (tmp_path / ".wade" / "plans").mkdir(parents=True)  # plan worktree signal
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.git.repo.get_git_dir", return_value=".git"),
            patch("wade.git.repo.is_head_attached", return_value=False),
        ):
            result = runner.invoke(
                app,
                ["knowledge", "add", "--session", "plan"],
                input="A learning\n",
            )
        assert result.exit_code == 1
        assert "discarded at session end" in result.output
        assert "plan file" in result.output  # plan-file hint present

    def test_add_blocked_in_deps_session_without_plan_hint(self, tmp_path: Path) -> None:
        # A `task deps` worktree is also detached but has NO .wade/plans dir — the
        # base message must stand alone with no false "put it in the plan file".
        config = self._config(tmp_path)
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.git.repo.get_git_dir", return_value=".git"),
            patch("wade.git.repo.is_head_attached", return_value=False),
        ):
            result = runner.invoke(
                app,
                ["knowledge", "add", "--session", "implementation"],
                input="A learning\n",
            )
        assert result.exit_code == 1
        assert "discarded at session end" in result.output
        assert "plan file" not in result.output  # no plan-file hint for a deps session

    def test_tag_add_blocked_in_throwaway_session(self, tmp_path: Path) -> None:
        config = self._config(tmp_path)
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.git.repo.get_git_dir", return_value=".git"),
            patch("wade.git.repo.is_head_attached", return_value=False),
        ):
            result = runner.invoke(app, ["knowledge", "tag", "add", "a1b2c3d4", "git"])
        assert result.exit_code == 1
        assert "discarded at session end" in result.output

    def test_add_allowed_when_attached(self, tmp_path: Path) -> None:
        config = self._config(tmp_path)
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.git.repo.get_git_dir", return_value=".git"),
            patch("wade.git.repo.is_head_attached", return_value=True),
        ):
            result = runner.invoke(
                app,
                ["knowledge", "add", "--session", "implementation"],
                input="A learning\n",
            )
        assert result.exit_code == 0

    def test_add_allowed_outside_git_repo(self, tmp_path: Path) -> None:
        # A non-repo path (tests, odd setups) must NOT be treated as a throwaway session.
        config = self._config(tmp_path)
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.git.repo.get_git_dir", return_value=None),
        ):
            result = runner.invoke(
                app,
                ["knowledge", "add", "--session", "implementation"],
                input="A learning\n",
            )
        assert result.exit_code == 0

    def test_rate_stages_vote_instead_of_writing_canonical_sidecar(self, tmp_path: Path) -> None:
        config = self._config(tmp_path)
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.git.repo.get_git_dir", return_value=".git"),
            patch("wade.git.repo.is_head_attached", return_value=False),
            patch(
                "wade.services.knowledge_service.resolve_canonical_knowledge_path",
                side_effect=AssertionError("detached rating must not access the main checkout"),
            ),
        ):
            result = runner.invoke(app, ["knowledge", "rate", "a1b2c3d4", "up"])

        assert result.exit_code == 0
        assert (tmp_path / ".wade" / "knowledge-ratings-staged.jsonl").is_file()
        assert not (tmp_path / "KNOWLEDGE.ratings.jsonl").exists()


class TestKnowledgeStatusCommand:
    def test_status_reports_pending_legacy_migration(self, tmp_path: Path) -> None:
        (tmp_path / "KNOWLEDGE.ratings.yml").write_text("e: {up: 1}\n", encoding="utf-8")
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "status"])
        assert result.exit_code == 0
        assert "pending migration" in result.output

    def test_status_clean_when_nothing_pending(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        with patch("wade.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["knowledge", "status"])
        assert result.exit_code == 0
        assert "clean" in result.output.lower()

    def test_status_reports_staged_detached_votes(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project_root=str(tmp_path),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        staged = tmp_path / ".wade" / "knowledge-ratings-staged.jsonl"
        staged.parent.mkdir()
        staged.write_text(
            '{"dir": "up", "event_id": "event", "id": "entry", "ts": "now"}\n',
            encoding="utf-8",
        )
        with (
            patch("wade.config.loader.load_config", return_value=config),
            patch("wade.git.repo.get_git_dir", return_value=".git"),
            patch("wade.git.repo.is_head_attached", return_value=False),
        ):
            result = runner.invoke(app, ["knowledge", "status"])

        assert result.exit_code == 0
        assert "1 detached-session rating vote" in result.output
