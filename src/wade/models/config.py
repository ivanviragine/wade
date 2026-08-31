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

import re
from enum import StrEnum

from crossby.models.config import ComplexityModelMapping as ComplexityModelMapping
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from wade.models.permission import PermissionMode, coerce_permission_mode
from wade.models.session import MergeStrategy
from wade.models.skill import SkillRef
from wade.models.workflow import AICommandKey

CONFIG_SCHEMA_VERSION = 2

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


AI_COMMAND_NAMES: tuple[str, ...] = tuple(command.value for command in AICommandKey)
"""Canonical per-command AI config sections supported by WADE."""


NETWORK_ENABLED_BY_DEFAULT_COMMANDS: frozenset[str] = frozenset(
    {
        AICommandKey.IMPLEMENT,
        AICommandKey.REVIEW_PR_COMMENTS,
    }
)
"""Interactive Codex session commands whose required lifecycle needs network access."""


LEGACY_AI_COMMAND_ALIASES: dict[str, str] = {"work": "implement"}
"""Back-compat aliases accepted in config validation/loading paths."""


class AIConfig(BaseModel):
    """AI tool configuration section."""

    default_tool: str | None = None
    default_model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    yolo: bool | None = None
    # Global Codex sandbox network policy. ``None`` defers to the command's
    # built-in default: enabled for interactive implementation and PR-comment
    # sessions, disabled for every other command. Wade always passes an explicit
    # pin at launch so ambient Codex ``config.toml`` can never silently change
    # a wade-managed sandbox.
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


class ProjectSkillDiscoveryConfig(BaseModel):
    """Read-only discovery policy for project-owned Agent Skills."""

    model_config = ConfigDict(extra="forbid")

    discover: bool = True
    include: tuple[str, ...] = ("*",)
    exclude: tuple[str, ...] = ()

    @field_validator("include", "exclude")
    @classmethod
    def _patterns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not isinstance(pattern, str) or not pattern.strip() for pattern in value):
            raise ValueError("Skill include/exclude patterns must be non-empty strings")
        return value


class SkillsConfig(BaseModel):
    """Top-level project-skill discovery configuration."""

    model_config = ConfigDict(extra="forbid")

    project: ProjectSkillDiscoveryConfig = Field(default_factory=ProjectSkillDiscoveryConfig)


class SessionSkillConfig(BaseModel):
    """Optional session-slot overrides; ``None`` means use normal precedence."""

    model_config = ConfigDict(extra="forbid")

    work: tuple[SkillRef, ...] | None = None
    review: tuple[SkillRef, ...] | None = None

    @field_validator("work", "review")
    @classmethod
    def _nonempty_binding(cls, value: tuple[SkillRef, ...] | None) -> tuple[SkillRef, ...] | None:
        if value == ():
            raise ValueError("Configured skill bindings cannot be empty")
        return value


class SessionConfig(BaseModel):
    """Configuration for one interactive session."""

    model_config = ConfigDict(extra="forbid")

    skills: SessionSkillConfig = Field(default_factory=SessionSkillConfig)


class SessionsConfig(BaseModel):
    """Interactive session binding configuration.

    ``deps`` is intentionally absent: dependency methodology is configured only
    through ``delegations.dependency_analysis``.
    """

    model_config = ConfigDict(extra="forbid")

    plan: SessionConfig = Field(default_factory=SessionConfig)
    implementation: SessionConfig = Field(default_factory=SessionConfig)
    review_pr_comments: SessionConfig = Field(default_factory=SessionConfig)


class DelegationSkillConfig(BaseModel):
    """Optional bounded-delegation work binding override."""

    model_config = ConfigDict(extra="forbid")

    work: tuple[SkillRef, ...] | None = None

    @field_validator("work")
    @classmethod
    def _nonempty_binding(cls, value: tuple[SkillRef, ...] | None) -> tuple[SkillRef, ...] | None:
        if value == ():
            raise ValueError("Configured skill bindings cannot be empty")
        return value


class DelegationConfig(BaseModel):
    """Configuration for one bounded delegation."""

    model_config = ConfigDict(extra="forbid")

    skills: DelegationSkillConfig = Field(default_factory=DelegationSkillConfig)


class DelegationsConfig(BaseModel):
    """All bounded-delegation binding configuration."""

    model_config = ConfigDict(extra="forbid")

    plan_review: DelegationConfig = Field(default_factory=DelegationConfig)
    code_review: DelegationConfig = Field(default_factory=DelegationConfig)
    batch_review: DelegationConfig = Field(default_factory=DelegationConfig)
    dependency_analysis: DelegationConfig = Field(default_factory=DelegationConfig)


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


# A bot ``name`` is a CLI-selectable identifier (`--bot <name>`) that is also
# interpolated into the ``.wade/bot-triggered-<name>@<sha>`` marker filename, so
# it must stay a safe path component: letters, digits, ``.``, ``_``, ``-`` only.
# This keeps marker files confined to ``.wade/`` (no separators / traversal) and
# rules out Rich-markup control tokens in the name.
_SAFE_BOT_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")


def is_valid_bot_name(name: str) -> bool:
    """True if *name* is a safe bot identifier (see :data:`_SAFE_BOT_NAME_RE`)."""
    return bool(name) and _SAFE_BOT_NAME_RE.fullmatch(name) is not None


class ReviewBotConfig(BaseModel):
    """One external review bot and the PR comment that triggers it (#431).

    ``name`` is the stable identifier used by ``--bot`` selection and the
    per-bot auto-trigger marker; ``trigger`` is the exact comment body posted to
    the PR to (re-)invoke that bot (e.g. ``"@coderabbitai review"``). ``enabled``
    gates the default (no ``--bot``) trigger path; an explicit ``--bot <name>``
    overrides it.

    ``name`` is constrained to a safe identifier (``[A-Za-z0-9._-]+``): it becomes
    a ``.wade/`` marker-file component, so path separators / traversal are
    rejected to keep markers confined to that directory (and, as a side effect,
    a name can never carry a Rich-markup control token).
    """

    name: str
    trigger: str
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("bot_review.bots entry requires a non-empty string `name`")
        if not is_valid_bot_name(v):
            raise ValueError(
                f"bot_review.bots name '{v}' is invalid — use only letters, digits, "
                "'.', '_', '-' (no path separators, spaces, or other characters)"
            )
        return v


def _default_review_bots() -> list[ReviewBotConfig]:
    """The built-in bot-review triggers shipped as defaults (#431).

    Produced by a factory (not a shared module-level list) so every
    :class:`BotReviewConfig` gets its own instances — a plain mutable default
    would share one list across all ``ProjectConfig`` instances (a Pydantic
    footgun). CodeRabbit / Codex / Bugbot ship enabled so the feature works out
    of the box even when ``.wade.yml`` has no ``bot_review:`` section.
    """
    return [
        ReviewBotConfig(name="coderabbit", trigger="@coderabbitai review"),
        ReviewBotConfig(name="codex", trigger="@codex review"),
        ReviewBotConfig(name="bugbot", trigger="bugbot run"),
    ]


class BotReviewConfig(BaseModel):
    """``bot_review`` — external-bot review-trigger configuration (#431).

    Deliberately distinct from the ``ai.review_*`` blocks (``review_plan``,
    ``review_implementation``, ``review_batch``, ``review_pr_comments``): those
    configure wade's own AI-tool reviews, whereas these are external-bot trigger
    strings posted as PR comments. Keeping the name separate stops the two from
    reading confusingly in ``.wade.yml``.

    ``auto_trigger`` is opt-in: when ``True``, ``done`` posts the enabled bots'
    triggers after a successful push. The three built-in bots ship as defaults
    (via :func:`_default_review_bots`) so ``wade review trigger`` works with no
    config; every field is overridable.

    ``offer_on_done`` (#464) governs the middle ground between "post every time"
    and "never": with ``auto_trigger: False`` (the default), a session's ``done``
    — and the post-session menus — **offer** the triggers instead of leaving the
    user to remember ``wade review trigger <N>``. Interactively that is a
    confirm/menu entry; in a non-TTY agent session ``done`` prints the offer so
    the agent can put it in its closing dialog. Set it to ``False`` for repos
    whose bots already auto-review every push, where a trigger comment is
    redundant noise. The three states, in precedence order (a ``done``
    ``--trigger-bots`` / ``--no-trigger-bots`` flag overrides all of them):

    ===================  =================  ==================================
    ``auto_trigger``     ``offer_on_done``  behavior after ``done`` pushes
    ===================  =================  ==================================
    ``True``             (ignored)          posts the enabled bots' triggers
    ``False``            ``True``           offers them (default)
    ``False``            ``False``          silent — trigger manually
    ===================  =================  ==================================

    ``arrival_timeout`` / ``ack_timeout`` bound how long review completion waits
    for an expected bot (#448). WADE refuses to report all-clear while an enabled
    bot has not posted a review covering HEAD; ``arrival_timeout`` (seconds) caps
    that wait, after which the bot stops blocking and is reported as missing. A bot
    that *acknowledges* with a reaction (👀/+1) gets the longer ``ack_timeout``
    ceiling instead. **Latency note:** because all three bots ship enabled, a repo
    without one installed adds up to ``arrival_timeout`` to every session before
    proceeding with a "no review from X" note — disable an unused bot
    (``enabled: false``) to remove its floor. ``ack_timeout`` must be
    ``>= arrival_timeout``.

    Bot ``name`` values must be unique — ``--bot`` selection and the per-bot
    auto-trigger marker both key off ``name``, so a duplicate would silently
    collide (post twice / share one marker). Enforcing it here makes the invariant
    hold for every construction path (``load_config`` and direct instantiation
    alike), not only under ``wade check-config``.
    """

    auto_trigger: bool = False
    offer_on_done: bool = True
    arrival_timeout: StrictInt = Field(default=300, gt=0)
    ack_timeout: StrictInt = Field(default=900, gt=0)
    bots: list[ReviewBotConfig] = Field(default_factory=_default_review_bots)

    @model_validator(mode="after")
    def _validate_unique_names(self) -> BotReviewConfig:
        seen: set[str] = set()
        for bot in self.bots:
            if bot.name in seen:
                raise ValueError(
                    f"bot_review.bots: duplicate name '{bot.name}' (names must be unique)"
                )
            seen.add(bot.name)
        return self

    @model_validator(mode="after")
    def _validate_timeout_ordering(self) -> BotReviewConfig:
        if self.ack_timeout < self.arrival_timeout:
            raise ValueError(
                "bot_review.ack_timeout must be >= arrival_timeout "
                f"(got ack_timeout={self.ack_timeout}, arrival_timeout={self.arrival_timeout})"
            )
        return self


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

    version: int = CONFIG_SCHEMA_VERSION

    project: ProjectSettings = ProjectSettings()
    ai: AIConfig = AIConfig()
    models: dict[str, ComplexityModelMapping] = {}
    provider: ProviderConfig = ProviderConfig()
    permissions: PermissionsConfig = PermissionsConfig()
    hooks: HooksConfig = HooksConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    delegations: DelegationsConfig = Field(default_factory=DelegationsConfig)
    done: DoneConfig = DoneConfig()
    bot_review: BotReviewConfig = Field(default_factory=BotReviewConfig)

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
        ``ai.network_access`` → command default. Interactive implementation and
        PR-comment sessions default to enabled because their required lifecycle
        reaches GitHub; all other commands are always disabled. Only Codex
        honors this (crossby capability-gates every other tool), and wade always
        forwards the resolved bool so ambient Codex config can never silently
        flip it on.
        """
        if command and command not in NETWORK_ENABLED_BY_DEFAULT_COMMANDS:
            return False
        if command:
            cmd_config = getattr(self.ai, command, None)
            if isinstance(cmd_config, AICommandConfig) and cmd_config.network_access is not None:
                return cmd_config.network_access
        if self.ai.network_access is not None:
            return self.ai.network_access
        return command in NETWORK_ENABLED_BY_DEFAULT_COMMANDS
