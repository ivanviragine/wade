"""Tests for config migration pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from wade.config.migrations import (
    ensure_version,
    migrate_string_tiers_to_tier_config,
    run_all_migrations,
)

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
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            yaml.dump({"ai": {"default_tool": "claude"}}, default_flow_style=False)
        )
        result = run_all_migrations(config_path)
        assert result is True
        migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert migrated["version"] == 2

    def test_already_versioned_returns_false(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            yaml.dump({"version": 2, "ai": {"default_tool": "claude"}}, default_flow_style=False)
        )
        result = run_all_migrations(config_path)
        assert result is False

    def test_invalid_yaml_returns_false(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("{ invalid yaml: [")
        result = run_all_migrations(config_path)
        assert result is False

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        config_path = tmp_path / "nonexistent.yml"
        result = run_all_migrations(config_path)
        assert result is False

    def test_empty_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("")
        result = run_all_migrations(config_path)
        assert result is True
        migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert migrated["version"] == 2

    def test_non_dict_yaml_rejected(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("just a string\n")
        result = run_all_migrations(config_path)
        assert result is False
        # File should not be modified
        assert config_path.read_text(encoding="utf-8") == "just a string\n"

    def test_file_not_modified_when_no_changes(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        content = yaml.dump(
            {"version": 2, "ai": {"default_tool": "claude"}}, default_flow_style=False
        )
        config_path.write_text(content)
        mtime_before = config_path.stat().st_mtime_ns

        run_all_migrations(config_path)
        mtime_after = config_path.stat().st_mtime_ns

        assert mtime_before == mtime_after


# ---------------------------------------------------------------------------
# migrate_string_tiers_to_tier_config
# ---------------------------------------------------------------------------


class TestMigrateStringTiersToTierConfig:
    def test_converts_string_tier_to_dict_form(self) -> None:
        raw: dict[str, Any] = {
            "version": 2,
            "models": {"claude": {"easy": "haiku", "complex": "sonnet"}},
        }
        result = migrate_string_tiers_to_tier_config(raw)
        assert result is True
        assert raw["models"]["claude"]["easy"] == {"model": "haiku", "effort": None}
        assert raw["models"]["claude"]["complex"] == {"model": "sonnet", "effort": None}

    def test_idempotent_on_already_dict_form(self) -> None:
        raw: dict[str, Any] = {
            "version": 2,
            "models": {"claude": {"easy": {"model": "haiku", "effort": "low"}}},
        }
        result = migrate_string_tiers_to_tier_config(raw)
        assert result is False
        assert raw["models"]["claude"]["easy"] == {"model": "haiku", "effort": "low"}

    def test_no_op_when_no_models_key(self) -> None:
        raw: dict[str, Any] = {"version": 2, "ai": {"default_tool": "claude"}}
        result = migrate_string_tiers_to_tier_config(raw)
        assert result is False

    def test_no_op_when_models_is_not_dict(self) -> None:
        raw: dict[str, Any] = {"version": 2, "models": "not-a-dict"}
        result = migrate_string_tiers_to_tier_config(raw)
        assert result is False

    def test_skips_none_tier_values(self) -> None:
        raw: dict[str, Any] = {
            "version": 2,
            "models": {"claude": {"easy": None, "complex": "sonnet"}},
        }
        result = migrate_string_tiers_to_tier_config(raw)
        assert result is True
        assert raw["models"]["claude"]["easy"] is None
        assert raw["models"]["claude"]["complex"] == {"model": "sonnet", "effort": None}

    def test_all_four_tiers_converted(self) -> None:
        raw: dict[str, Any] = {
            "models": {
                "claude": {
                    "easy": "haiku",
                    "medium": "haiku",
                    "complex": "sonnet",
                    "very_complex": "opus",
                }
            }
        }
        migrate_string_tiers_to_tier_config(raw)
        tool = raw["models"]["claude"]
        for tier in ("easy", "medium", "complex", "very_complex"):
            assert isinstance(tool[tier], dict)
            assert "model" in tool[tier]
            assert "effort" in tool[tier]

    def test_run_all_migrations_applies_tier_conversion(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nmodels:\n  claude:\n    easy: haiku\n    complex: sonnet\n"
        )
        result = run_all_migrations(config_path)
        assert result is True
        migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert migrated["models"]["claude"]["easy"] == {"model": "haiku", "effort": None}
        assert migrated["models"]["claude"]["complex"] == {"model": "sonnet", "effort": None}
