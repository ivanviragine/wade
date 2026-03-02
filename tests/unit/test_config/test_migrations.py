"""Tests for config migration pipeline."""

from __future__ import annotations

from pathlib import Path

import yaml

from ghaiw.config.migrations import ensure_version, run_all_migrations

# ---------------------------------------------------------------------------
# ensure_version
# ---------------------------------------------------------------------------


class TestEnsureVersion:
    def test_adds_version_when_missing(self) -> None:
        raw: dict = {"project": {}}
        result = ensure_version(raw)
        assert result is True
        assert raw["version"] == 2

    def test_noop_when_present(self) -> None:
        raw: dict = {"version": 2}
        result = ensure_version(raw)
        assert result is False
        assert raw["version"] == 2

    def test_preserves_existing_version_value(self) -> None:
        raw: dict = {"version": 1}
        result = ensure_version(raw)
        assert result is False
        assert raw["version"] == 1

    def test_idempotent_second_call(self) -> None:
        raw: dict = {}
        ensure_version(raw)
        result = ensure_version(raw)
        assert result is False


# ---------------------------------------------------------------------------
# run_all_migrations
# ---------------------------------------------------------------------------


class TestRunAllMigrations:
    def test_adds_version_to_versionless_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text(
            yaml.dump({"ai": {"default_tool": "claude"}}, default_flow_style=False)
        )
        result = run_all_migrations(config_path)
        assert result is True
        migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert migrated["version"] == 2

    def test_already_versioned_returns_false(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text(
            yaml.dump({"version": 2, "ai": {"default_tool": "claude"}}, default_flow_style=False)
        )
        result = run_all_migrations(config_path)
        assert result is False

    def test_invalid_yaml_returns_false(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text("{ invalid yaml: [")
        result = run_all_migrations(config_path)
        assert result is False

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        config_path = tmp_path / "nonexistent.yml"
        result = run_all_migrations(config_path)
        assert result is False

    def test_empty_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text("")
        result = run_all_migrations(config_path)
        assert result is True
        migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert migrated["version"] == 2

    def test_non_dict_yaml_rejected(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text("just a string\n")
        result = run_all_migrations(config_path)
        assert result is False
        # File should not be modified
        assert config_path.read_text(encoding="utf-8") == "just a string\n"

    def test_file_not_modified_when_no_changes(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        content = yaml.dump(
            {"version": 2, "ai": {"default_tool": "claude"}}, default_flow_style=False
        )
        config_path.write_text(content)
        mtime_before = config_path.stat().st_mtime_ns

        run_all_migrations(config_path)
        mtime_after = config_path.stat().st_mtime_ns

        assert mtime_before == mtime_after
