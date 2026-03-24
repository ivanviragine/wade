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
