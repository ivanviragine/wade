"""Configuration domain models — ProjectConfig and nested sections.

Matches the v2 .ghaiw.yml format:

    version: 2
    project:
      main_branch: main
      issue_label: feature-plan
      ...
    ai:
      default_tool: copilot
      plan:
        tool: claude
        model: ""
      ...
    models:
      copilot:
        easy: claude-haiku-4.5
        ...
    provider:
      name: github
    hooks:
      post_worktree_create: scripts/setup-worktree.sh
      copy_to_worktree:
        - .env
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from ghaiw.models.work import MergeStrategy


class ProviderID(StrEnum):
    """Canonical identifiers for task providers."""

    GITHUB = "github"
    LINEAR = "linear"
    ASANA = "asana"
    TRELLO = "trello"
    CLICKUP = "clickup"
    JIRA = "jira"


class ComplexityModelMapping(BaseModel):
    """Model IDs for each complexity tier.

    Values are exact model IDs as returned by the tool's get_models().
    Defaults are None — populated at init time by querying the tool.
    """

    easy: str | None = None
    medium: str | None = None
    complex: str | None = None
    very_complex: str | None = None


class ProviderConfig(BaseModel):
    """Provider-specific configuration."""

    name: ProviderID = ProviderID.GITHUB
    project: str | None = None
    api_token_env: str | None = None


class AICommandConfig(BaseModel):
    """Per-command AI tool and model override."""

    tool: str | None = None
    model: str | None = None


class AIConfig(BaseModel):
    """AI tool configuration section."""

    default_tool: str | None = None
    default_model: str | None = None
    plan: AICommandConfig = AICommandConfig()
    deps: AICommandConfig = AICommandConfig()
    work: AICommandConfig = AICommandConfig()


class HooksConfig(BaseModel):
    """Hooks configuration for worktree lifecycle."""

    post_worktree_create: str | None = None
    copy_to_worktree: list[str] = []


class ProjectSettings(BaseModel):
    """Core project settings section."""

    main_branch: str | None = None
    issue_label: str = "feature-plan"
    worktrees_dir: str = "../.worktrees"
    branch_prefix: str = "feat"
    merge_strategy: MergeStrategy = MergeStrategy.PR


class ProjectConfig(BaseModel):
    """Full project configuration from .ghaiw.yml (v2 format).

    This is the validated, structured representation. The config loader
    parses the YAML file and constructs this model.
    """

    version: int = 2

    project: ProjectSettings = ProjectSettings()
    ai: AIConfig = AIConfig()
    models: dict[str, ComplexityModelMapping] = {}
    provider: ProviderConfig = ProviderConfig()
    hooks: HooksConfig = HooksConfig()

    # Resolved values (set after loading, not in YAML)
    config_path: str | None = Field(default=None, exclude=True)
    project_root: str | None = Field(default=None, exclude=True)

    def get_ai_tool(self, command: str | None = None) -> str | None:
        """Get the AI tool for a command, with fallback chain.

        Fallback: command-specific tool → global default_tool → None.
        """
        if command:
            cmd_config = getattr(self.ai, command, None)
            if isinstance(cmd_config, AICommandConfig) and cmd_config.tool:
                return cmd_config.tool
        return self.ai.default_tool

    def get_model(self, command: str | None = None) -> str | None:
        """Get the model for a command, with fallback chain.

        Fallback: command-specific model → ai.default_model → None.
        """
        if command:
            cmd_config = getattr(self.ai, command, None)
            if isinstance(cmd_config, AICommandConfig) and cmd_config.model:
                return cmd_config.model
        return self.ai.default_model

    def get_complexity_model(self, tool: str, complexity: str) -> str | None:
        """Get model ID for a tool + complexity combination."""
        mapping = self.models.get(tool)
        if mapping:
            return getattr(mapping, complexity, None)
        return None
