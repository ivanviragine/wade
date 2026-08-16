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

permissions:
  allowed_commands:
    - wade *
    - ./scripts/check.sh *

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
        assert config.permissions.allowed_commands == ["wade *", "./scripts/check.sh *"]
        assert config.hooks.post_worktree_create == "scripts/setup-worktree.sh"
        assert config.hooks.copy_to_worktree == [".env"]

    def test_hooks_quality_gates_default_off(self, tmp_path: Path) -> None:
        # A config with no quality-gate keys leaves every gate off/empty, so
        # nothing is installed unless the project opts in.
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\n")
        config = parse_config_file(config_path)
        assert config.hooks.pre_commit.lint is None
        assert config.hooks.pre_commit.test is None
        assert config.hooks.commit_msg.conventional is False
        assert config.hooks.post_tool_use.enabled is False
        assert config.hooks.post_tool_use.lint_cmd is None
        assert config.hooks.post_tool_use.timeout == 10

    def test_hooks_quality_gates_parsed(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\n"
            "hooks:\n"
            "  pre_commit:\n"
            "    lint: ./scripts/check.sh --lint\n"
            "    test: ./scripts/test.sh\n"
            "  commit_msg:\n"
            "    conventional: true\n"
            "  post_tool_use:\n"
            "    enabled: true\n"
            "    lint_cmd: ruff check\n"
            "    timeout: 20\n"
        )
        config = parse_config_file(config_path)
        assert config.hooks.pre_commit.lint == "./scripts/check.sh --lint"
        assert config.hooks.pre_commit.test == "./scripts/test.sh"
        assert config.hooks.commit_msg.conventional is True
        assert config.hooks.post_tool_use.enabled is True
        assert config.hooks.post_tool_use.lint_cmd == "ruff check"
        assert config.hooks.post_tool_use.timeout == 20

    def test_hooks_null_booleans_normalized_to_defaults(self, tmp_path: Path) -> None:
        # A key present-but-null must fall back to the documented default (mirrors
        # the `done` section) rather than raising a Pydantic error at load time.
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\n"
            "hooks:\n"
            "  commit_msg:\n"
            "    conventional:\n"
            "  post_tool_use:\n"
            "    enabled:\n"
            "    timeout:\n"
        )
        config = parse_config_file(config_path)
        assert config.hooks.commit_msg.conventional is False
        assert config.hooks.post_tool_use.enabled is False
        assert config.hooks.post_tool_use.timeout == 10

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
        # Implement falls back to global (old "work:" YAML key maps to "implement" field)
        assert config.get_ai_tool("implement") == "copilot"

    def test_default_model_fallback(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(SAMPLE_V2_CONFIG)

        config = parse_config_file(config_path)
        # Implement has no explicit model, should fall back to default_model
        assert config.get_model("implement") == "claude-haiku-4.5"

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

    def test_done_gates_default_on(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\n")

        config = parse_config_file(config_path)
        # Every completion gate defaults on — enforcement is the point (#349).
        assert config.done.require_pr_summary is True
        assert config.done.require_sync is True
        assert config.done.require_review is True
        assert config.done.require_resolved_threads is True
        assert config.done.require_conventional_title is True
        assert config.done.pre_push_backstop is True

    def test_done_gates_round_trip(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\n"
            "done:\n"
            "  require_pr_summary: false\n"
            "  require_sync: false\n"
            "  require_review: false\n"
            "  require_resolved_threads: false\n"
            "  require_conventional_title: false\n"
            "  pre_push_backstop: false\n"
        )

        config = parse_config_file(config_path)
        assert config.done.require_pr_summary is False
        assert config.done.require_sync is False
        assert config.done.require_review is False
        assert config.done.require_resolved_threads is False
        assert config.done.require_conventional_title is False
        assert config.done.pre_push_backstop is False

    def test_done_flag_null_normalized_to_default(self, tmp_path: Path) -> None:
        # `require_sync:` with no value parses to None. DoneConfig's fields are
        # non-optional bools, so the loader must normalize None to the default
        # (True) rather than crash with a cryptic Pydantic error.
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\ndone:\n  require_sync:\n")

        config = parse_config_file(config_path)
        assert config.done.require_sync is True

    def test_max_review_passes_default(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\n")
        config = parse_config_file(config_path)
        assert config.done.max_review_passes == 2

    def test_max_review_passes_override(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\ndone:\n  max_review_passes: 5\n")
        config = parse_config_file(config_path)
        assert config.done.max_review_passes == 5

    def test_max_review_passes_null_normalized_to_default(self, tmp_path: Path) -> None:
        # An explicit null (not a bool default) must normalize to 2, not crash.
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\ndone:\n  max_review_passes:\n")
        config = parse_config_file(config_path)
        assert config.done.max_review_passes == 2

    def test_max_review_passes_zero_rejected_at_load(self, tmp_path: Path) -> None:
        # PositiveInt bound makes a 0 fail loudly at load (wrapped as ConfigError).
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\ndone:\n  max_review_passes: 0\n")
        with pytest.raises(ConfigError):
            parse_config_file(config_path)

    def test_max_review_passes_negative_rejected_at_load(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\ndone:\n  max_review_passes: -1\n")
        with pytest.raises(ConfigError):
            parse_config_file(config_path)

    def test_max_review_passes_bool_rejected_at_load(self, tmp_path: Path) -> None:
        # StrictInt rejects YAML `true` at load — a plain PositiveInt would coerce
        # it to 1 (bool is an int subclass), silently accepting an invalid value.
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\ndone:\n  max_review_passes: true\n")
        with pytest.raises(ConfigError):
            parse_config_file(config_path)

    def test_max_review_passes_string_rejected_at_load(self, tmp_path: Path) -> None:
        # StrictInt rejects a YAML string; a plain PositiveInt would coerce "2"→2.
        config_path = tmp_path / ".wade.yml"
        config_path.write_text('version: 2\ndone:\n  max_review_passes: "2"\n')
        with pytest.raises(ConfigError):
            parse_config_file(config_path)

    def test_max_review_passes_float_rejected_at_load(self, tmp_path: Path) -> None:
        # StrictInt rejects a YAML float; a plain PositiveInt would coerce 2.0→2.
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\ndone:\n  max_review_passes: 2.0\n")
        with pytest.raises(ConfigError):
            parse_config_file(config_path)

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


class TestParseCommandConfig:
    """Tests that per-command AI config fields are parsed correctly."""

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

    def test_review_pr_comments_parsed(self, tmp_path: Path) -> None:
        """The dedicated ``ai.review_pr_comments`` section (#389) round-trips.

        The loader iterates ``AI_COMMAND_NAMES`` and spreads the parsed sections
        onto ``AIConfig`` by name, so the new section maps onto
        ``AIConfig.review_pr_comments`` with no loader edit.
        """
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\n"
            "ai:\n"
            "  review_pr_comments:\n"
            "    tool: claude\n"
            "    model: claude-sonnet-5\n"
            "    effort: high\n"
            "    permission_mode: yolo\n"
        )
        config = parse_config_file(config_path)
        assert config.ai.review_pr_comments.tool == "claude"
        assert config.ai.review_pr_comments.model == "claude-sonnet-5"
        assert config.ai.review_pr_comments.effort == "high"
        assert config.ai.review_pr_comments.permission_mode == "yolo"

    def test_review_batch_and_yolo_parsed(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\n"
            "ai:\n"
            "  yolo: true\n"
            "  review_batch:\n"
            "    tool: claude\n"
            "    mode: headless\n"
            "    enabled: false\n"
            "    yolo: true\n"
        )
        config = parse_config_file(config_path)
        assert config.ai.yolo is True
        assert config.ai.review_batch.tool == "claude"
        assert config.ai.review_batch.mode == "headless"
        assert config.ai.review_batch.enabled is False
        assert config.ai.review_batch.yolo is True

    def test_global_effort_parsed(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  effort: medium\n")
        config = parse_config_file(config_path)
        assert config.ai.effort == "medium"

    def test_global_network_access_parsed(self, tmp_path: Path) -> None:
        # `ai.network_access: true` must reach the model — the loader used to drop
        # it, so the documented opt-in silently resolved to False (#423).
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  network_access: true\n")
        config = parse_config_file(config_path)
        assert config.ai.network_access is True
        assert config.get_network_access() is True

    def test_command_network_access_parsed(self, tmp_path: Path) -> None:
        # `ai.<command>.network_access: true` must reach the command config and
        # win over the (unset) global default in the resolver.
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  implement:\n    tool: codex\n    network_access: true\n"
        )
        config = parse_config_file(config_path)
        assert config.ai.implement.network_access is True
        assert config.get_network_access("implement") is True

    def test_network_access_defaults_to_none(self, tmp_path: Path) -> None:
        # Unset at both levels — the resolver falls through to disabled.
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: codex\n")
        config = parse_config_file(config_path)
        assert config.ai.network_access is None
        assert config.ai.implement.network_access is None
        assert config.get_network_access("implement") is False

    def test_enabled_false_parsed(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  review_plan:\n    tool: claude\n    enabled: false\n"
        )
        config = parse_config_file(config_path)
        assert config.ai.review_plan.enabled is False

    def test_enabled_true_parsed(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  review_plan:\n    tool: claude\n    enabled: true\n"
        )
        config = parse_config_file(config_path)
        assert config.ai.review_plan.enabled is True

    def test_enabled_missing_defaults_to_none(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  review_plan:\n    tool: claude\n")
        config = parse_config_file(config_path)
        assert config.ai.review_plan.enabled is None


class TestKnowledgeConfig:
    def test_knowledge_config_parsed(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nknowledge:\n  enabled: true\n  path: docs/KNOWLEDGE.md\n"
        )

        config = parse_config_file(config_path)
        assert config.knowledge.enabled is True
        assert config.knowledge.path == "docs/KNOWLEDGE.md"

    def test_falsey_non_mapping_knowledge_rejected(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nknowledge: false\n")

        with pytest.raises(ConfigError, match="knowledge must be a mapping"):
            parse_config_file(config_path)


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


class TestPermissionsParsing:
    def test_permissions_parsed(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\n"
            "permissions:\n"
            "  allowed_commands:\n"
            "    - wade *\n"
            "    - ./scripts/test.sh *\n"
        )
        config = parse_config_file(config_path)
        assert config.permissions.allowed_commands == ["wade *", "./scripts/test.sh *"]


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
