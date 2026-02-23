"""Tests for configuration models."""

from __future__ import annotations

from ghaiw.models.config import (
    AICommandConfig,
    AIConfig,
    ComplexityModelMapping,
    ProjectConfig,
    ProjectSettings,
)
from ghaiw.models.work import MergeStrategy


class TestProjectConfig:
    def test_defaults(self) -> None:
        config = ProjectConfig()
        assert config.version == 2
        assert config.project.merge_strategy == MergeStrategy.PR
        assert config.project.issue_label == "feature-plan"
        assert config.project.branch_prefix == "feat"
        assert config.ai.default_tool is None
        assert config.models == {}

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
