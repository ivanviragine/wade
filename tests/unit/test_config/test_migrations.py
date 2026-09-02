"""Tests for config migration pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from wade.config.migrations import (
    ensure_version,
    migrate_string_tiers_to_tier_config,
    run_all_migrations,
    strip_knowledge_from_copy_to_worktree,
    strip_retired_network_access,
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


# ---------------------------------------------------------------------------
# strip_knowledge_from_copy_to_worktree (#358)
# ---------------------------------------------------------------------------


class TestStripKnowledgeFromCopyToWorktree:
    def test_strips_default_knowledge_and_ratings(self) -> None:
        raw: dict = {
            "hooks": {
                "copy_to_worktree": [
                    ".env",
                    "KNOWLEDGE.md",
                    "KNOWLEDGE.ratings.yml",
                    "KNOWLEDGE.ratings.jsonl",
                ]
            }
        }
        assert strip_knowledge_from_copy_to_worktree(raw) is True
        assert raw["hooks"]["copy_to_worktree"] == [".env"]

    def test_strips_custom_knowledge_path(self) -> None:
        raw: dict = {
            "knowledge": {"enabled": True, "path": "docs/LEARNINGS.md"},
            "hooks": {"copy_to_worktree": ["docs/LEARNINGS.md", "docs/LEARNINGS.ratings.yml"]},
        }
        assert strip_knowledge_from_copy_to_worktree(raw) is True
        assert raw["hooks"]["copy_to_worktree"] == []

    def test_strips_when_config_path_has_contained_dotdot(self) -> None:
        # #358 review: a contained ``..`` in the config path must canonicalize so the
        # plainly-spelled copy entries are still stripped — the same policy bootstrap's
        # copy-exclusion applies, so a redundant-``..`` spelling can't bypass either site.
        raw: dict = {
            "knowledge": {"enabled": True, "path": "docs/../KNOWLEDGE.md"},
            "hooks": {"copy_to_worktree": ["KNOWLEDGE.md", "KNOWLEDGE.ratings.jsonl", ".env"]},
        }
        assert strip_knowledge_from_copy_to_worktree(raw) is True
        assert raw["hooks"]["copy_to_worktree"] == [".env"]

    def test_noop_when_nothing_to_strip(self) -> None:
        raw: dict = {"hooks": {"copy_to_worktree": [".env", ".secrets"]}}
        assert strip_knowledge_from_copy_to_worktree(raw) is False
        assert raw["hooks"]["copy_to_worktree"] == [".env", ".secrets"]

    def test_noop_when_no_hooks_section(self) -> None:
        raw: dict = {"project": {}}
        assert strip_knowledge_from_copy_to_worktree(raw) is False

    def test_run_all_migrations_strips_knowledge_copy_entries(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\n"
            "hooks:\n"
            "  copy_to_worktree:\n"
            "  - .env\n"
            "  - KNOWLEDGE.md\n"
            "  - KNOWLEDGE.ratings.yml\n"
        )
        assert run_all_migrations(config_path) is True
        migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert migrated["hooks"]["copy_to_worktree"] == [".env"]


# ---------------------------------------------------------------------------
# strip_retired_network_access (#478)
# ---------------------------------------------------------------------------


class TestStripRetiredNetworkAccess:
    def test_strips_global_key(self) -> None:
        raw: dict = {"ai": {"network_access": True, "default_tool": "codex"}}
        assert strip_retired_network_access(raw) is True
        assert raw["ai"] == {"default_tool": "codex"}

    def test_strips_per_command_keys(self) -> None:
        raw: dict = {
            "ai": {
                "implement": {"network_access": True, "yolo": True},
                "review_pr_comments": {"network_access": False},
            }
        }
        assert strip_retired_network_access(raw) is True
        assert raw["ai"]["implement"] == {"yolo": True}
        assert raw["ai"]["review_pr_comments"] == {}

    def test_strips_global_and_per_command_together(self) -> None:
        raw: dict = {
            "ai": {
                "network_access": True,
                "plan": {"network_access": False, "effort": "high"},
            }
        }
        assert strip_retired_network_access(raw) is True
        assert "network_access" not in raw["ai"]
        assert raw["ai"]["plan"] == {"effort": "high"}

    def test_is_idempotent(self) -> None:
        raw: dict = {"ai": {"network_access": True, "implement": {"network_access": False}}}
        assert strip_retired_network_access(raw) is True
        # Second run finds nothing left to strip and reports no change.
        assert strip_retired_network_access(raw) is False

    def test_leaves_the_replacement_key_alone(self) -> None:
        # ``sandbox`` is the replacement axis, not a casualty of the cleanup.
        raw: dict = {"ai": {"sandbox": True, "implement": {"sandbox": False}}}
        assert strip_retired_network_access(raw) is False
        assert raw["ai"]["sandbox"] is True
        assert raw["ai"]["implement"]["sandbox"] is False

    def test_noop_when_no_ai_section(self) -> None:
        raw: dict = {"project": {}}
        assert strip_retired_network_access(raw) is False

    def test_noop_when_ai_section_is_not_a_mapping(self) -> None:
        raw: dict = {"ai": "nonsense"}
        assert strip_retired_network_access(raw) is False

    def test_run_all_migrations_strips_the_retired_key(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\n"
            "ai:\n"
            "  network_access: true\n"
            "  default_tool: codex\n"
            "  implement:\n"
            "    network_access: false\n"
            "    yolo: true\n"
        )
        assert run_all_migrations(config_path) is True
        migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert "network_access" not in migrated["ai"]
        assert "network_access" not in migrated["ai"]["implement"]
        assert migrated["ai"]["default_tool"] == "codex"
        assert migrated["ai"]["implement"]["yolo"] is True

    def test_run_all_migrations_is_idempotent_on_a_migrated_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  network_access: true\n")
        assert run_all_migrations(config_path) is True
        assert run_all_migrations(config_path) is False
