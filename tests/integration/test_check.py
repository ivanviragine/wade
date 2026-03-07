"""Integration tests for check and check-config commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wade.cli.main import app

runner = CliRunner()


class TestCheck:
    """Test check via implementation-session sub-app (was top-level ``check``)."""

    def test_check_in_main_checkout(
        self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """implementation-session check in a main checkout should exit 2."""
        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(app, ["implementation-session", "check"], catch_exceptions=False)
        assert result.exit_code == 2
        assert "IN_MAIN_CHECKOUT" in result.output

    def test_check_not_in_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """implementation-session check outside git should exit 1."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["implementation-session", "check"])
        assert result.exit_code == 1
        assert "NOT_IN_GIT_REPO" in result.output


class TestCheckConfig:
    def test_valid_config(self, tmp_wade_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """check-config with a valid .wade.yml should succeed."""
        monkeypatch.chdir(tmp_wade_project)
        result = runner.invoke(app, ["check-config"])
        assert result.exit_code == 0

    def test_missing_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """check-config without .wade.yml should exit 1 and report CONFIG_NOT_FOUND."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["check-config"])
        assert result.exit_code == 1
        assert "CONFIG_NOT_FOUND" in result.output
