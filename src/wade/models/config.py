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
from pydantic import BaseModel, Field, StrictInt

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
    # Codex sandbox network policy for this command. ``None`` means "unset — fall
    # through to the global default"; a bool pins it explicitly. Only Codex acts
    # on it (it forwards to crossby's launch-time network pin); other tools
    # capability-gate it upstream. See :meth:`ProjectConfig.get_network_access`.
    network_access: bool | None = None
    enabled: bool | None = None
    timeout: int | None = None


AI_COMMAND_NAMES: tuple[str, ...] = (
    "plan",
    "deps",
    "implement",
    "review_plan",
    "review_implementation",
    "review_batch",
    "review_pr_comments",
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
    # Global Codex sandbox network policy (default disabled). ``None`` here is
    # the unset state that :meth:`ProjectConfig.get_network_access` resolves to
    # ``False`` — network is off unless a project opts in. Wade always passes an
    # explicit pin at launch so ambient Codex ``config.toml`` can never silently
    # enable network for a wade-managed sandbox.
    network_access: bool | None = None
    plan: AICommandConfig = AICommandConfig()
    deps: AICommandConfig = AICommandConfig()
    implement: AICommandConfig = AICommandConfig()
    review_plan: AICommandConfig = AICommandConfig()
    review_implementation: AICommandConfig = AICommandConfig()
    review_batch: AICommandConfig = AICommandConfig()
    review_pr_comments: AICommandConfig = AICommandConfig()


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


class PreCommitConfig(BaseModel):
    """``hooks.pre_commit`` — commands run by the managed ``pre-commit`` git hook.

    Both default to unset, so **nothing is installed unless opted in**. When
    either is set, a per-worktree ``pre-commit`` hook runs the configured
    command(s); a non-zero exit blocks the commit. This is a **quality** gate,
    not an enforcement boundary — ``git commit --no-verify`` bypasses it.
    """

    lint: str | None = None
    test: str | None = None


class CommitMsgConfig(BaseModel):
    """``hooks.commit_msg`` — the managed ``commit-msg`` git hook.

    ``conventional: true`` installs a per-worktree ``commit-msg`` hook that
    rejects a subject line which is not a Conventional Commit. Off by default;
    bypassable with ``git commit --no-verify``.
    """

    conventional: bool = False


class PostToolUseConfig(BaseModel):
    """``hooks.post_tool_use`` — in-turn lint feedback fed to context-capable tools.

    Off by default. When ``enabled`` and a lint command is resolvable, a
    PostToolUse hook lints the just-edited file and injects any findings back to
    the agent as context so it can fix them while the edit is still in working
    memory. ``lint_cmd`` is a **file-scoped** linter (the edited path is appended
    as a positional arg); when unset, wade falls back to ``pre_commit.lint`` run
    **unscoped** (whole-repo). ``timeout`` bounds the per-edit run (skip on
    overrun). Named ``lint_cmd`` — not ``lint`` — to avoid a string-vs-boolean
    key collision with :attr:`PreCommitConfig.lint`.
    """

    enabled: bool = False
    lint_cmd: str | None = None
    timeout: int = 10


class HooksConfig(BaseModel):
    """Hooks configuration for worktree lifecycle."""

    post_worktree_create: str | None = None
    copy_to_worktree: list[str] = []
    pre_commit: PreCommitConfig = PreCommitConfig()
    commit_msg: CommitMsgConfig = CommitMsgConfig()
    post_tool_use: PostToolUseConfig = PostToolUseConfig()


class DoneConfig(BaseModel):
    """Completion-gate toggles for the session ``done`` command (#349).

    Every gate defaults **on** — enforcing a complete workflow is the point of
    the ``done`` gate. Each field is an escape hatch a project can flip off in
    ``.wade.yml`` when a gate does not fit its flow:

    - ``require_pr_summary`` — refuse when ``PR-SUMMARY.md`` is missing, empty, or
      still a template placeholder (implementation sessions).
    - ``require_sync`` — auto-sync a branch behind main, refuse only on conflict
      (implementation sessions).
    - ``require_review`` — refuse unless ``wade review implementation`` ran for
      the current sha (both session types).
    - ``require_resolved_threads`` — refuse on unresolved PR review threads
      (review-pr-comments sessions).
    - ``require_conventional_title`` — refuse when the issue title is not a
      conventional-commit title, and sync an open PR's title to the (validated)
      issue title so a corrected title reaches the PR (both session types). The
      PR title is derived from the issue title verbatim, so this is what keeps
      the ``PR Title Lint`` CI check green.
    - ``pre_push_backstop`` — install the per-worktree pre-push git hook that
      refuses a push lacking a current ``.wade/done@<sha>`` marker.
    - ``max_review_passes`` — cap on the review→fix→re-review loop for
      **implementation sessions** (#384). Once this many distinct commits have
      been reviewed without an exact-sha ``reviewed`` marker for the current tip,
      ``done`` completes anyway (with a notice) rather than looping forever. A
      **strict** positive integer — ``0``/negative *and* non-int YAML scalars
      (``true``, ``"2"``, ``2.0``) are rejected at load, not coerced (a plain
      ``PositiveInt`` would silently accept ``true``→1, ``"2"``→2, ``2.0``→2).
    """

    require_pr_summary: bool = True
    require_sync: bool = True
    require_review: bool = True
    require_resolved_threads: bool = True
    require_conventional_title: bool = True
    pre_push_backstop: bool = True
    max_review_passes: StrictInt = Field(default=2, gt=0)


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
    done: DoneConfig = DoneConfig()

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

    def get_network_access(self, command: str | None = None) -> bool:
        """Resolve the Codex sandbox network policy for a command.

        Fallback: command-specific ``ai.<command>.network_access`` → global
        ``ai.network_access`` → ``False``. Network is **disabled by default**;
        an explicit ``True`` at either level opts in. Only Codex honors this
        (crossby capability-gates every other tool), and wade always forwards
        the resolved bool so ambient Codex config can never silently flip it on.
        """
        if command:
            cmd_config = getattr(self.ai, command, None)
            if isinstance(cmd_config, AICommandConfig) and cmd_config.network_access is not None:
                return cmd_config.network_access
        if self.ai.network_access is not None:
            return self.ai.network_access
        return False
