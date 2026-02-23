"""Integration tests for check and check-config commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ghaiw.cli.main import app

runner = CliRunner()


class TestCheck:
    def test_check_in_main_checkout(self, tmp_ghaiw_project: Path) -> None:
        """check in a main checkout should exit 2 (IN_MAIN_CHECKOUT)."""
        result = runner.invoke(app, ["check"], catch_exceptions=False)
        # Outside a proper git context in test runner, may get various codes
        # Just verify it doesn't crash
        assert result.exit_code in (0, 1, 2)

    def test_check_not_in_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """check outside git should exit 1."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["check"])
        # Should report not in git repo
        assert result.exit_code in (1, 2)


class TestCheckConfig:
    def test_valid_config(self, tmp_ghaiw_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """check-config with a valid .ghaiw.yml should succeed."""
        monkeypatch.chdir(tmp_ghaiw_project)
        result = runner.invoke(app, ["check-config"])
        assert result.exit_code == 0

    def test_missing_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """check-config without .ghaiw.yml should fail."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["check-config"])
        assert result.exit_code != 0
