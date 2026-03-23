"""Integration tests for the knowledge CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from wade.cli.main import app

runner = CliRunner()


class TestKnowledgeAdd:
    def test_add_appends_knowledge_entry(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = tmp_wade_project / ".wade.yml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config["knowledge"] = {"enabled": True, "path": "docs/KNOWLEDGE.md"}
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

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
        config_path = tmp_wade_project / ".wade.yml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config["knowledge"] = {"enabled": True, "path": "../escape.md"}
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

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
        config_path = tmp_wade_project / ".wade.yml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config["knowledge"] = {"enabled": True, "path": "KNOWLEDGE.md"}
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

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
        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(
            app,
            ["knowledge", "add", "--session", "plan"],
            input="This should be blocked until knowledge is enabled.\n",
        )

        assert result.exit_code == 1
        assert "Knowledge capture is not enabled" in result.output
        assert not (tmp_wade_project / "KNOWLEDGE.md").exists()
