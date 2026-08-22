"""Check service — worktree safety and config validation."""

from __future__ import annotations

import contextlib
import os
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any

import structlog
import yaml
from crossby.models.ai import AIToolID, EffortLevel
from pydantic import BaseModel, Field

from wade.config.loader import (
    ConfigError,
    ensure_yaml_mapping,
    find_config_file,
    parse_config_file,
)
from wade.git import repo
from wade.git.repo import GitError
from wade.models.config import (
    AI_COMMAND_NAMES,
    LEGACY_AI_COMMAND_ALIASES,
    AICommandConfig,
    AIConfig,
    CommitMsgConfig,
    DoneConfig,
    HooksConfig,
    PostToolUseConfig,
    PreCommitConfig,
    ProjectConfig,
    is_valid_bot_name,
)
from wade.models.delegation import DelegationMode
from wade.models.session import MergeStrategy
from wade.providers import registered_provider_names

logger = structlog.get_logger()


class CheckStatus(StrEnum):
    """Worktree check result."""

    IN_WORKTREE = "IN_WORKTREE"
    IN_MAIN_CHECKOUT = "IN_MAIN_CHECKOUT"
    NOT_IN_GIT_REPO = "NOT_IN_GIT_REPO"
    WORKTREE_GIT_BLOCKED = "WORKTREE_GIT_BLOCKED"


class CheckExitCode(IntEnum):
    """Exit codes for wade check."""

    IN_WORKTREE = 0
    NOT_IN_GIT_REPO = 1
    IN_MAIN_CHECKOUT = 2
    # A linked worktree whose out-of-root git metadata (private and/or common
    # git dir) is not writable — e.g. an AI session launched into a sandbox that
    # never granted those dirs as writable roots. The tree looks like a worktree,
    # but git writes (index, refs, objects) would fail. Distinct exit code so the
    # agent can react. (3 is free in run_check's 0/1/2 space; handle_sync_result
    # uses 4 but that is a different command's exit space — no collision.)
    WORKTREE_GIT_BLOCKED = 3


class ConfigExitCode(IntEnum):
    """Exit codes for wade check-config."""

    VALID = 0
    NOT_FOUND = 1
    INVALID = 3


class CheckResult(BaseModel):
    """Result of a worktree safety check."""

    status: CheckStatus
    exit_code: int
    toplevel: str | None = None
    branch: str | None = None
    git_dir: str | None = None
    # Git-metadata dirs that failed the write probe (WORKTREE_GIT_BLOCKED only).
    blocked_paths: list[str] = Field(default_factory=list)

    def format_output(self) -> str:
        """Format as structured text output matching Bash behavior."""
        lines = [self.status.value]
        if self.toplevel is not None:
            lines.append(f"toplevel={self.toplevel}")
        if self.branch is not None:
            lines.append(f"branch={self.branch}")
        if self.git_dir is not None:
            lines.append(f"gitdir={self.git_dir}")
        for blocked in self.blocked_paths:
            lines.append(f"blocked={blocked}")
        if self.status == CheckStatus.WORKTREE_GIT_BLOCKED:
            lines.append(
                "error: git metadata is not writable — this linked worktree's "
                "private/common git dir lives outside the sandbox writable set, "
                "so git writes (index, refs, objects) and wade sync/done will fail."
            )
            lines.append(
                "hint: relaunch this AI session via `wade implement` / `wade review` "
                "on an up-to-date wade + crossby so Codex grants the git-metadata "
                "dirs as sandbox writable roots. Do not edit files until this passes."
            )
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


def _probe_dir_writable(dir_path: Path) -> bool:
    """Attempt a real, self-cleaning write inside *dir_path*.

    Creates and immediately deletes a uniquely-named probe file. Returns True
    only when the write succeeds; any ``OSError`` — including a sandbox denial,
    which surfaces even when the Unix mode bits report writable — returns False.
    A **real write** is required: ``os.access()`` alone is insufficient because
    OS sandbox policy (Codex Seatbelt/Landlock) denies the write while leaving
    the permission bits looking writable.

    The probe file is removed in a ``finally`` block so a partial success, a
    failed write, or an interruption never leaves an artefact behind.

    The file is created with ``O_NOFOLLOW`` so a pre-existing symlink planted at
    the probe path is refused (``ELOOP``) rather than followed — a real write
    must land in *dir_path* itself, never be redirected elsewhere. ``O_CREAT``
    without ``O_EXCL`` still truncates a stale regular probe from a crashed run
    with the same PID, so that never masquerades as an unwritable dir.

    ``O_NOFOLLOW`` is absent on Windows, so the flag is resolved via ``getattr``
    and degrades to ``0`` there (the no-follow guard is simply skipped) rather
    than raising ``AttributeError`` and breaking the check on every Windows
    linked-worktree session.
    """
    probe = dir_path / f".wade-write-probe-{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(probe, flags, 0o600)
    except OSError:
        return False
    try:
        os.write(fd, b"wade git-write readiness probe\n")
    except OSError:
        return False
    finally:
        # Always clean up — close the descriptor, then unlink the probe file.
        # Both are defensive: swallow a missing/denied removal or a double close.
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            probe.unlink()
    return True


def _blocked_git_metadata_dirs(path: Path) -> list[str]:
    """Return the out-of-root git-metadata dirs that are NOT writable.

    Probes the worktree's **private** git dir (``get_git_dir``) and the shared
    **common** git dir (``get_git_common_dir``) — the two roots a linked
    worktree's git writes (``index``/``index.lock``, refs, objects) target,
    which live outside the worktree tree. Relative results (git may return the
    common dir relative to *path*) are resolved against *path*, matching
    ``repo._index_lock_present``.

    Returns a sorted, de-duplicated list of blocked absolute paths (a
    non-existent dir counts as blocked so the failure surfaces rather than being
    silently skipped); empty when every resolvable root is writable.
    """
    candidates: list[Path] = []
    for getter in (repo.get_git_dir, repo.get_git_common_dir):
        raw = getter(path)
        if not raw:
            continue
        d = Path(raw)
        if not d.is_absolute():
            d = path / d
        candidates.append(d.resolve())

    seen: set[Path] = set()
    blocked: list[str] = []
    for d in candidates:
        # The private and common dir coincide in a plain checkout; probe once.
        if d in seen:
            continue
        seen.add(d)
        if not d.is_dir() or not _probe_dir_writable(d):
            blocked.append(str(d))
    return sorted(blocked)


def check_worktree(cwd: Path | None = None) -> CheckResult:
    """Check if the current directory is in a worktree.

    Returns CheckResult with status and exit code:
      0 / IN_WORKTREE          — safe for AI work
      1 / NOT_IN_GIT_REPO      — not inside any git repo
      2 / IN_MAIN_CHECKOUT     — in main checkout, only planning allowed
      3 / WORKTREE_GIT_BLOCKED — a linked worktree whose out-of-root git
                                 metadata is not writable (git writes would fail)
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

        # Readiness probe: a linked worktree's git metadata lives outside the
        # tree, so a sandbox that confines writes to the worktree (Codex
        # workspace-write) blocks every git write unless those dirs are granted
        # as writable roots. Verify with a real write, not just "is a worktree".
        blocked = _blocked_git_metadata_dirs(path)
        if blocked:
            logger.warning(
                "check.worktree_git_blocked",
                branch=branch,
                toplevel=toplevel,
                blocked=blocked,
            )
            return CheckResult(
                status=CheckStatus.WORKTREE_GIT_BLOCKED,
                exit_code=CheckExitCode.WORKTREE_GIT_BLOCKED,
                toplevel=toplevel,
                branch=branch,
                git_dir=git_dir,
                blocked_paths=blocked,
            )

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

# AI tools removed from wade, mapped to their recommended replacement. Used to
# turn the generic "invalid tool" error into an actionable migration hint when a
# project's .wade.yml still points at a tool that no longer exists in crossby.
_REMOVED_AI_TOOLS: dict[str, str] = {"gemini": "antigravity-cli"}


def _invalid_tool_message(field: str, tool: str) -> str:
    """Build the validation error for an unknown AI tool.

    Removed tools (e.g. ``gemini``) get an actionable "switch to X" hint instead
    of the generic list of valid tools.
    """
    replacement = _REMOVED_AI_TOOLS.get(tool)
    if replacement is not None:
        return (
            f"{field}: '{tool}' is no longer supported — {tool.capitalize()} CLI "
            f"support was removed. Switch to '{replacement}' or another supported tool."
        )
    return f"{field}: '{tool}' is invalid. Use one of: {', '.join(sorted(_VALID_AI_TOOLS))}"


def detect_removed_ai_tools(config: ProjectConfig) -> dict[str, str]:
    """Return ``{config location: replacement tool}`` for removed tools in config.

    Scans ``ai.default_tool``, each ``ai.<command>.tool`` override, and every
    ``models.<tool>`` key for tools that wade no longer supports. Empty when the
    config is clean.
    """
    found: dict[str, str] = {}

    default_tool = config.ai.default_tool
    if default_tool in _REMOVED_AI_TOOLS:
        found["ai.default_tool"] = _REMOVED_AI_TOOLS[default_tool]

    for cmd in AI_COMMAND_NAMES:
        cmd_config = getattr(config.ai, cmd, None)
        tool = getattr(cmd_config, "tool", None)
        if tool in _REMOVED_AI_TOOLS:
            found[f"ai.{cmd}.tool"] = _REMOVED_AI_TOOLS[tool]

    for tool_name in config.models:
        if tool_name in _REMOVED_AI_TOOLS:
            found[f"models.{tool_name}"] = _REMOVED_AI_TOOLS[tool_name]

    return found


# Valid effort levels
_VALID_EFFORT_LEVELS = {e.value for e in EffortLevel}

# Valid delegation modes
_VALID_DELEGATION_MODES = {m.value for m in DelegationMode}

# Valid merge strategies
_VALID_MERGE_STRATEGIES = {s.value for s in MergeStrategy}

# Valid complexity keys in the models section
_VALID_COMPLEXITY_KEYS = {"easy", "medium", "complex", "very_complex"}

# Valid keys for the AI config sections, derived from the Pydantic models so the
# validator's allowlists can't drift from the schema (issue #368). Deriving
# these — rather than hand-maintaining literal sets — means any field later
# added to ``AICommandConfig`` / ``AIConfig`` is accepted automatically.
_VALID_AI_COMMAND_KEYS = frozenset(AICommandConfig.model_fields)
# Top-level ``ai`` scalar keys are AIConfig's fields minus the per-command
# subsections, which are validated separately as their own sections.
_VALID_AI_SCALAR_KEYS = frozenset(AIConfig.model_fields) - set(AI_COMMAND_NAMES)
# Full accepted key set for the top-level ``ai`` section: scalar keys plus the
# per-command subsections (canonical names + legacy aliases). Precomputed once
# rather than rebuilt on every _validate_ai_section call.
_VALID_AI_TOP_LEVEL_KEYS = frozenset(
    {*_VALID_AI_SCALAR_KEYS, *AI_COMMAND_NAMES, *LEGACY_AI_COMMAND_ALIASES}
)

# Valid keys for the ``done`` section, derived from the Pydantic model so the
# validator can't drift from the schema (per knowledge ca245d6a — config-key
# validity lives in three places; deriving keeps them in sync automatically).
_VALID_DONE_KEYS = frozenset(DoneConfig.model_fields)

# Valid keys for the ``hooks`` section and its quality-gate subsections, all
# derived from their Pydantic models for the same reason (#352 added
# pre_commit / commit_msg / post_tool_use; hand-maintaining these sets is exactly
# the drift knowledge ca245d6a warns about).
_VALID_HOOKS_KEYS = frozenset(HooksConfig.model_fields)
_VALID_PRE_COMMIT_KEYS = frozenset(PreCommitConfig.model_fields)
_VALID_COMMIT_MSG_KEYS = frozenset(CommitMsgConfig.model_fields)
_VALID_POST_TOOL_USE_KEYS = frozenset(PostToolUseConfig.model_fields)


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
            _validate_knowledge_section(knowledge, config_path, errors)

    # Validate done section
    done = raw.get("done")
    if done is not None:
        if not isinstance(done, dict):
            errors.append("done: must be a mapping")
        else:
            _validate_done_section(done, errors)

    # Validate bot_review section
    bot_review = raw.get("bot_review")
    if bot_review is not None:
        if not isinstance(bot_review, dict):
            errors.append("bot_review: must be a mapping")
        else:
            _validate_bot_review_section(bot_review, errors)

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
        "done",
        "bot_review",
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
    if merge is not None and str(merge).strip().lower() == "direct":
        errors.append(
            "project.merge_strategy: 'direct' is retired (#357). Change it to 'PR' "
            "— wade migrates it automatically on load, but check-config rejects it."
        )
    elif merge is not None and str(merge) not in _VALID_MERGE_STRATEGIES:
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
        errors.append(_invalid_tool_message("ai.default_tool", str(default_tool)))

    effort = ai.get("effort")
    if effort is not None and str(effort) not in _VALID_EFFORT_LEVELS:
        errors.append(
            f"ai.effort: '{effort}' is invalid. "
            f"Use one of: {', '.join(sorted(_VALID_EFFORT_LEVELS))}"
        )

    yolo = ai.get("yolo")
    if yolo is not None and not isinstance(yolo, bool):
        errors.append("ai.yolo: must be true or false")

    network_access = ai.get("network_access")
    if network_access is not None and not isinstance(network_access, bool):
        errors.append("ai.network_access: must be true or false")

    # Validate per-command sections
    seen_sections: dict[str, str] = {}
    for cmd in (*AI_COMMAND_NAMES, *LEGACY_AI_COMMAND_ALIASES):
        cmd_section = ai.get(cmd)
        if cmd_section is not None:
            canonical_cmd = LEGACY_AI_COMMAND_ALIASES.get(cmd, cmd)
            previous_key = seen_sections.get(canonical_cmd)
            if previous_key is not None and previous_key != cmd:
                errors.append(
                    f"ai.{cmd}: duplicates ai.{previous_key}. "
                    f"Use only one section for '{canonical_cmd}'"
                )
                continue
            seen_sections[canonical_cmd] = cmd
            if not isinstance(cmd_section, dict):
                errors.append(f"ai.{cmd}: must be a mapping")
            else:
                _validate_ai_command_section(cmd, cmd_section, errors)

    for key in ai:
        if key not in _VALID_AI_TOP_LEVEL_KEYS:
            errors.append(f"ai.{key}: unsupported key")


def _validate_ai_command_section(cmd: str, cmd_section: dict[str, Any], errors: list[str]) -> None:
    """Validate one per-command AI config subsection."""
    tool = cmd_section.get("tool")
    if tool is not None and str(tool) and str(tool) not in _VALID_AI_TOOLS:
        errors.append(_invalid_tool_message(f"ai.{cmd}.tool", str(tool)))

    mode = cmd_section.get("mode")
    if mode is not None and str(mode) not in _VALID_DELEGATION_MODES:
        errors.append(
            f"ai.{cmd}.mode: '{mode}' is invalid. "
            f"Use one of: {', '.join(sorted(_VALID_DELEGATION_MODES))}"
        )

    effort = cmd_section.get("effort")
    if effort is not None and str(effort) not in _VALID_EFFORT_LEVELS:
        errors.append(
            f"ai.{cmd}.effort: '{effort}' is invalid. "
            f"Use one of: {', '.join(sorted(_VALID_EFFORT_LEVELS))}"
        )

    enabled = cmd_section.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append(f"ai.{cmd}.enabled: must be true or false")

    yolo = cmd_section.get("yolo")
    if yolo is not None and not isinstance(yolo, bool):
        errors.append(f"ai.{cmd}.yolo: must be true or false")

    network_access = cmd_section.get("network_access")
    if network_access is not None and not isinstance(network_access, bool):
        errors.append(f"ai.{cmd}.network_access: must be true or false")

    timeout = cmd_section.get("timeout")
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0
    ):
        errors.append(f"ai.{cmd}.timeout: must be a positive integer")

    for key in cmd_section:
        if key not in _VALID_AI_COMMAND_KEYS:
            errors.append(f"ai.{cmd}.{key}: unsupported key")


def _validate_models_section(models: dict[str, Any], errors: list[str]) -> None:
    """Validate the models section (per-tool complexity mappings)."""
    if not models:
        errors.append("models: block is empty. Add at least one tool section or remove the key")
        return

    for tool_name, mapping in models.items():
        if str(tool_name) not in _VALID_AI_TOOLS:
            errors.append(_invalid_tool_message(f"models.{tool_name}", str(tool_name)))
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
    """Validate the hooks section (including the #352 quality-gate subsections)."""
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

    _validate_hooks_subsection(
        hooks.get("pre_commit"),
        section="hooks.pre_commit",
        valid_keys=_VALID_PRE_COMMIT_KEYS,
        string_keys=("lint", "test"),
        bool_keys=(),
        positive_int_keys=(),
        errors=errors,
    )
    _validate_hooks_subsection(
        hooks.get("commit_msg"),
        section="hooks.commit_msg",
        valid_keys=_VALID_COMMIT_MSG_KEYS,
        string_keys=(),
        bool_keys=("conventional",),
        positive_int_keys=(),
        errors=errors,
    )
    _validate_hooks_subsection(
        hooks.get("post_tool_use"),
        section="hooks.post_tool_use",
        valid_keys=_VALID_POST_TOOL_USE_KEYS,
        string_keys=("lint_cmd",),
        bool_keys=("enabled",),
        positive_int_keys=("timeout",),
        errors=errors,
    )

    # Derived from HooksConfig.model_fields so a field added to the model is
    # accepted automatically — no hand-maintained literal set to drift (#368).
    for key in hooks:
        if key not in _VALID_HOOKS_KEYS:
            errors.append(f"hooks.{key}: unsupported key")


def _validate_hooks_subsection(
    section_raw: Any,
    *,
    section: str,
    valid_keys: frozenset[str],
    string_keys: tuple[str, ...],
    bool_keys: tuple[str, ...],
    positive_int_keys: tuple[str, ...],
    errors: list[str],
) -> None:
    """Validate one nested ``hooks.*`` quality-gate subsection.

    A ``None`` section (key absent, or present-but-null → treated as "unset,
    use defaults") is skipped; any other non-mapping value is an error. String
    keys must be non-empty strings, bool keys booleans, and positive-int keys
    positive integers (rejecting ``bool``, which is an ``int`` subclass).
    """
    if section_raw is None:
        return
    if not isinstance(section_raw, dict):
        errors.append(f"{section}: must be a mapping")
        return

    for key in string_keys:
        value = section_raw.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"{section}.{key}: must be a non-empty string command")

    for key in bool_keys:
        value = section_raw.get(key)
        if value is not None and not isinstance(value, bool):
            errors.append(f"{section}.{key}: must be true or false")

    for key in positive_int_keys:
        value = section_raw.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            errors.append(f"{section}.{key}: must be a positive integer")

    for key in section_raw:
        if key not in valid_keys:
            errors.append(f"{section}.{key}: unsupported key")


def _validate_knowledge_section(
    knowledge: dict[str, Any], config_path: Path, errors: list[str]
) -> None:
    """Validate the project knowledge section."""
    enabled = knowledge.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append("knowledge.enabled: must be a boolean (true or false)")

    path_value = knowledge.get("path")
    if path_value is not None:
        if not isinstance(path_value, str):
            errors.append("knowledge.path: must be a string")
        elif not path_value.strip():
            errors.append("knowledge.path: must be a non-empty relative path")
        else:
            root = config_path.parent.resolve()
            candidate = Path(path_value)
            if candidate.is_absolute():
                errors.append("knowledge.path: must be inside the project root")
            else:
                resolved = (root / path_value).resolve()
                if not resolved.is_relative_to(root):
                    errors.append("knowledge.path: must be inside the project root")

    valid_keys = {"enabled", "path"}
    for key in knowledge:
        if key not in valid_keys:
            errors.append(f"knowledge.{key}: unsupported key")


def _validate_done_section(done: dict[str, Any], errors: list[str]) -> None:
    """Validate the ``done`` completion-gate section.

    Every field is a boolean gate toggle **except** ``max_review_passes``, which
    is a positive int (#384). Iterating ``done.items()`` only sees keys the user
    explicitly wrote, so an explicit null (``require_sync:`` with no value) is a
    user mistake, not an "unset" default — reject it. This keeps `wade check`
    aligned with the loader, which normalizes such a null to the documented
    default rather than crashing.
    """
    for key, value in done.items():
        if key not in _VALID_DONE_KEYS:
            errors.append(
                f"done.{key}: unsupported key. "
                f"Supported keys: {', '.join(sorted(_VALID_DONE_KEYS))}"
            )
        elif key == "max_review_passes":
            # Reject bool explicitly — it is an int subclass, so a bare
            # `isinstance(value, int)` would wrongly accept `true`/`false`.
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors.append(f"done.{key}: must be a positive integer")
        elif not isinstance(value, bool):
            errors.append(f"done.{key}: must be true or false")


def _validate_bot_review_section(bot_review: dict[str, Any], errors: list[str]) -> None:
    """Validate the ``bot_review`` external-bot trigger section (#431).

    ``auto_trigger`` / ``offer_on_done`` are booleans; ``bots`` is a list of
    ``{name, trigger, enabled?}`` mappings with non-empty string ``name`` /
    ``trigger``. Mirrors the loader's parse rules so ``wade check`` catches a
    malformed section before it reaches ``load_config``.
    """
    for flag in ("auto_trigger", "offer_on_done"):
        value = bot_review.get(flag)
        if value is not None and not isinstance(value, bool):
            errors.append(f"bot_review.{flag}: must be true or false")

    # arrival_timeout / ack_timeout (#448): strict positive integers (reject bool,
    # which is an int subclass) mirroring the ``BotReviewConfig`` StrictInt fields.
    for key in ("arrival_timeout", "ack_timeout"):
        value = bot_review.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            errors.append(f"bot_review.{key}: must be a positive integer")
    arrival_timeout = bot_review.get("arrival_timeout")
    ack_timeout = bot_review.get("ack_timeout")
    if (
        isinstance(arrival_timeout, int)
        and not isinstance(arrival_timeout, bool)
        and isinstance(ack_timeout, int)
        and not isinstance(ack_timeout, bool)
        and ack_timeout < arrival_timeout
    ):
        errors.append("bot_review.ack_timeout: must be >= arrival_timeout")

    bots = bot_review.get("bots")
    if bots is not None:
        if not isinstance(bots, list):
            errors.append("bot_review.bots: must be a list")
        else:
            _validate_bot_review_bots(bots, errors)

    valid_keys = {"auto_trigger", "offer_on_done", "arrival_timeout", "ack_timeout", "bots"}
    supported = ", ".join(sorted(valid_keys))
    for key in bot_review:
        if key not in valid_keys:
            errors.append(f"bot_review.{key}: unsupported key. Supported keys: {supported}")


def _validate_bot_review_bots(bots: list[Any], errors: list[str]) -> None:
    """Validate each entry of ``bot_review.bots`` (#431).

    Mirrors the ``ReviewBotConfig`` / ``BotReviewConfig`` model invariants so
    ``wade check-config`` reports a bad section before it reaches ``load_config``:

    - Names must be unique — ``--bot`` selection and the per-bot auto-trigger
      marker both key off ``name``, so a duplicate would silently collide (post
      twice / share one marker).
    - Names must be a safe identifier (``[A-Za-z0-9._-]``) — ``name`` becomes a
      ``.wade/`` marker-file component, so separators / traversal are rejected.
    """
    valid_bot_keys = {"name", "trigger", "enabled"}
    seen_names: set[str] = set()
    for i, entry in enumerate(bots):
        if not isinstance(entry, dict):
            errors.append(f"bot_review.bots[{i}]: must be a mapping")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"bot_review.bots[{i}].name: must be a non-empty string")
        else:
            if not is_valid_bot_name(name):
                errors.append(
                    f"bot_review.bots[{i}].name: '{name}' is invalid — use only letters, "
                    "digits, '.', '_', '-' (no path separators or spaces)"
                )
            if name in seen_names:
                errors.append(
                    f"bot_review.bots[{i}].name: '{name}' is duplicated (names must be unique)"
                )
            seen_names.add(name)
        trigger = entry.get("trigger")
        if not isinstance(trigger, str) or not trigger.strip():
            errors.append(f"bot_review.bots[{i}].trigger: must be a non-empty string")
        enabled = entry.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            errors.append(f"bot_review.bots[{i}].enabled: must be true or false")
        for key in entry:
            if key not in valid_bot_keys:
                errors.append(f"bot_review.bots[{i}].{key}: unsupported key")
