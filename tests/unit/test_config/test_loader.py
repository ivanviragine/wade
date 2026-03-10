"""Tests for config loader — walk-up discovery, parsing, validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from wade.config.loader import ConfigError, find_config_file, load_config, parse_config_file

SAMPLE_V2_CONFIG = """\
version: 2

project:
  main_branch: main
  issue_label: feature-plan
  worktrees_dir: ../.worktrees
  branch_prefix: feat
  merge_strategy: PR

ai:
  default_tool: copilot
  default_model: claude-haiku-4.5
  plan:
    tool: claude
    model: ""
  deps:
    tool: copilot
    model: ""
  work:
    tool: copilot
    model: ""

models:
  copilot:
    easy: claude-haiku-4.5
    medium: claude-haiku-4.5
    complex: claude-sonnet-4.6
    very_complex: claude-opus-4.6

provider:
  name: github

hooks:
  post_worktree_create: scripts/setup-worktree.sh
  copy_to_worktree:
    - .env
"""


class TestFindConfigFile:
    def test_finds_in_current(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\n")
        assert find_config_file(tmp_path) == config

    def test_finds_in_parent(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\n")
        child = tmp_path / "src" / "app"
        child.mkdir(parents=True)
        assert find_config_file(child) == config

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert find_config_file(tmp_path) is None


class TestParseConfigFile:
    def test_full_v2_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(SAMPLE_V2_CONFIG)

        config = parse_config_file(config_path)
        assert config.version == 2
        assert config.project.main_branch == "main"
        assert config.project.issue_label == "feature-plan"
        assert config.project.merge_strategy == "PR"
        assert config.ai.default_tool == "copilot"
        assert config.ai.default_model == "claude-haiku-4.5"
        assert config.ai.plan.tool == "claude"
        assert config.provider.name == "github"
        assert config.hooks.post_worktree_create == "scripts/setup-worktree.sh"
        assert config.hooks.copy_to_worktree == [".env"]

    def test_model_mapping(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(SAMPLE_V2_CONFIG)

        config = parse_config_file(config_path)
        assert "copilot" in config.models
        mapping = config.models["copilot"]
        assert mapping.easy == "claude-haiku-4.5"
        assert mapping.very_complex == "claude-opus-4.6"

    def test_command_override_fallback(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(SAMPLE_V2_CONFIG)

        config = parse_config_file(config_path)
        # Plan has specific tool override
        assert config.get_ai_tool("plan") == "claude"
        # Work falls back to global
        assert config.get_ai_tool("work") == "copilot"

    def test_default_model_fallback(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(SAMPLE_V2_CONFIG)

        config = parse_config_file(config_path)
        # Work has no explicit model, should fall back to default_model
        assert config.get_model("work") == "claude-haiku-4.5"

    def test_no_default_model(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n")

        config = parse_config_file(config_path)
        assert config.ai.default_model is None

    def test_minimal_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\n")

        config = parse_config_file(config_path)
        assert config.version == 2
        assert config.project.issue_label == "feature-plan"
        assert config.ai.default_tool is None

    def test_empty_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("")

        config = parse_config_file(config_path)
        assert config.version == 2  # Default

    def test_config_path_stored(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\n")

        config = parse_config_file(config_path)
        assert config.config_path == str(config_path)
        assert config.project_root == str(tmp_path)


class TestParseCommandConfigModeEffort:
    """Tests that mode and effort fields are parsed from per-command AI config."""

    def test_mode_parsed_from_deps(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  deps:\n    tool: claude\n    mode: headless\n")
        config = parse_config_file(config_path)
        assert config.ai.deps.mode == "headless"

    def test_effort_parsed_from_review_plan(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  review_plan:\n    tool: claude\n    effort: low\n"
        )
        config = parse_config_file(config_path)
        assert config.ai.review_plan.effort == "low"

    def test_mode_and_effort_default_to_none(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  plan:\n    tool: claude\n")
        config = parse_config_file(config_path)
        assert config.ai.plan.mode is None
        assert config.ai.plan.effort is None

    def test_review_plan_and_review_implementation_parsed(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\n"
            "ai:\n"
            "  review_plan:\n"
            "    tool: claude\n"
            "    mode: prompt\n"
            "  review_implementation:\n"
            "    tool: copilot\n"
            "    mode: headless\n"
        )
        config = parse_config_file(config_path)
        assert config.ai.review_plan.tool == "claude"
        assert config.ai.review_plan.mode == "prompt"
        assert config.ai.review_implementation.tool == "copilot"
        assert config.ai.review_implementation.mode == "headless"

    def test_global_effort_parsed(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  effort: medium\n")
        config = parse_config_file(config_path)
        assert config.ai.effort == "medium"


class TestProviderSettings:
    """Tests that provider settings dict is parsed from config."""

    def test_settings_parsed(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\n"
            "provider:\n"
            "  name: clickup\n"
            "  api_token_env: CLICKUP_API_TOKEN\n"
            "  settings:\n"
            "    list_id: '901'\n"
            "    team_id: '123'\n"
        )
        config = parse_config_file(config_path)
        assert config.provider.name == "clickup"
        assert config.provider.api_token_env == "CLICKUP_API_TOKEN"
        assert config.provider.settings == {"list_id": "901", "team_id": "123"}

    def test_settings_default_empty(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nprovider:\n  name: github\n")
        config = parse_config_file(config_path)
        assert config.provider.settings == {}

    def test_settings_null_treated_as_empty(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nprovider:\n  name: github\n  settings:\n")
        config = parse_config_file(config_path)
        assert config.provider.settings == {}


class TestParseConfigFileErrors:
    def test_malformed_yaml_raises_config_error(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(":\n  - [\ninvalid: yaml: content\n")

        with pytest.raises(ConfigError, match="Invalid YAML"):
            parse_config_file(config_path)

    def test_config_error_includes_file_path(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(":\n  - [\n")

        with pytest.raises(ConfigError, match=str(config_path)):
            parse_config_file(config_path)


class TestLoadConfig:
    def test_loads_from_cwd(self, tmp_path: Path, monkeypatch) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nproject:\n  issue_label: custom\n")

        config = load_config(tmp_path)
        assert config.project.issue_label == "custom"

    def test_returns_defaults_when_missing(self, tmp_path: Path) -> None:
        config = load_config(tmp_path)
        assert config.version == 2
        assert config.config_path is None
