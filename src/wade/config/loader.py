"""Configuration loader — find + parse .wade.yml (walk up from CWD)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from wade.models.config import (
    AI_COMMAND_NAMES,
    AICommandConfig,
    AIConfig,
    ComplexityModelMapping,
    HooksConfig,
    PermissionsConfig,
    ProjectConfig,
    ProjectSettings,
    ProviderConfig,
)

CONFIG_FILENAME = ".wade.yml"


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
        merge_strategy=project_raw.get("merge_strategy", "PR"),
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
        yolo=ai_raw.get("yolo"),
        **command_configs,
    )

    # Parse models section (nested: tool → complexity → model)
    models_raw = _section_mapping(raw, "models")
    models: dict[str, ComplexityModelMapping] = {}
    for tool_name, mapping_raw in models_raw.items():
        if isinstance(mapping_raw, dict):
            models[tool_name] = ComplexityModelMapping(
                easy=mapping_raw.get("easy"),
                medium=mapping_raw.get("medium"),
                complex=mapping_raw.get("complex"),
                very_complex=mapping_raw.get("very_complex"),
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
        allowed_commands=allowed_commands if allowed_commands is not None else ["wade:*"],
    )

    # Parse hooks section
    hooks_raw = _section_mapping(raw, "hooks")
    hooks = HooksConfig(
        post_worktree_create=hooks_raw.get("post_worktree_create"),
        copy_to_worktree=hooks_raw.get("copy_to_worktree", []),
    )

    return ProjectConfig(
        version=version,
        project=project,
        ai=ai,
        models=models,
        provider=provider,
        permissions=permissions,
        hooks=hooks,
        config_path=str(config_path),
        project_root=str(config_path.parent),
    )


def _parse_command_config(raw: dict[str, Any] | None) -> AICommandConfig:
    """Parse a per-command AI config section."""
    if not raw or not isinstance(raw, dict):
        return AICommandConfig()
    return AICommandConfig(
        tool=raw.get("tool"),
        model=raw.get("model") or None,  # Treat empty string as None
        mode=raw.get("mode"),
        effort=raw.get("effort"),
        yolo=raw.get("yolo"),
        enabled=raw.get("enabled"),
    )
