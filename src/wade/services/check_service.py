"""Check service — worktree safety and config validation."""

from __future__ import annotations

from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any

import structlog
import yaml

from wade.config.loader import (
    ConfigError,
    ensure_yaml_mapping,
    find_config_file,
    parse_config_file,
)
from wade.git import repo
from wade.git.repo import GitError
from wade.models.ai import AIToolID
from wade.models.session import MergeStrategy
from wade.providers import registered_provider_names

logger = structlog.get_logger()


class CheckStatus(StrEnum):
    """Worktree check result."""

    IN_WORKTREE = "IN_WORKTREE"
    IN_MAIN_CHECKOUT = "IN_MAIN_CHECKOUT"
    NOT_IN_GIT_REPO = "NOT_IN_GIT_REPO"


class CheckExitCode(IntEnum):
    """Exit codes for wade check."""

    IN_WORKTREE = 0
    NOT_IN_GIT_REPO = 1
    IN_MAIN_CHECKOUT = 2


class ConfigExitCode(IntEnum):
    """Exit codes for wade check-config."""

    VALID = 0
    NOT_FOUND = 1
    INVALID = 3


class CheckResult:
    """Result of a worktree safety check."""

    def __init__(
        self,
        status: CheckStatus,
        exit_code: int,
        toplevel: str | None = None,
        branch: str | None = None,
        git_dir: str | None = None,
    ) -> None:
        self.status = status
        self.exit_code = exit_code
        self.toplevel = toplevel
        self.branch = branch
        self.git_dir = git_dir

    def format_output(self) -> str:
        """Format as structured text output matching Bash behavior."""
        lines = [self.status.value]
        if self.toplevel is not None:
            lines.append(f"toplevel={self.toplevel}")
        if self.branch is not None:
            lines.append(f"branch={self.branch}")
        if self.git_dir is not None:
            lines.append(f"gitdir={self.git_dir}")
        return "\n".join(lines)


class ConfigCheckResult:
    """Result of a config validation check."""

    def __init__(
        self,
        exit_code: int,
        config_path: str | None = None,
        errors: list[str] | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.config_path = config_path
        self.errors = errors or []

    @property
    def is_valid(self) -> bool:
        return self.exit_code == ConfigExitCode.VALID

    def format_output(self) -> str:
        """Format as structured text output matching Bash behavior."""
        if self.exit_code == ConfigExitCode.NOT_FOUND:
            lines = ["CONFIG_NOT_FOUND"]
            lines.append("error: .wade.yml not found in current directory or parents")
            lines.append("hint: run 'wade init' to create a default config")
            return "\n".join(lines)

        if self.exit_code == ConfigExitCode.VALID:
            lines = ["VALID_CONFIG"]
            if self.config_path:
                lines.append(f"path={self.config_path}")
            return "\n".join(lines)

        # INVALID
        lines = ["INVALID_CONFIG"]
        if self.config_path:
            lines.append(f"path={self.config_path}")
        for error in self.errors:
            lines.append(f"error: {error}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Worktree check
# ---------------------------------------------------------------------------


def check_worktree(cwd: Path | None = None) -> CheckResult:
    """Check if the current directory is in a worktree.

    Returns CheckResult with status and exit code:
      0 / IN_WORKTREE       — safe for AI work
      1 / NOT_IN_GIT_REPO   — not inside any git repo
      2 / IN_MAIN_CHECKOUT  — in main checkout, only planning allowed
    """
    path = cwd or Path.cwd()

    if not repo.is_git_repo(path):
        logger.info("check.not_in_git_repo", path=str(path))
        return CheckResult(
            status=CheckStatus.NOT_IN_GIT_REPO,
            exit_code=CheckExitCode.NOT_IN_GIT_REPO,
        )

    try:
        toplevel = str(repo.get_repo_root(path))
    except GitError:
        toplevel = None

    try:
        branch = repo.get_current_branch(path)
    except GitError:
        branch = "DETACHED"

    if repo.is_worktree(path):
        git_dir = repo.get_git_dir(path)

        logger.info("check.in_worktree", branch=branch, toplevel=toplevel)
        return CheckResult(
            status=CheckStatus.IN_WORKTREE,
            exit_code=CheckExitCode.IN_WORKTREE,
            toplevel=toplevel,
            branch=branch,
            git_dir=git_dir,
        )

    logger.info("check.in_main_checkout", branch=branch, toplevel=toplevel)
    return CheckResult(
        status=CheckStatus.IN_MAIN_CHECKOUT,
        exit_code=CheckExitCode.IN_MAIN_CHECKOUT,
        toplevel=toplevel,
        branch=branch,
    )


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

# Valid AI tool names for config validation
_VALID_AI_TOOLS = {t.value for t in AIToolID}

# Valid merge strategies
_VALID_MERGE_STRATEGIES = {s.value for s in MergeStrategy}

# Valid complexity keys in the models section
_VALID_COMPLEXITY_KEYS = {"easy", "medium", "complex", "very_complex"}


def validate_config(cwd: Path | None = None) -> ConfigCheckResult:
    """Validate the project's .wade.yml config.

    Returns ConfigCheckResult with exit code:
      0 — valid config
      1 — config not found
      3 — invalid config with field-level errors
    """
    path = cwd or Path.cwd()

    config_path = find_config_file(path)
    if config_path is None:
        return ConfigCheckResult(exit_code=ConfigExitCode.NOT_FOUND)

    errors = _validate_config_file(config_path)

    if errors:
        return ConfigCheckResult(
            exit_code=ConfigExitCode.INVALID,
            config_path=str(config_path),
            errors=errors,
        )

    return ConfigCheckResult(
        exit_code=ConfigExitCode.VALID,
        config_path=str(config_path),
    )


def _validate_config_file(config_path: Path) -> list[str]:
    """Validate a config file and return a list of error messages.

    Uses YAML parsing + field-level validation (not Pydantic, to give
    precise error messages rather than Pydantic's generic ones).
    """
    errors: list[str] = []

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"cannot read config file: {e}"]

    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        return [f"YAML parse error: {e}"]

    try:
        validated = ensure_yaml_mapping(raw)
    except ConfigError:
        return ["config must be a YAML mapping (key: value pairs)"]

    if validated is None:
        # Empty file — treated as defaults, valid
        return []

    raw = validated

    # Validate version
    version = raw.get("version")
    if version is not None and version != 2:
        errors.append(f"version: '{version}' is invalid. Use: version: 2")

    # Validate project section
    project = raw.get("project")
    if project is not None:
        if not isinstance(project, dict):
            errors.append("project: must be a mapping")
        else:
            _validate_project_section(project, errors)

    # Validate ai section
    ai = raw.get("ai")
    if ai is not None:
        if not isinstance(ai, dict):
            errors.append("ai: must be a mapping")
        else:
            _validate_ai_section(ai, errors)

    # Validate models section
    models = raw.get("models")
    if models is not None:
        if not isinstance(models, dict):
            errors.append("models: must be a nested mapping")
        else:
            _validate_models_section(models, errors)

    # Validate provider section
    provider = raw.get("provider")
    if provider is not None:
        if not isinstance(provider, dict):
            errors.append("provider: must be a mapping")
        else:
            _validate_provider_section(provider, errors)

    # Validate permissions section
    permissions = raw.get("permissions")
    if permissions is not None:
        if not isinstance(permissions, dict):
            errors.append("permissions: must be a mapping")
        else:
            _validate_permissions_section(permissions, errors)

    # Validate hooks section
    hooks = raw.get("hooks")
    if hooks is not None:
        if not isinstance(hooks, dict):
            errors.append("hooks: must be a mapping")
        else:
            _validate_hooks_section(hooks, errors)

    # Validate knowledge section
    knowledge = raw.get("knowledge")
    if knowledge is not None:
        if not isinstance(knowledge, dict):
            errors.append("knowledge: must be a mapping")
        else:
            _validate_knowledge_section(knowledge, errors)

    # Check for unsupported top-level keys
    supported_keys = {
        "version",
        "project",
        "ai",
        "models",
        "provider",
        "permissions",
        "hooks",
        "knowledge",
    }
    for key in raw:
        if key not in supported_keys:
            errors.append(
                f"unsupported key '{key}'. Supported keys: {', '.join(sorted(supported_keys))}"
            )

    # Try to parse the full config to catch any remaining issues
    if not errors:
        try:
            parse_config_file(config_path)
        except Exception as e:
            errors.append(f"config parse error: {e}")

    return errors


def _validate_project_section(project: dict[str, Any], errors: list[str]) -> None:
    """Validate the project section."""
    merge = project.get("merge_strategy")
    if merge is not None and str(merge) not in _VALID_MERGE_STRATEGIES:
        errors.append(
            f"project.merge_strategy: '{merge}' is invalid. "
            f"Allowed values: {', '.join(sorted(_VALID_MERGE_STRATEGIES))}"
        )

    valid_keys = {
        "main_branch",
        "issue_label",
        "worktrees_dir",
        "branch_prefix",
        "merge_strategy",
    }
    for key in project:
        if key not in valid_keys:
            errors.append(
                f"project.{key}: unsupported key. Supported keys: {', '.join(sorted(valid_keys))}"
            )


def _validate_ai_section(ai: dict[str, Any], errors: list[str]) -> None:
    """Validate the ai section."""
    default_tool = ai.get("default_tool")
    if default_tool is not None and str(default_tool) and str(default_tool) not in _VALID_AI_TOOLS:
        errors.append(
            f"ai.default_tool: '{default_tool}' is invalid. "
            f"Use one of: {', '.join(sorted(_VALID_AI_TOOLS))}"
        )

    # Validate per-command sections
    for cmd in ("plan", "deps", "implement", "work", "review_plan", "review_implementation"):
        cmd_section = ai.get(cmd)
        if cmd_section is not None:
            if not isinstance(cmd_section, dict):
                errors.append(f"ai.{cmd}: must be a mapping")
            else:
                tool = cmd_section.get("tool")
                if tool is not None and str(tool) and str(tool) not in _VALID_AI_TOOLS:
                    errors.append(
                        f"ai.{cmd}.tool: '{tool}' is invalid. "
                        f"Use one of: {', '.join(sorted(_VALID_AI_TOOLS))}"
                    )

    valid_keys = {
        "default_tool",
        "default_model",
        "effort",
        "plan",
        "deps",
        "implement",
        "work",
        "review_plan",
        "review_implementation",
    }
    for key in ai:
        if key not in valid_keys:
            errors.append(f"ai.{key}: unsupported key")


def _validate_models_section(models: dict[str, Any], errors: list[str]) -> None:
    """Validate the models section (per-tool complexity mappings)."""
    if not models:
        errors.append("models: block is empty. Add at least one tool section or remove the key")
        return

    for tool_name, mapping in models.items():
        if str(tool_name) not in _VALID_AI_TOOLS:
            errors.append(
                f"models.{tool_name}: unsupported tool. "
                f"Use one of: {', '.join(sorted(_VALID_AI_TOOLS))}"
            )
            continue

        if not isinstance(mapping, dict):
            errors.append(f"models.{tool_name}: must be a mapping of complexity keys")
            continue

        for key, value in mapping.items():
            if key not in _VALID_COMPLEXITY_KEYS:
                errors.append(
                    f"models.{tool_name}.{key}: unsupported key. "
                    f"Allowed keys: {', '.join(sorted(_VALID_COMPLEXITY_KEYS))}"
                )
            elif not value:
                errors.append(f"models.{tool_name}.{key}: is empty. Set a model value")


def _validate_provider_section(provider: dict[str, Any], errors: list[str]) -> None:
    """Validate the provider section."""
    name = provider.get("name")
    valid_providers = registered_provider_names()
    if name is not None and str(name) not in valid_providers:
        errors.append(
            f"provider.name: '{name}' is not supported. "
            f"Supported: {', '.join(sorted(valid_providers))}"
        )

    settings = provider.get("settings")
    if settings is not None and not isinstance(settings, dict):
        errors.append("provider.settings: must be a mapping of key-value pairs")

    valid_keys = {"name", "project", "api_token_env", "settings"}
    for key in provider:
        if key not in valid_keys:
            errors.append(f"provider.{key}: unsupported key")


def _validate_permissions_section(permissions: dict[str, Any], errors: list[str]) -> None:
    """Validate the permissions section."""
    allowed = permissions.get("allowed_commands")
    if allowed is not None:
        if not isinstance(allowed, list):
            errors.append(
                "permissions.allowed_commands: must be a list. "
                "Use: allowed_commands: followed by '- <pattern>' items"
            )
        else:
            for i, item in enumerate(allowed):
                if not item or not str(item).strip():
                    errors.append(
                        f"permissions.allowed_commands[{i}]: item is empty. "
                        "Use: - <command pattern>"
                    )

    valid_keys = {"allowed_commands"}
    for key in permissions:
        if key not in valid_keys:
            errors.append(f"permissions.{key}: unsupported key")


def _validate_hooks_section(hooks: dict[str, Any], errors: list[str]) -> None:
    """Validate the hooks section."""
    copy_list = hooks.get("copy_to_worktree")
    if copy_list is not None:
        if not isinstance(copy_list, list):
            errors.append(
                "hooks.copy_to_worktree: must be a list. "
                "Use: copy_to_worktree: followed by '- <path>' items"
            )
        elif len(copy_list) == 0:
            errors.append(
                "hooks.copy_to_worktree: list is empty. "
                "Add at least one '- <path>' item or remove the key"
            )
        else:
            for i, item in enumerate(copy_list):
                if not item or not str(item).strip():
                    errors.append(
                        f"hooks.copy_to_worktree[{i}]: item is empty. Use: - <relative-path>"
                    )

    valid_keys = {"post_worktree_create", "copy_to_worktree"}
    for key in hooks:
        if key not in valid_keys:
            errors.append(f"hooks.{key}: unsupported key")


def _validate_knowledge_section(knowledge: dict[str, Any], errors: list[str]) -> None:
    """Validate the knowledge section."""
    enabled = knowledge.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append("knowledge.enabled: must be a boolean (true or false)")

    path = knowledge.get("path")
    if path is not None and not isinstance(path, str):
        errors.append("knowledge.path: must be a string")

    valid_keys = {"enabled", "path"}
    for key in knowledge:
        if key not in valid_keys:
            errors.append(f"knowledge.{key}: unsupported key")
