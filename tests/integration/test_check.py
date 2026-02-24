"""Integration tests for check and check-config commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ghaiw.cli.main import app

runner = CliRunner()


class TestCheck:
    def test_check_in_main_checkout(
        self, tmp_ghaiw_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """check in a main checkout should exit 2 and report IN_MAIN_CHECKOUT."""
        monkeypatch.chdir(tmp_ghaiw_project)
        result = runner.invoke(app, ["check"], catch_exceptions=False)
        assert result.exit_code == 2
        assert "IN_MAIN_CHECKOUT" in result.output

    def test_check_not_in_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """check outside git should exit 1 and report NOT_IN_GIT_REPO."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["check"])
        assert result.exit_code == 1
        assert "NOT_IN_GIT_REPO" in result.output


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
