"""Integration tests for the knowledge CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from wade.cli.main import app

runner = CliRunner()


def _write_knowledge_config(
    project_root: Path, *, enabled: bool = True, path: str = "KNOWLEDGE.md"
) -> None:
    config_path = project_root / ".wade.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["knowledge"] = {"enabled": enabled, "path": path}
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


class TestKnowledgeAdd:
    def test_add_appends_knowledge_entry(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_knowledge_config(tmp_wade_project, path="docs/KNOWLEDGE.md")

        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(
            app,
            ["knowledge", "add", "--session", "plan", "--issue", "7"],
            input="Prefer task labels over body metadata.\n",
        )

        assert result.exit_code == 0
        knowledge_path = tmp_wade_project / "docs" / "KNOWLEDGE.md"
        assert knowledge_path.exists()
        text = knowledge_path.read_text(encoding="utf-8")
        assert "| plan | Issue #7" in text
        assert "Prefer task labels over body metadata." in text

    def test_add_invalid_path_exits_cleanly(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_knowledge_config(tmp_wade_project, path="../escape.md")

        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(
            app,
            ["knowledge", "add", "--session", "implementation", "--issue", "9"],
            input="This should fail cleanly.\n",
        )

        assert result.exit_code == 1
        assert "must be inside project root" in result.output
        assert not (tmp_wade_project.parent / "escape.md").exists()

    def test_add_rejects_invalid_session_type(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_knowledge_config(tmp_wade_project)

        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(
            app,
            ["knowledge", "add", "--session", "review"],
            input="This should not be accepted.\n",
        )

        assert result.exit_code == 1
        assert "Invalid session type" in result.output
        assert not (tmp_wade_project / "KNOWLEDGE.md").exists()

    def test_add_requires_knowledge_enabled(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_knowledge_config(tmp_wade_project, enabled=False)

        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(
            app,
            ["knowledge", "add", "--session", "plan"],
            input="This should be blocked until knowledge is enabled.\n",
        )

        assert result.exit_code == 1
        assert "Knowledge capture is not enabled" in result.output
        assert not (tmp_wade_project / "KNOWLEDGE.md").exists()


class TestKnowledgeGet:
    def test_get_prints_existing_knowledge_file(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_knowledge_config(tmp_wade_project, path="docs/KNOWLEDGE.md")
        knowledge_path = tmp_wade_project / "docs" / "KNOWLEDGE.md"
        knowledge_path.parent.mkdir(parents=True, exist_ok=True)
        knowledge_path.write_text("# Project Knowledge\n\nPrefer labels.\n", encoding="utf-8")

        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(app, ["knowledge", "get"])

        assert result.exit_code == 0
        assert "# Project Knowledge" in result.output
        assert "Prefer labels." in result.output

    def test_get_missing_file_exits_0_with_notice(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_knowledge_config(tmp_wade_project)

        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(app, ["knowledge", "get"])

        assert result.exit_code == 0
        assert "No knowledge file found." in result.output

    def test_get_requires_knowledge_enabled(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_knowledge_config(tmp_wade_project, enabled=False)

        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(app, ["knowledge", "get"])

        assert result.exit_code == 1
        assert "Knowledge capture is not enabled" in result.output

    def test_get_invalid_path_exits_cleanly(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_knowledge_config(tmp_wade_project, path="../escape.md")

        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(app, ["knowledge", "get"])

        assert result.exit_code == 1
        assert "must be inside project root" in result.output


class TestKnowledgeRate:
    def test_rate_updates_sidecar_file(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_knowledge_config(tmp_wade_project)
        (tmp_wade_project / "KNOWLEDGE.md").write_text(
            (
                "# Project Knowledge\n\n---\n\n## a1b2c3d4 | 2026-03-24 | plan\n\n"
                "Prefer labels.\n\n---\n"
            ),
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(app, ["knowledge", "rate", "a1b2c3d4", "up"])

        assert result.exit_code == 0
        ratings = yaml.safe_load((tmp_wade_project / "KNOWLEDGE.ratings.yml").read_text())
        assert ratings["a1b2c3d4"]["up"] == 1

    def test_rate_invalid_path_exits_cleanly(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_knowledge_config(tmp_wade_project, path="../escape.md")

        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(app, ["knowledge", "rate", "a1b2c3d4", "up"])

        assert result.exit_code == 1
        assert "must be inside project root" in result.output


class TestKnowledgeTagWorkflow:
    """Integration test for the full tag workflow."""

    def test_add_with_tags_then_tag_crud(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_knowledge_config(tmp_wade_project)
        monkeypatch.chdir(tmp_wade_project)

        # Add entry with tags
        result = runner.invoke(
            app,
            ["knowledge", "add", "--session", "plan", "--tag", "git", "--tag", "worktree"],
            input="Worktree safety matters.\n",
        )
        assert result.exit_code == 0
        # Extract entry ID from output (format: "Knowledge entry XXXXXXXX added to ...")
        entry_id = result.output.split("entry ")[1].split(" ")[0]

        # Verify tags in file
        text = (tmp_wade_project / "KNOWLEDGE.md").read_text(encoding="utf-8")
        assert "tags: git, worktree" in text

        # Add another tag
        result = runner.invoke(app, ["knowledge", "tag", "add", entry_id, "safety"])
        assert result.exit_code == 0

        # List tags for entry
        result = runner.invoke(app, ["knowledge", "tag", "list", entry_id])
        assert result.exit_code == 0
        assert "git" in result.output
        assert "worktree" in result.output
        assert "safety" in result.output

        # Remove a tag
        result = runner.invoke(app, ["knowledge", "tag", "remove", entry_id, "git"])
        assert result.exit_code == 0

        # Verify removal
        result = runner.invoke(app, ["knowledge", "tag", "list", entry_id])
        assert "git" not in result.output
        assert "worktree" in result.output
        assert "safety" in result.output

        # List all tags
        result = runner.invoke(app, ["knowledge", "tag", "list"])
        assert result.exit_code == 0
        assert "safety" in result.output
        assert "worktree" in result.output


class TestKnowledgeSearchWorkflow:
    """Integration test for search + tag filtering."""

    def test_search_and_tag_filter(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_knowledge_config(tmp_wade_project)
        monkeypatch.chdir(tmp_wade_project)

        # Add entries with different tags and content
        runner.invoke(
            app,
            ["knowledge", "add", "--session", "plan", "--tag", "git"],
            input="Git worktree is useful for isolation.\n",
        )
        runner.invoke(
            app,
            ["knowledge", "add", "--session", "plan", "--tag", "testing"],
            input="Always write tests for new features.\n",
        )
        runner.invoke(
            app,
            ["knowledge", "add", "--session", "plan"],
            input="Docker is unrelated.\n",
        )

        # Search by text
        result = runner.invoke(app, ["knowledge", "get", "--search", "worktree", "--no-filter"])
        assert result.exit_code == 0
        assert "worktree" in result.output
        assert "Docker" not in result.output

        # Filter by tag
        result = runner.invoke(app, ["knowledge", "get", "--tag", "testing", "--no-filter"])
        assert result.exit_code == 0
        assert "tests" in result.output
        assert "Docker" not in result.output

        # Combined search + tag (OR semantics)
        result = runner.invoke(
            app,
            ["knowledge", "get", "--search", "worktree", "--tag", "testing", "--no-filter"],
        )
        assert result.exit_code == 0
        assert "worktree" in result.output
        assert "tests" in result.output
        assert "Docker" not in result.output
