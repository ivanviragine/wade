"""Tests for config loader — walk-up discovery, parsing, validation."""

from __future__ import annotations

from pathlib import Path

from ghaiw.config.loader import find_config_file, load_config, parse_config_file

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
        config = tmp_path / ".ghaiw.yml"
        config.write_text("version: 2\n")
        assert find_config_file(tmp_path) == config

    def test_finds_in_parent(self, tmp_path: Path) -> None:
        config = tmp_path / ".ghaiw.yml"
        config.write_text("version: 2\n")
        child = tmp_path / "src" / "app"
        child.mkdir(parents=True)
        assert find_config_file(child) == config

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert find_config_file(tmp_path) is None


class TestParseConfigFile:
    def test_full_v2_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text(SAMPLE_V2_CONFIG)

        config = parse_config_file(config_path)
        assert config.version == 2
        assert config.project.main_branch == "main"
        assert config.project.issue_label == "feature-plan"
        assert config.project.merge_strategy == "PR"
        assert config.ai.default_tool == "copilot"
        assert config.ai.plan.tool == "claude"
        assert config.provider.name == "github"
        assert config.hooks.post_worktree_create == "scripts/setup-worktree.sh"
        assert config.hooks.copy_to_worktree == [".env"]

    def test_model_mapping(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text(SAMPLE_V2_CONFIG)

        config = parse_config_file(config_path)
        assert "copilot" in config.models
        mapping = config.models["copilot"]
        assert mapping.easy == "claude-haiku-4.5"
        assert mapping.very_complex == "claude-opus-4.6"

    def test_command_override_fallback(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text(SAMPLE_V2_CONFIG)

        config = parse_config_file(config_path)
        # Plan has specific tool override
        assert config.get_ai_tool("plan") == "claude"
        # Work falls back to global
        assert config.get_ai_tool("work") == "copilot"

    def test_minimal_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text("version: 2\n")

        config = parse_config_file(config_path)
        assert config.version == 2
        assert config.project.issue_label == "feature-plan"
        assert config.ai.default_tool is None

    def test_empty_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text("")

        config = parse_config_file(config_path)
        assert config.version == 2  # Default

    def test_config_path_stored(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text("version: 2\n")

        config = parse_config_file(config_path)
        assert config.config_path == str(config_path)
        assert config.project_root == str(tmp_path)


class TestLoadConfig:
    def test_loads_from_cwd(self, tmp_path: Path, monkeypatch) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        config_path.write_text("version: 2\nproject:\n  issue_label: custom\n")

        config = load_config(tmp_path)
        assert config.project.issue_label == "custom"

    def test_returns_defaults_when_missing(self, tmp_path: Path) -> None:
        config = load_config(tmp_path)
        assert config.version == 2
        assert config.config_path is None
