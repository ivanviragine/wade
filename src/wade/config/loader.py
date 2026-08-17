"""Configuration loader — find + parse .wade.yml (walk up from CWD)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

from wade.models.config import (
    AI_COMMAND_NAMES,
    WADE_BASE_ALLOWLIST_PATTERN,
    AICommandConfig,
    AIConfig,
    BotReviewConfig,
    CommitMsgConfig,
    ComplexityModelMapping,
    DoneConfig,
    HooksConfig,
    KnowledgeConfig,
    PermissionsConfig,
    PostToolUseConfig,
    PreCommitConfig,
    ProjectConfig,
    ProjectSettings,
    ProviderConfig,
    ReviewBotConfig,
)
from wade.models.permission import coerce_permission_mode
from wade.models.session import MergeStrategy

logger = structlog.get_logger()

CONFIG_FILENAME = ".wade.yml"


def _validated_permission_mode(raw_value: Any, *, source: str) -> str | None:
    """Validate a raw config ``permission_mode`` value, warning on invalid input.

    ``plan`` and any unknown value warn and fall back to ``None`` (treated as
    unset), so an invalid config never silently misbehaves. Valid values are
    returned as their canonical string form.
    """
    if raw_value is None:
        return None
    mode = coerce_permission_mode(str(raw_value))
    if mode is None:
        logger.warning(
            "config.invalid_permission_mode",
            value=raw_value,
            source=source,
            fallback="default",
        )
        return None
    return mode.value


def _migrate_merge_strategy(raw_value: Any) -> MergeStrategy:
    """Migrate the retired ``direct`` merge strategy to ``PR`` on load.

    The ``direct`` strategy was removed in #357. An existing config that still
    carries it is silently upgraded to ``PR`` (with a warning) so the project
    keeps working instead of failing to load. Any other value goes through the
    normal enum validation — an invalid value raises ``ValueError``, which
    :func:`parse_config_file` surfaces as a ``ConfigError``.
    """
    if raw_value is None:
        return MergeStrategy.PR
    value = str(raw_value)
    if value.strip().lower() == "direct":
        logger.warning(
            "config.merge_strategy_direct_retired",
            value=raw_value,
            fallback="PR",
        )
        return MergeStrategy.PR
    return MergeStrategy(value)


class ConfigError(Exception):
    """Raised when .wade.yml cannot be parsed or has invalid structure."""


def ensure_yaml_mapping(raw: Any) -> dict[str, Any] | None:
    """Validate that parsed YAML is a dict (mapping).

    Returns:
        The dict if raw is a dict, None if raw is None (empty file).

    Raises:
        ConfigError: If raw is a non-dict, non-None value (list, scalar).
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    raise ConfigError("Config must be a YAML mapping (key: value pairs)")


def find_config_file(start: Path | None = None) -> Path | None:
    """Walk up from start (or CWD) looking for .wade.yml.

    Returns the path to the config file, or None if not found.
    """
    current = (start or Path.cwd()).resolve()

    while True:
        candidate = current / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break  # Reached filesystem root
        current = parent

    return None


def load_config(start: Path | None = None) -> ProjectConfig:
    """Find and parse the project config.

    Returns a ProjectConfig with defaults if no config file exists.
    """
    config_path = find_config_file(start)
    if config_path is None:
        return ProjectConfig()

    return parse_config_file(config_path)


def parse_config_file(config_path: Path) -> ProjectConfig:
    """Parse a .wade.yml file into a ProjectConfig."""
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {config_path}: {e}") from e

    validated = ensure_yaml_mapping(raw)
    if validated is None:
        # Empty file — treated as defaults
        return ProjectConfig(
            config_path=str(config_path),
            project_root=str(config_path.parent),
        )

    try:
        return _build_config(validated, config_path)
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(f"Invalid config structure in {config_path}: {e}") from e


def _section_mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a top-level section as a mapping or raise a structural error."""
    section = raw.get(key)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise TypeError(f"{key} must be a mapping")
    return section


def _parse_tier_value(v: Any) -> tuple[str | None, str | None]:
    """Parse a complexity-tier value into (model, effort).

    Accepts both the legacy string form and the new ``{model, effort}`` dict form.
    """
    if v is None:
        return None, None
    if isinstance(v, str):
        return v or None, None
    if isinstance(v, dict):
        model = v.get("model") or None
        effort = v.get("effort") or None
        return model, effort
    return None, None


def _build_config(raw: dict[str, Any], config_path: Path) -> ProjectConfig:
    """Build a ProjectConfig from raw YAML dict."""
    version = raw.get("version", 2)

    # Parse project section
    project_raw = _section_mapping(raw, "project")
    project = ProjectSettings(
        main_branch=project_raw.get("main_branch"),
        issue_label=project_raw.get("issue_label", "feature-plan"),
        worktrees_dir=project_raw.get("worktrees_dir", "../.worktrees"),
        branch_prefix=project_raw.get("branch_prefix", "feat"),
        merge_strategy=_migrate_merge_strategy(project_raw.get("merge_strategy")),
    )

    # Parse ai section
    ai_raw = _section_mapping(raw, "ai")
    command_configs = {cmd: _parse_command_config(ai_raw.get(cmd, {})) for cmd in AI_COMMAND_NAMES}
    # Back-compat: accept legacy `ai.work` as the implement override.
    command_configs["implement"] = _parse_command_config(
        ai_raw.get("implement") if "implement" in ai_raw else ai_raw.get("work", {})
    )
    ai = AIConfig(
        default_tool=ai_raw.get("default_tool"),
        default_model=ai_raw.get("default_model"),
        effort=ai_raw.get("effort"),
        permission_mode=_validated_permission_mode(
            ai_raw.get("permission_mode"), source="ai.permission_mode"
        ),
        yolo=ai_raw.get("yolo"),
        network_access=ai_raw.get("network_access"),
        **command_configs,
    )

    # Parse models section (nested: tool → complexity → model/effort)
    models_raw = _section_mapping(raw, "models")
    models: dict[str, ComplexityModelMapping] = {}
    for tool_name, mapping_raw in models_raw.items():
        if isinstance(mapping_raw, dict):
            em, ee = _parse_tier_value(mapping_raw.get("easy"))
            mm, me = _parse_tier_value(mapping_raw.get("medium"))
            cm, ce = _parse_tier_value(mapping_raw.get("complex"))
            vm, ve = _parse_tier_value(mapping_raw.get("very_complex"))
            models[tool_name] = ComplexityModelMapping(
                easy=em,
                easy_effort=ee,
                medium=mm,
                medium_effort=me,
                complex=cm,
                complex_effort=ce,
                very_complex=vm,
                very_complex_effort=ve,
            )

    # Parse provider section
    provider_raw = _section_mapping(raw, "provider")
    provider_settings = provider_raw.get("settings") or {}
    if not isinstance(provider_settings, dict):
        raise TypeError("provider.settings must be a mapping")
    provider = ProviderConfig(
        name=provider_raw.get("name", "github"),
        project=provider_raw.get("project"),
        api_token_env=provider_raw.get("api_token_env"),
        settings=provider_settings,
    )

    permissions_raw = _section_mapping(raw, "permissions")
    allowed_commands = permissions_raw.get("allowed_commands")
    if allowed_commands is not None and not isinstance(allowed_commands, list):
        raise TypeError("permissions.allowed_commands must be a list")
    permissions = PermissionsConfig(
        allowed_commands=allowed_commands
        if allowed_commands is not None
        else [WADE_BASE_ALLOWLIST_PATTERN],
    )

    # Parse hooks section. The three nested quality-gate subsections
    # (pre_commit / commit_msg / post_tool_use) all default off, mirroring the
    # `done` section's null-normalization for booleans so a key present-but-null
    # falls back to the documented default rather than crashing Pydantic here
    # (`wade check` still flags an explicit null as invalid).
    hooks_raw = _section_mapping(raw, "hooks")

    def _subsection(key: str) -> dict[str, Any]:
        value = hooks_raw.get(key)
        return value if isinstance(value, dict) else {}

    pre_commit_raw = _subsection("pre_commit")
    pre_commit = PreCommitConfig(
        lint=pre_commit_raw.get("lint") or None,
        test=pre_commit_raw.get("test") or None,
    )

    commit_msg_raw = _subsection("commit_msg")
    _conventional = commit_msg_raw.get("conventional", False)
    commit_msg = CommitMsgConfig(conventional=False if _conventional is None else _conventional)

    post_tool_use_raw = _subsection("post_tool_use")
    _ptu_enabled = post_tool_use_raw.get("enabled", False)
    _ptu_timeout = post_tool_use_raw.get("timeout", 10)
    post_tool_use = PostToolUseConfig(
        enabled=False if _ptu_enabled is None else _ptu_enabled,
        lint_cmd=post_tool_use_raw.get("lint_cmd") or None,
        timeout=10 if _ptu_timeout is None else _ptu_timeout,
    )

    hooks = HooksConfig(
        post_worktree_create=hooks_raw.get("post_worktree_create"),
        copy_to_worktree=hooks_raw.get("copy_to_worktree", []),
        pre_commit=pre_commit,
        commit_msg=commit_msg,
        post_tool_use=post_tool_use,
    )

    # Parse knowledge section
    knowledge_raw = _section_mapping(raw, "knowledge")
    knowledge = KnowledgeConfig(
        enabled=knowledge_raw.get("enabled", False),
        path=knowledge_raw.get("path", "KNOWLEDGE.md"),
    )

    # Parse done section (completion gates). All gates default on. A key present
    # but null (e.g. `require_sync:` with no value → None) is normalized to the
    # default so DoneConfig's non-optional bool fields never receive None, which
    # would raise a cryptic Pydantic error here. `wade check` still flags an
    # explicit null as invalid via _validate_done_section.
    done_raw = _section_mapping(raw, "done")

    def _done_flag(key: str) -> Any:
        value = done_raw.get(key, True)
        return True if value is None else value

    # `max_review_passes` is an int (default 2), not a bool — it must NOT use
    # `_done_flag` (which normalizes null to the bool default `True`). An explicit
    # null normalizes to the documented default 2; any other value is passed
    # through raw so a bad one (0 / -1 / bool / str / float) fails loudly at
    # DoneConfig construction via its *strict* positive-int bound rather than
    # being coerced (a plain PositiveInt would accept `true`/`"2"`/`2.0`).
    _max_passes = done_raw.get("max_review_passes", 2)
    if _max_passes is None:
        _max_passes = 2

    done = DoneConfig(
        require_pr_summary=_done_flag("require_pr_summary"),
        require_sync=_done_flag("require_sync"),
        require_review=_done_flag("require_review"),
        require_resolved_threads=_done_flag("require_resolved_threads"),
        require_conventional_title=_done_flag("require_conventional_title"),
        pre_push_backstop=_done_flag("pre_push_backstop"),
        max_review_passes=_max_passes,
    )

    # Parse bot_review section (#431). Optional and fully model-defaulted: an
    # absent section loads the built-in CodeRabbit/Codex/Bugbot defaults (no
    # config-version migration needed). `_build_config` is hand-rolled per
    # section, so a new Pydantic model alone is not parsed — this explicit block
    # is what makes the section take effect.
    bot_review_raw = _section_mapping(raw, "bot_review")
    bot_review = _parse_bot_review(bot_review_raw)

    return ProjectConfig(
        version=version,
        project=project,
        ai=ai,
        models=models,
        provider=provider,
        permissions=permissions,
        hooks=hooks,
        knowledge=knowledge,
        done=done,
        bot_review=bot_review,
        config_path=str(config_path),
        project_root=str(config_path.parent),
    )


def _parse_review_bot(raw: Any) -> ReviewBotConfig:
    """Parse one ``bot_review.bots`` entry into a :class:`ReviewBotConfig`.

    ``name`` / ``trigger`` are required, non-empty strings — a missing or blank
    one raises (caught by ``parse_config_file`` and surfaced as a
    ``ConfigError``). A null ``enabled`` normalizes to the ``True`` default
    rather than crashing the model.
    """
    if not isinstance(raw, dict):
        raise TypeError("bot_review.bots entries must be mappings")
    name = raw.get("name")
    trigger = raw.get("trigger")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("each bot_review.bots entry requires a non-empty string `name`")
    if not isinstance(trigger, str) or not trigger.strip():
        raise ValueError("each bot_review.bots entry requires a non-empty string `trigger`")
    enabled = raw.get("enabled", True)
    return ReviewBotConfig(
        name=name,
        trigger=trigger,
        enabled=True if enabled is None else enabled,
    )


def _parse_bot_review(raw: dict[str, Any]) -> BotReviewConfig:
    """Parse the ``bot_review`` section into a :class:`BotReviewConfig`.

    An empty/absent section keeps the model defaults (auto-trigger off, the three
    built-in bots). A present section overrides ``auto_trigger`` and, when it
    supplies an explicit ``bots`` list, replaces the defaults wholesale; an
    omitted ``bots`` list keeps the built-in bots. A null ``auto_trigger``
    normalizes to the ``False`` default.
    """
    if not raw:
        return BotReviewConfig()
    auto_trigger = raw.get("auto_trigger", False)
    auto_trigger = False if auto_trigger is None else auto_trigger
    bots_raw = raw.get("bots")
    if bots_raw is None:
        return BotReviewConfig(auto_trigger=auto_trigger)
    if not isinstance(bots_raw, list):
        raise TypeError("bot_review.bots must be a list")
    bots = [_parse_review_bot(entry) for entry in bots_raw]
    return BotReviewConfig(auto_trigger=auto_trigger, bots=bots)


def _parse_command_config(raw: dict[str, Any] | None) -> AICommandConfig:
    """Parse a per-command AI config section."""
    if not raw or not isinstance(raw, dict):
        return AICommandConfig()
    return AICommandConfig(
        tool=raw.get("tool"),
        model=raw.get("model") or None,  # Treat empty string as None
        mode=raw.get("mode"),
        effort=raw.get("effort"),
        permission_mode=_validated_permission_mode(
            raw.get("permission_mode"), source="ai.<command>.permission_mode"
        ),
        yolo=raw.get("yolo"),
        network_access=raw.get("network_access"),
        enabled=raw.get("enabled"),
        timeout=raw.get("timeout"),
    )
