"""Configuration domain models — ProjectConfig and nested sections.

Matches the v2 .wade.yml format:

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
      name: github   # or "clickup" or "markdown"
    permissions:
      allowed_commands:
        - "wade *"
    hooks:
      post_worktree_create: scripts/setup-worktree.sh
      copy_to_worktree:
        - .env
"""

from __future__ import annotations

from enum import StrEnum

from crossby.models.config import ComplexityModelMapping as ComplexityModelMapping
from pydantic import BaseModel, Field

from wade.models.permission import PermissionMode, coerce_permission_mode
from wade.models.session import MergeStrategy

# wade's own command pattern — the base allowlist entry that must always be
# pre-authorized so agents can run ``wade ...`` without manual approval.
WADE_BASE_ALLOWLIST_PATTERN = "wade *"


def _level_permission_mode(permission_mode: str | None, yolo: bool | None) -> PermissionMode | None:
    """Resolve one config level's autonomy setting (``permission_mode`` or yolo).

    ``permission_mode`` wins over the ``yolo`` alias at the same level. Returns
    ``None`` when neither is set (or ``permission_mode`` is invalid), so the
    caller falls through to the next level.
    """
    if permission_mode is not None:
        return coerce_permission_mode(permission_mode)
    if yolo is not None:
        return PermissionMode.YOLO if yolo else PermissionMode.DEFAULT
    return None


def with_wade_base_pattern(patterns: list[str]) -> list[str]:
    """Return *patterns* with wade's base allowlist pattern guaranteed present.

    crossby's permission writers are generic and inject no app-specific base
    pattern, so wade guarantees ``wade *`` itself wherever it hands an allowlist
    to those writers (worktree bootstrap and launch-time delegation alike).
    """
    if WADE_BASE_ALLOWLIST_PATTERN in patterns:
        return patterns
    return [WADE_BASE_ALLOWLIST_PATTERN, *patterns]


class ProviderID(StrEnum):
    """Canonical identifiers for task providers."""

    GITHUB = "github"
    CLICKUP = "clickup"
    MARKDOWN = "markdown"


class ProviderConfig(BaseModel):
    """Provider-specific configuration."""

    name: ProviderID = ProviderID.GITHUB
    project: str | None = None
    api_token_env: str | None = None
    settings: dict[str, str] = {}


class AICommandConfig(BaseModel):
    """Per-command AI tool and model override."""

    tool: str | None = None
    model: str | None = None
    effort: str | None = None
    mode: str | None = None
    # Autonomy tier (default|accept-edits|auto|yolo). ``yolo`` below is a
    # back-compat alias; ``permission_mode`` wins when both are set.
    permission_mode: str | None = None
    yolo: bool | None = None
    enabled: bool | None = None
    timeout: int | None = None


AI_COMMAND_NAMES: tuple[str, ...] = (
    "plan",
    "deps",
    "implement",
    "review_plan",
    "review_implementation",
    "review_batch",
)
"""Canonical per-command AI config sections supported by WADE."""


LEGACY_AI_COMMAND_ALIASES: dict[str, str] = {"work": "implement"}
"""Back-compat aliases accepted in config validation/loading paths."""


class AIConfig(BaseModel):
    """AI tool configuration section."""

    default_tool: str | None = None
    default_model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    yolo: bool | None = None
    plan: AICommandConfig = AICommandConfig()
    deps: AICommandConfig = AICommandConfig()
    implement: AICommandConfig = AICommandConfig()
    review_plan: AICommandConfig = AICommandConfig()
    review_implementation: AICommandConfig = AICommandConfig()
    review_batch: AICommandConfig = AICommandConfig()


class PermissionsConfig(BaseModel):
    """Permission pre-authorization for AI tool sessions.

    Canonical command patterns (e.g. ``"wade *"``) are translated to
    tool-specific allowlist flags at launch time.
    """

    allowed_commands: list[str] = [WADE_BASE_ALLOWLIST_PATTERN]


class KnowledgeConfig(BaseModel):
    """Project knowledge file configuration."""

    enabled: bool = False
    path: str = "KNOWLEDGE.md"


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
    """Full project configuration from .wade.yml (v2 format).

    This is the validated, structured representation. The config loader
    parses the YAML file and constructs this model.
    """

    version: int = 2

    project: ProjectSettings = ProjectSettings()
    ai: AIConfig = AIConfig()
    models: dict[str, ComplexityModelMapping] = {}
    provider: ProviderConfig = ProviderConfig()
    permissions: PermissionsConfig = PermissionsConfig()
    hooks: HooksConfig = HooksConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()

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

    def get_complexity_effort(self, tool: str, complexity: str) -> str | None:
        """Get effort level for a tool + complexity combination."""
        mapping = self.models.get(tool)
        if mapping:
            return getattr(mapping, f"{complexity}_effort", None)
        return None

    def get_effort(self, command: str | None = None) -> str | None:
        """Get the effort level for a command, with fallback chain.

        Fallback: command-specific effort → global ai.effort → None.
        """
        if command:
            cmd_config = getattr(self.ai, command, None)
            if isinstance(cmd_config, AICommandConfig) and cmd_config.effort:
                return cmd_config.effort
        return self.ai.effort

    def get_permission_mode(self, command: str | None = None) -> PermissionMode | None:
        """Resolve the configured permission (autonomy) mode for a command.

        Fallback: command-specific → global. At each level an explicit
        ``permission_mode`` wins over the legacy ``yolo`` alias (``yolo: true``
        → ``yolo`` tier, ``yolo: false`` → ``default``). Invalid values are
        treated as unset here (they fall through); the loader emits the warning
        when parsing them.
        """
        if command:
            cmd_config = getattr(self.ai, command, None)
            if isinstance(cmd_config, AICommandConfig):
                mode = _level_permission_mode(cmd_config.permission_mode, cmd_config.yolo)
                if mode is not None:
                    return mode
        return _level_permission_mode(self.ai.permission_mode, self.ai.yolo)

    def get_yolo(self, command: str | None = None) -> bool:
        """Whether the resolved permission mode for a command is ``yolo``.

        Derived from :meth:`get_permission_mode` so the yolo alias has a single
        source of truth.
        """
        return self.get_permission_mode(command) is PermissionMode.YOLO
