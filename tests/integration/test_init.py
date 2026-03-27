"""Integration tests for init, update, and deinit commands."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from wade.cli.main import app

runner = CliRunner()


class TestInit:
    def test_init_creates_expected_managed_files(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init should create the config and managed artifacts with current defaults."""
        monkeypatch.chdir(tmp_git_repo)
        (tmp_git_repo / "scripts").mkdir()
        (tmp_git_repo / "scripts" / "check.sh").write_text("#!/bin/sh\necho ok\n")

        result = runner.invoke(app, ["init", "--yes", "--ai", "claude"])
        assert result.exit_code == 0

        config_path = tmp_git_repo / ".wade.yml"
        assert config_path.exists(), "init should create .wade.yml"
        config = yaml.safe_load(config_path.read_text())
        assert config["version"] == 2
        assert config["ai"]["default_tool"] == "claude"
        assert config["models"]["claude"]["medium"] == "claude-sonnet-4.6"
        assert config["models"]["claude"]["medium"] != config["models"]["claude"]["easy"]
        assert "permissions" not in config

        assert (tmp_git_repo / ".wade-managed").is_file()
        assert (tmp_git_repo / "AGENTS.md").is_file()
        assert (tmp_git_repo / "CLAUDE.md").exists()

        gitignore = (tmp_git_repo / ".gitignore").read_text()
        assert "# wade:start" in gitignore
        assert "# wade:end" in gitignore


class TestDeinit:
    def test_deinit_removes_config(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """deinit should remove .wade.yml and managed files."""
        monkeypatch.chdir(tmp_wade_project)

        # Create some managed files
        skills_dir = tmp_wade_project / ".claude" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        result = runner.invoke(
            app,
            ["deinit", "--force"],  # Non-TTY: confirm() returns default=False, so use --force
        )
        # Deinit should remove .wade.yml when forced
        assert result.exit_code == 0
        config_path = tmp_wade_project / ".wade.yml"
        assert not config_path.exists(), ".wade.yml should be removed after deinit"
