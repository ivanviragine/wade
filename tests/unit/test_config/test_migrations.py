"""Tests for config migration pipeline — idempotent YAML mutations."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from ghaiw.config.migrations import (
    _get_ai_tool,
    backfill_missing_model_keys,
    backfill_per_command_keys,
    ensure_version,
    migrate_deprecated_model_values,
    migrate_flat_to_nested_models,
    normalize_merge_strategy,
    normalize_model_format,
    run_all_migrations,
)

# ---------------------------------------------------------------------------
# _get_ai_tool
# ---------------------------------------------------------------------------


class TestGetAITool:
    def test_v2_format_ai_default_tool(self) -> None:
        raw = {"ai": {"default_tool": "claude"}}
        assert _get_ai_tool(raw) == "claude"

    def test_v1_format_ai_tool(self) -> None:
        raw = {"ai_tool": "copilot"}
        assert _get_ai_tool(raw) == "copilot"

    def test_v2_takes_precedence_over_v1(self) -> None:
        raw = {"ai": {"default_tool": "gemini"}, "ai_tool": "claude"}
        assert _get_ai_tool(raw) == "gemini"

    def test_returns_none_when_missing(self) -> None:
        raw = {"project": {"main_branch": "main"}}
        assert _get_ai_tool(raw) is None

    def test_returns_none_when_ai_is_not_dict(self) -> None:
        raw = {"ai": "claude"}
        assert _get_ai_tool(raw) is None

    def test_returns_none_when_default_tool_is_empty_string(self) -> None:
        raw = {"ai": {"default_tool": ""}}
        assert _get_ai_tool(raw) is None

    def test_returns_none_when_ai_tool_is_empty_string(self) -> None:
        raw = {"ai_tool": ""}
        assert _get_ai_tool(raw) is None

    def test_falls_back_to_v1_when_v2_default_tool_missing(self) -> None:
        raw = {"ai": {"plan": {}}, "ai_tool": "codex"}
        assert _get_ai_tool(raw) == "codex"

    def test_converts_to_string(self) -> None:
        raw = {"ai": {"default_tool": 42}}
        assert _get_ai_tool(raw) == "42"


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
# migrate_deprecated_model_values
# ---------------------------------------------------------------------------


class TestMigrateDeprecatedModelValues:
    def test_replaces_deprecated_model(self) -> None:
        raw: dict = {
            "models": {
                "claude": {
                    "easy": "claude-sonnet-4",
                    "medium": "claude-haiku-4-5",
                }
            }
        }
        result = migrate_deprecated_model_values(raw, "claude")
        assert result is True
        assert raw["models"]["claude"]["easy"] == "claude-sonnet-4.6"
        assert raw["models"]["claude"]["medium"] == "claude-haiku-4.5"

    def test_skips_unknown_model(self) -> None:
        raw: dict = {
            "models": {
                "claude": {
                    "easy": "claude-haiku-4.5",
                    "complex": "claude-sonnet-4.6",
                }
            }
        }
        result = migrate_deprecated_model_values(raw, "claude")
        assert result is False

    def test_no_models_section(self) -> None:
        raw: dict = {"version": 2}
        result = migrate_deprecated_model_values(raw, "claude")
        assert result is False

    def test_models_not_a_dict(self) -> None:
        raw: dict = {"models": "invalid"}
        result = migrate_deprecated_model_values(raw, "claude")
        assert result is False

    def test_tool_mapping_not_a_dict(self) -> None:
        raw: dict = {"models": {"claude": "invalid"}}
        result = migrate_deprecated_model_values(raw, "claude")
        assert result is False

    def test_replaces_across_multiple_tools(self) -> None:
        raw: dict = {
            "models": {
                "claude": {"easy": "claude-sonnet-4"},
                "copilot": {"easy": "claude-opus-4"},
            }
        }
        result = migrate_deprecated_model_values(raw, "claude")
        assert result is True
        assert raw["models"]["claude"]["easy"] == "claude-sonnet-4.6"
        assert raw["models"]["copilot"]["easy"] == "claude-opus-4.6"

    def test_no_ai_tool_still_works(self) -> None:
        raw: dict = {"models": {"claude": {"easy": "claude-sonnet-4"}}}
        result = migrate_deprecated_model_values(raw, None)
        assert result is True
        assert raw["models"]["claude"]["easy"] == "claude-sonnet-4.6"

    def test_non_string_values_ignored(self) -> None:
        raw: dict = {"models": {"claude": {"easy": 123}}}
        result = migrate_deprecated_model_values(raw, "claude")
        assert result is False

    def test_idempotent_second_call(self) -> None:
        raw: dict = {"models": {"claude": {"easy": "claude-sonnet-4"}}}
        migrate_deprecated_model_values(raw, "claude")
        result = migrate_deprecated_model_values(raw, "claude")
        assert result is False


# ---------------------------------------------------------------------------
# migrate_flat_to_nested_models
# ---------------------------------------------------------------------------


class TestMigrateFlatToNestedModels:
    def test_moves_flat_keys_to_nested(self) -> None:
        raw: dict = {
            "model_easy": "claude-haiku-4-5",
            "model_medium": "claude-haiku-4-5",
            "model_complex": "claude-sonnet-4-6",
            "model_very_complex": "claude-opus-4-6",
        }
        result = migrate_flat_to_nested_models(raw, "claude")
        assert result is True
        assert raw["models"]["claude"]["easy"] == "claude-haiku-4-5"
        assert raw["models"]["claude"]["medium"] == "claude-haiku-4-5"
        assert raw["models"]["claude"]["complex"] == "claude-sonnet-4-6"
        assert raw["models"]["claude"]["very_complex"] == "claude-opus-4-6"
        assert "model_easy" not in raw
        assert "model_medium" not in raw
        assert "model_complex" not in raw
        assert "model_very_complex" not in raw

    def test_preserves_existing_nested_values(self) -> None:
        raw: dict = {
            "model_easy": "claude-haiku-4-5",
            "models": {"claude": {"easy": "existing-model"}},
        }
        result = migrate_flat_to_nested_models(raw, "claude")
        assert result is False
        assert raw["models"]["claude"]["easy"] == "existing-model"
        assert "model_easy" not in raw

    def test_returns_false_when_no_flat_keys(self) -> None:
        raw: dict = {"version": 2, "models": {"claude": {"easy": "haiku"}}}
        result = migrate_flat_to_nested_models(raw, "claude")
        assert result is False

    def test_returns_false_when_no_ai_tool(self) -> None:
        raw: dict = {"model_easy": "claude-haiku-4-5"}
        result = migrate_flat_to_nested_models(raw, None)
        assert result is False

    def test_partial_flat_keys(self) -> None:
        raw: dict = {
            "model_easy": "claude-haiku-4-5",
            "model_complex": "claude-sonnet-4-6",
        }
        result = migrate_flat_to_nested_models(raw, "claude")
        assert result is True
        assert raw["models"]["claude"]["easy"] == "claude-haiku-4-5"
        assert raw["models"]["claude"]["complex"] == "claude-sonnet-4-6"
        assert "medium" not in raw["models"]["claude"]

    def test_creates_models_section_if_missing(self) -> None:
        raw: dict = {"model_easy": "claude-haiku-4-5"}
        migrate_flat_to_nested_models(raw, "claude")
        assert "models" in raw
        assert "claude" in raw["models"]

    def test_flat_keys_removed_even_when_nested_exists(self) -> None:
        raw: dict = {
            "model_easy": "should-be-removed",
            "model_medium": "new-medium",
            "models": {"claude": {"easy": "existing"}},
        }
        migrate_flat_to_nested_models(raw, "claude")
        assert "model_easy" not in raw
        assert "model_medium" not in raw
        assert raw["models"]["claude"]["easy"] == "existing"
        assert raw["models"]["claude"]["medium"] == "new-medium"

    def test_idempotent_second_call(self) -> None:
        raw: dict = {
            "model_easy": "claude-haiku-4-5",
            "model_complex": "claude-sonnet-4-6",
        }
        migrate_flat_to_nested_models(raw, "claude")
        result = migrate_flat_to_nested_models(raw, "claude")
        assert result is False


# ---------------------------------------------------------------------------
# normalize_model_format
# ---------------------------------------------------------------------------


class TestNormalizeModelFormat:
    def test_dots_for_claude(self) -> None:
        raw: dict = {
            "models": {
                "claude": {
                    "easy": "claude-haiku-4-5",
                    "complex": "claude-sonnet-4-6",
                }
            }
        }
        result = normalize_model_format(raw, "claude")
        assert result is True
        assert raw["models"]["claude"]["easy"] == "claude-haiku-4.5"
        assert raw["models"]["claude"]["complex"] == "claude-sonnet-4.6"

    def test_dots_for_copilot(self) -> None:
        raw: dict = {
            "models": {
                "copilot": {
                    "easy": "claude-haiku-4-5",
                    "complex": "claude-sonnet-4-6",
                }
            }
        }
        result = normalize_model_format(raw, "copilot")
        assert result is True
        assert raw["models"]["copilot"]["easy"] == "claude-haiku-4.5"
        assert raw["models"]["copilot"]["complex"] == "claude-sonnet-4.6"

    def test_noop_when_already_correct_dots(self) -> None:
        raw: dict = {
            "models": {
                "claude": {
                    "easy": "claude-haiku-4.5",
                }
            }
        }
        result = normalize_model_format(raw, "claude")
        assert result is False

    def test_noop_when_already_correct_dots_copilot(self) -> None:
        raw: dict = {
            "models": {
                "copilot": {
                    "easy": "claude-haiku-4.5",
                }
            }
        }
        result = normalize_model_format(raw, "copilot")
        assert result is False

    def test_non_claude_models_unchanged(self) -> None:
        raw: dict = {
            "models": {
                "gemini": {
                    "easy": "gemini-2.0-flash",
                    "complex": "gemini-2.5-pro",
                }
            }
        }
        original = copy.deepcopy(raw)
        result = normalize_model_format(raw, "gemini")
        assert result is False
        assert raw == original

    def test_returns_false_no_ai_tool(self) -> None:
        raw: dict = {"models": {"claude": {"easy": "claude-haiku-4.5"}}}
        result = normalize_model_format(raw, None)
        assert result is False

    def test_returns_false_no_models_section(self) -> None:
        raw: dict = {"version": 2}
        result = normalize_model_format(raw, "claude")
        assert result is False

    def test_models_not_a_dict(self) -> None:
        raw: dict = {"models": "invalid"}
        result = normalize_model_format(raw, "claude")
        assert result is False

    def test_normalizes_across_all_tool_sections(self) -> None:
        raw: dict = {
            "models": {
                "claude": {"easy": "claude-haiku-4-5"},
                "copilot": {"easy": "claude-haiku-4-5"},
            }
        }
        result = normalize_model_format(raw, "claude")
        assert result is True
        assert raw["models"]["claude"]["easy"] == "claude-haiku-4.5"
        assert raw["models"]["copilot"]["easy"] == "claude-haiku-4.5"

    def test_non_string_values_ignored(self) -> None:
        raw: dict = {"models": {"claude": {"easy": 123}}}
        result = normalize_model_format(raw, "claude")
        assert result is False

    def test_opus_model_dashes_to_dots(self) -> None:
        raw: dict = {"models": {"claude": {"very_complex": "claude-opus-4-6"}}}
        result = normalize_model_format(raw, "claude")
        assert result is True
        assert raw["models"]["claude"]["very_complex"] == "claude-opus-4.6"

    def test_opus_model_dots(self) -> None:
        raw: dict = {"models": {"copilot": {"very_complex": "claude-opus-4-6"}}}
        result = normalize_model_format(raw, "copilot")
        assert result is True
        assert raw["models"]["copilot"]["very_complex"] == "claude-opus-4.6"

    def test_idempotent_second_call_claude(self) -> None:
        raw: dict = {"models": {"claude": {"easy": "claude-haiku-4.5"}}}
        normalize_model_format(raw, "claude")
        result = normalize_model_format(raw, "claude")
        assert result is False

    def test_idempotent_second_call_copilot(self) -> None:
        raw: dict = {"models": {"copilot": {"easy": "claude-haiku-4-5"}}}
        normalize_model_format(raw, "copilot")
        result = normalize_model_format(raw, "copilot")
        assert result is False


# ---------------------------------------------------------------------------
# backfill_missing_model_keys
# ---------------------------------------------------------------------------


class TestBackfillMissingModelKeys:
    def test_fills_all_missing_tiers(self) -> None:
        raw: dict = {"models": {"claude": {}}}
        result = backfill_missing_model_keys(raw, "claude")
        assert result is True
        mapping = raw["models"]["claude"]
        assert mapping["easy"] == "claude-haiku-4.5"
        assert mapping["medium"] == "claude-haiku-4.5"
        assert mapping["complex"] == "claude-sonnet-4.6"
        assert mapping["very_complex"] == "claude-opus-4.6"

    def test_preserves_existing_values(self) -> None:
        raw: dict = {
            "models": {
                "claude": {
                    "easy": "custom-model",
                    "complex": "another-custom",
                }
            }
        }
        result = backfill_missing_model_keys(raw, "claude")
        assert result is True
        assert raw["models"]["claude"]["easy"] == "custom-model"
        assert raw["models"]["claude"]["complex"] == "another-custom"
        assert raw["models"]["claude"]["medium"] == "claude-haiku-4.5"
        assert raw["models"]["claude"]["very_complex"] == "claude-opus-4.6"

    def test_noop_when_all_present(self) -> None:
        raw: dict = {
            "models": {
                "claude": {
                    "easy": "a",
                    "medium": "b",
                    "complex": "c",
                    "very_complex": "d",
                }
            }
        }
        result = backfill_missing_model_keys(raw, "claude")
        assert result is False

    def test_returns_false_no_ai_tool(self) -> None:
        raw: dict = {}
        result = backfill_missing_model_keys(raw, None)
        assert result is False

    def test_creates_models_section_if_missing(self) -> None:
        raw: dict = {}
        result = backfill_missing_model_keys(raw, "claude")
        assert result is True
        assert "models" in raw
        assert "claude" in raw["models"]

    def test_copilot_defaults_use_dots(self) -> None:
        raw: dict = {}
        backfill_missing_model_keys(raw, "copilot")
        assert raw["models"]["copilot"]["easy"] == "claude-haiku-4.5"
        assert raw["models"]["copilot"]["complex"] == "claude-sonnet-4.6"
        assert raw["models"]["copilot"]["very_complex"] == "claude-opus-4.6"

    def test_gemini_defaults(self) -> None:
        raw: dict = {}
        backfill_missing_model_keys(raw, "gemini")
        assert raw["models"]["gemini"]["easy"] == "gemini-3-flash-preview"
        assert raw["models"]["gemini"]["complex"] == "gemini-3-pro-preview"

    def test_unknown_tool_returns_false(self) -> None:
        raw: dict = {}
        result = backfill_missing_model_keys(raw, "unknown-tool")
        assert result is False

    def test_idempotent_second_call(self) -> None:
        raw: dict = {}
        backfill_missing_model_keys(raw, "claude")
        result = backfill_missing_model_keys(raw, "claude")
        assert result is False


# ---------------------------------------------------------------------------
# backfill_per_command_keys
# ---------------------------------------------------------------------------


class TestBackfillPerCommandKeys:
    def test_creates_all_command_sections(self) -> None:
        raw: dict = {"ai": {"default_tool": "claude"}}
        result = backfill_per_command_keys(raw)
        assert result is True
        assert raw["ai"]["plan"] == {}
        assert raw["ai"]["deps"] == {}
        assert raw["ai"]["work"] == {}

    def test_preserves_existing_command_sections(self) -> None:
        raw: dict = {
            "ai": {
                "default_tool": "claude",
                "plan": {"tool": "gemini", "model": "gemini-2.5-pro"},
                "deps": {"tool": "claude"},
                "work": {"model": "claude-opus-4-6"},
            }
        }
        result = backfill_per_command_keys(raw)
        assert result is False
        assert raw["ai"]["plan"]["tool"] == "gemini"
        assert raw["ai"]["deps"]["tool"] == "claude"
        assert raw["ai"]["work"]["model"] == "claude-opus-4-6"

    def test_creates_ai_section_if_missing(self) -> None:
        raw: dict = {"version": 2}
        result = backfill_per_command_keys(raw)
        assert result is True
        assert "ai" in raw
        assert raw["ai"]["plan"] == {}

    def test_replaces_non_dict_ai_section(self) -> None:
        raw: dict = {"ai": "invalid"}
        result = backfill_per_command_keys(raw)
        assert result is True
        assert isinstance(raw["ai"], dict)
        assert raw["ai"]["plan"] == {}
        assert raw["ai"]["deps"] == {}
        assert raw["ai"]["work"] == {}

    def test_partial_missing(self) -> None:
        raw: dict = {
            "ai": {
                "plan": {"tool": "claude"},
            }
        }
        result = backfill_per_command_keys(raw)
        assert result is True
        assert raw["ai"]["plan"]["tool"] == "claude"
        assert raw["ai"]["deps"] == {}
        assert raw["ai"]["work"] == {}

    def test_replaces_non_dict_command_section(self) -> None:
        raw: dict = {
            "ai": {
                "plan": "invalid",
                "deps": {"tool": "claude"},
                "work": 42,
            }
        }
        result = backfill_per_command_keys(raw)
        assert result is True
        assert raw["ai"]["plan"] == {}
        assert raw["ai"]["deps"]["tool"] == "claude"
        assert raw["ai"]["work"] == {}

    def test_idempotent_second_call(self) -> None:
        raw: dict = {}
        backfill_per_command_keys(raw)
        result = backfill_per_command_keys(raw)
        assert result is False


# ---------------------------------------------------------------------------
# normalize_merge_strategy
# ---------------------------------------------------------------------------


class TestNormalizeMergeStrategy:
    def test_lowercase_pr_becomes_uppercase(self) -> None:
        raw: dict = {"project": {"merge_strategy": "pr"}}
        result = normalize_merge_strategy(raw)
        assert result is True
        assert raw["project"]["merge_strategy"] == "PR"

    def test_mixed_case_pr_becomes_uppercase(self) -> None:
        raw: dict = {"project": {"merge_strategy": "Pr"}}
        result = normalize_merge_strategy(raw)
        assert result is True
        assert raw["project"]["merge_strategy"] == "PR"

    def test_uppercase_pr_unchanged(self) -> None:
        raw: dict = {"project": {"merge_strategy": "PR"}}
        result = normalize_merge_strategy(raw)
        assert result is False
        assert raw["project"]["merge_strategy"] == "PR"

    def test_direct_unchanged(self) -> None:
        raw: dict = {"project": {"merge_strategy": "direct"}}
        result = normalize_merge_strategy(raw)
        assert result is False
        assert raw["project"]["merge_strategy"] == "direct"

    def test_no_project_section(self) -> None:
        raw: dict = {"version": 2}
        result = normalize_merge_strategy(raw)
        assert result is False

    def test_project_not_a_dict(self) -> None:
        raw: dict = {"project": "invalid"}
        result = normalize_merge_strategy(raw)
        assert result is False

    def test_no_merge_strategy_key(self) -> None:
        raw: dict = {"project": {"main_branch": "main"}}
        result = normalize_merge_strategy(raw)
        assert result is False

    def test_non_string_strategy(self) -> None:
        raw: dict = {"project": {"merge_strategy": 42}}
        result = normalize_merge_strategy(raw)
        assert result is False

    def test_idempotent_second_call(self) -> None:
        raw: dict = {"project": {"merge_strategy": "pr"}}
        normalize_merge_strategy(raw)
        result = normalize_merge_strategy(raw)
        assert result is False


# ---------------------------------------------------------------------------
# run_all_migrations
# ---------------------------------------------------------------------------


class TestRunAllMigrations:
    def test_full_pipeline_v1_config(self, tmp_path: Path) -> None:
        """V1 flat keys are migrated to nested; deprecated values in flat keys
        survive the first run because migration 2 (deprecated) runs before
        migration 3 (flat-to-nested), so the value isn't in `models` yet when
        deprecation is checked. A second run cleans them up."""
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text(
            yaml.dump(
                {
                    "ai_tool": "claude",
                    "model_easy": "gemini-3-flash",
                    "model_medium": "claude-haiku-4.5",
                    "model_complex": "claude-sonnet-4-6",
                    "project": {"merge_strategy": "pr"},
                },
                default_flow_style=False,
            )
        )

        result = run_all_migrations(config_path)
        assert result is True

        migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert migrated["version"] == 2
        # Flat keys migrated to nested
        assert "model_easy" not in migrated
        assert "model_medium" not in migrated
        # Deprecated model survives first pass (moved by flat-to-nested after
        # deprecated-replacement already ran). Second run will fix it.
        assert migrated["models"]["claude"]["easy"] == "gemini-3-flash"
        # Model stays dotted (dot notation is canonical)
        assert migrated["models"]["claude"]["medium"] == "claude-haiku-4.5"
        # Merge strategy normalized
        assert migrated["project"]["merge_strategy"] == "PR"
        # Per-command keys backfilled
        assert "plan" in migrated["ai"]
        assert "deps" in migrated["ai"]
        assert "work" in migrated["ai"]
        # Missing tiers backfilled
        assert migrated["models"]["claude"]["very_complex"] == "claude-opus-4.6"

    def test_full_pipeline_v1_deprecated_cleaned_on_second_run(self, tmp_path: Path) -> None:
        """Deprecated flat values that were moved to nested on first run are
        replaced on the second run when the deprecated-model migration sees
        them in the `models` section."""
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text(
            yaml.dump(
                {
                    "ai_tool": "claude",
                    "model_easy": "claude-sonnet-4",
                    "model_complex": "claude-sonnet-4-6",
                    "project": {"merge_strategy": "pr"},
                },
                default_flow_style=False,
            )
        )

        run_all_migrations(config_path)
        second = run_all_migrations(config_path)
        assert second is True

        migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert migrated["models"]["claude"]["easy"] == "claude-sonnet-4.6"

        # Third run should be a no-op
        third = run_all_migrations(config_path)
        assert third is False

    def test_full_pipeline_v2_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text(
            yaml.dump(
                {
                    "version": 2,
                    "ai": {"default_tool": "copilot"},
                    "models": {
                        "copilot": {
                            "easy": "claude-haiku-4-5",
                            "medium": "claude-haiku-4-5",
                        }
                    },
                    "project": {"merge_strategy": "PR"},
                },
                default_flow_style=False,
            )
        )

        result = run_all_migrations(config_path)
        assert result is True

        migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        # Dashed models normalized to dotted for copilot
        assert migrated["models"]["copilot"]["easy"] == "claude-haiku-4.5"
        assert migrated["models"]["copilot"]["medium"] == "claude-haiku-4.5"
        # Missing tiers backfilled with copilot defaults
        assert migrated["models"]["copilot"]["complex"] == "claude-sonnet-4.6"
        assert migrated["models"]["copilot"]["very_complex"] == "claude-opus-4.6"

    def test_idempotent_second_run(self, tmp_path: Path) -> None:
        """A config with no cross-migration ordering issues stabilizes in one pass."""
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text(
            yaml.dump(
                {
                    "ai_tool": "claude",
                    "model_easy": "claude-haiku-4-5",
                    "model_complex": "claude-sonnet-4-6",
                    "project": {"merge_strategy": "pr"},
                },
                default_flow_style=False,
            )
        )

        first = run_all_migrations(config_path)
        assert first is True

        content_after_first = config_path.read_text(encoding="utf-8")
        second = run_all_migrations(config_path)
        assert second is False

        content_after_second = config_path.read_text(encoding="utf-8")
        assert content_after_first == content_after_second

    def test_already_migrated_returns_false(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text(
            yaml.dump(
                {
                    "version": 2,
                    "ai": {
                        "default_tool": "claude",
                        "plan": {},
                        "deps": {},
                        "work": {},
                    },
                    "models": {
                        "claude": {
                            "easy": "claude-haiku-4.5",
                            "medium": "claude-haiku-4.5",
                            "complex": "claude-sonnet-4.6",
                            "very_complex": "claude-opus-4.6",
                        }
                    },
                    "project": {"merge_strategy": "PR"},
                },
                default_flow_style=False,
            )
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
        assert "plan" in migrated["ai"]

    def test_non_dict_yaml_treated_as_empty(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text("just a string\n")

        result = run_all_migrations(config_path)
        assert result is True

        migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert migrated["version"] == 2

    def test_file_not_modified_when_no_changes(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        content = yaml.dump(
            {
                "version": 2,
                "ai": {
                    "default_tool": "claude",
                    "plan": {},
                    "deps": {},
                    "work": {},
                },
                "models": {
                    "claude": {
                        "easy": "claude-haiku-4.5",
                        "medium": "claude-haiku-4.5",
                        "complex": "claude-sonnet-4.6",
                        "very_complex": "claude-opus-4.6",
                    }
                },
                "project": {"merge_strategy": "PR"},
            },
            default_flow_style=False,
        )
        config_path.write_text(content)
        mtime_before = config_path.stat().st_mtime_ns

        run_all_migrations(config_path)
        mtime_after = config_path.stat().st_mtime_ns

        assert mtime_before == mtime_after
