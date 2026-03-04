"""Integration tests for init, update, and deinit commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wade.cli.main import app

runner = CliRunner()


class TestInit:
    def test_init_creates_config(self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """init should create .wade.yml in a fresh repo."""
        monkeypatch.chdir(tmp_git_repo)

        # Run init in non-interactive mode (provide inputs)
        runner.invoke(
            app,
            ["init"],
            input="y\n",  # Confirm any prompts
        )
        config_path = tmp_git_repo / ".wade.yml"
        assert config_path.exists(), "init should create .wade.yml"
        assert "version" in config_path.read_text()


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
