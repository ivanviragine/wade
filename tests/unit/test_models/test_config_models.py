"""Tests for configuration models."""

from __future__ import annotations

from wade.models.config import (
    AICommandConfig,
    AIConfig,
    ComplexityModelMapping,
    PermissionsConfig,
    ProjectConfig,
)
from wade.models.work import MergeStrategy


class TestPermissionsConfig:
    def test_defaults(self) -> None:
        perms = PermissionsConfig()
        assert perms.allowed_commands == ["wade *"]

    def test_custom_commands(self) -> None:
        perms = PermissionsConfig(
            allowed_commands=["wade *", "./scripts/check.sh *", "./scripts/fmt.sh *"]
        )
        assert len(perms.allowed_commands) == 3
        assert "./scripts/check.sh *" in perms.allowed_commands

    def test_empty_commands(self) -> None:
        perms = PermissionsConfig(allowed_commands=[])
        assert perms.allowed_commands == []


class TestProjectConfig:
    def test_defaults(self) -> None:
        config = ProjectConfig()
        assert config.version == 2
        assert config.project.merge_strategy == MergeStrategy.PR
        assert config.project.issue_label == "feature-plan"
        assert config.project.branch_prefix == "feat"
        assert config.ai.default_tool is None
        assert config.models == {}
        assert config.permissions.allowed_commands == ["wade *"]

    def test_get_ai_tool_global(self) -> None:
        config = ProjectConfig(ai=AIConfig(default_tool="claude"))
        assert config.get_ai_tool() == "claude"
        assert config.get_ai_tool("plan") == "claude"

    def test_get_ai_tool_command_override(self) -> None:
        config = ProjectConfig(
            ai=AIConfig(
                default_tool="copilot",
                plan=AICommandConfig(tool="claude"),
            )
        )
        assert config.get_ai_tool("plan") == "claude"
        assert config.get_ai_tool("work") == "copilot"

    def test_get_model_command_specific(self) -> None:
        config = ProjectConfig(
            ai=AIConfig(
                default_model="default-model",
                work=AICommandConfig(model="work-model"),
            )
        )
        assert config.get_model("work") == "work-model"

    def test_get_model_falls_back_to_default_model(self) -> None:
        config = ProjectConfig(ai=AIConfig(default_model="my-default"))
        assert config.get_model("work") == "my-default"
        assert config.get_model("plan") == "my-default"
        assert config.get_model() == "my-default"

    def test_get_model_returns_none_when_unset(self) -> None:
        config = ProjectConfig()
        assert config.get_model("work") is None
        assert config.get_model() is None

    def test_get_complexity_model(self) -> None:
        config = ProjectConfig(
            models={
                "copilot": ComplexityModelMapping(
                    easy="claude-haiku-4.5",
                    complex="claude-sonnet-4.6",
                )
            }
        )
        assert config.get_complexity_model("copilot", "easy") == "claude-haiku-4.5"
        assert config.get_complexity_model("copilot", "complex") == "claude-sonnet-4.6"
        assert config.get_complexity_model("copilot", "medium") is None
        assert config.get_complexity_model("unknown", "easy") is None
