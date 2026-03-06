"""Tests for Cursor CLI allowlist management (global + per-project)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wade.config.cursor_allowlist import (
    WADE_ALLOW_PATTERN,
    configure_allowlist,
    is_allowlist_configured,
)


@pytest.fixture(autouse=True)
def _patch_global_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the global config path to a temp directory."""
    fake_config = tmp_path / ".cursor" / "cli-config.json"
    monkeypatch.setattr("wade.config.cursor_allowlist._GLOBAL_CONFIG_PATH", fake_config)


def _global_config_path(tmp_path: Path) -> Path:
    return tmp_path / ".cursor" / "cli-config.json"


class TestConfigureAllowlistGlobal:
    """Tests for configure_allowlist() targeting the global config."""

    def test_creates_config_from_scratch(self, tmp_path: Path) -> None:
        configure_allowlist()

        data = json.loads(_global_config_path(tmp_path).read_text(encoding="utf-8"))
        assert data == {"permissions": {"allow": [WADE_ALLOW_PATTERN]}}

    def test_adds_to_existing_config(self, tmp_path: Path) -> None:
        config_path = _global_config_path(tmp_path)
        config_path.parent.mkdir(parents=True)
        existing = {
            "permissions": {"allow": ["Shell(ls)"], "deny": []},
            "version": 1,
        }
        config_path.write_text(json.dumps(existing) + "\n", encoding="utf-8")

        configure_allowlist()

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]
        assert "Shell(ls)" in data["permissions"]["allow"]
        assert data["version"] == 1

    def test_idempotent_no_duplicate(self, tmp_path: Path) -> None:
        configure_allowlist()
        configure_allowlist()

        data = json.loads(_global_config_path(tmp_path).read_text(encoding="utf-8"))
        assert data["permissions"]["allow"].count(WADE_ALLOW_PATTERN) == 1

    def test_handles_corrupted_json(self, tmp_path: Path) -> None:
        config_path = _global_config_path(tmp_path)
        config_path.parent.mkdir(parents=True)
        config_path.write_text("{broken!!", encoding="utf-8")

        configure_allowlist()

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]

    def test_pattern_value(self) -> None:
        assert WADE_ALLOW_PATTERN == "Shell(wade *)"


class TestConfigureAllowlistPerProject:
    """Tests for configure_allowlist() targeting per-project .cursor/cli.json."""

    def test_creates_per_project_config(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()

        configure_allowlist(project_root)

        config_path = project_root / ".cursor" / "cli.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data == {"permissions": {"allow": [WADE_ALLOW_PATTERN]}}

    def test_per_project_does_not_touch_global(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()

        configure_allowlist(project_root)

        assert not _global_config_path(tmp_path).exists()

    def test_per_project_adds_to_existing(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        config_path = project_root / ".cursor" / "cli.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps({"permissions": {"allow": ["Shell(ls)"]}}),
            encoding="utf-8",
        )

        configure_allowlist(project_root)

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]
        assert "Shell(ls)" in data["permissions"]["allow"]


class TestIsAllowlistConfigured:
    def test_returns_true_when_present_globally(self, tmp_path: Path) -> None:
        config_path = _global_config_path(tmp_path)
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps({"permissions": {"allow": [WADE_ALLOW_PATTERN]}}),
            encoding="utf-8",
        )

        assert is_allowlist_configured() is True

    def test_returns_false_when_file_missing(self) -> None:
        assert is_allowlist_configured() is False

    def test_returns_false_when_pattern_absent(self, tmp_path: Path) -> None:
        config_path = _global_config_path(tmp_path)
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps({"permissions": {"allow": ["Shell(ls)"]}}),
            encoding="utf-8",
        )

        assert is_allowlist_configured() is False

    def test_per_project_returns_true(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        config_path = project_root / ".cursor" / "cli.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps({"permissions": {"allow": [WADE_ALLOW_PATTERN]}}),
            encoding="utf-8",
        )

        assert is_allowlist_configured(project_root) is True

    def test_per_project_returns_false_when_missing(self, tmp_path: Path) -> None:
        project_root = tmp_path / "project"
        project_root.mkdir()

        assert is_allowlist_configured(project_root) is False
