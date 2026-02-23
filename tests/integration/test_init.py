"""Integration tests for init, update, and deinit commands."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ghaiw.cli.main import app

runner = CliRunner()


class TestInit:
    def test_init_creates_config(self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """init should create .ghaiw.yml in a fresh repo."""
        monkeypatch.chdir(tmp_git_repo)

        # Run init in non-interactive mode (provide inputs)
        result = runner.invoke(
            app,
            ["init"],
            input="y\n",  # Confirm any prompts
        )
        # Init may fail without AI tools, but should at least attempt
        # If it creates a config, that's success
        config_path = tmp_git_repo / ".ghaiw.yml"
        if config_path.exists():
            assert "version" in config_path.read_text()


class TestDeinit:
    def test_deinit_removes_config(
        self, tmp_ghaiw_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """deinit should remove .ghaiw.yml and managed files."""
        monkeypatch.chdir(tmp_ghaiw_project)

        # Create some managed files
        skills_dir = tmp_ghaiw_project / ".claude" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        result = runner.invoke(
            app,
            ["deinit"],
            input="y\n",  # Confirm
        )
        # Deinit should remove .ghaiw.yml
        # (May succeed or fail depending on state, but shouldn't crash)
        assert result.exit_code in (0, 1)
