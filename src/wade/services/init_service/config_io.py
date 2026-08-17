"""Init service config I/O — writing and patching ``.wade.yml``.

Serializes wizard selections into ``.wade.yml`` (fresh write and idempotent
patch), plus model-mapping normalization and the markdown-file bootstrap helper.
Leaf module — imports nothing from siblings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml
from crossby.config.defaults import get_defaults

from wade.config.loader import ConfigError, ensure_yaml_mapping
from wade.models.config import (
    AI_COMMAND_NAMES,
    BotReviewConfig,
    ComplexityModelMapping,
    KnowledgeConfig,
    ProjectSettings,
)
from wade.ui.console import console

logger = structlog.get_logger()

_COMMAND_OVERRIDE_NAMES = tuple(cmd for cmd in AI_COMMAND_NAMES if cmd != "implement")

__all__ = [
    "_COMMAND_OVERRIDE_NAMES",
    "_ensure_markdown_file",
    "_normalize_knowledge_setup",
    "_normalize_mapping",
    "_normalize_model",
    "_patch_config",
    "_resolve_models",
    "_tier_yaml_value",
    "_write_config",
]


def _resolve_models(tool: str | None) -> ComplexityModelMapping:
    """Resolve model mappings for a tool from hardcoded defaults.

    Returns:
        Normalized complexity-to-model mapping for the tool.
    """
    if not tool:
        return ComplexityModelMapping()
    # get_defaults() returns crossby's ComplexityModelMapping (no effort fields).
    # Convert to wade's local mapping so downstream code can attach per-tier effort.
    crossby_defaults = get_defaults(tool)
    wade_mapping = ComplexityModelMapping(
        easy=crossby_defaults.easy,
        medium=crossby_defaults.medium,
        complex=crossby_defaults.complex,
        very_complex=crossby_defaults.very_complex,
    )
    return _normalize_mapping(wade_mapping)


def _normalize_model(model_id: str | None) -> str | None:
    """Normalize a single model ID: fix deprecated names and ensure dot notation.

    Config always stores dot notation (claude-haiku-4.5). Tool-specific
    conversion (e.g., dashes for Claude CLI) happens at launch time.
    """
    if not model_id:
        return None
    import re

    # Ensure dot notation for Claude models (claude-haiku-4-5 → claude-haiku-4.5)
    if model_id.startswith("claude-"):
        model_id = re.sub(r"(\d)-(\d)", r"\1.\2", model_id)
    return model_id


def _normalize_mapping(
    mapping: ComplexityModelMapping,
) -> ComplexityModelMapping:
    """Normalize model IDs: fix deprecated names and standardize to dot notation.

    Config always stores dot notation. Tool-specific conversion happens at launch.
    """
    return ComplexityModelMapping(
        easy=_normalize_model(mapping.easy),
        medium=_normalize_model(mapping.medium),
        complex=_normalize_model(mapping.complex),
        very_complex=_normalize_model(mapping.very_complex),
        easy_effort=mapping.easy_effort,
        medium_effort=mapping.medium_effort,
        complex_effort=mapping.complex_effort,
        very_complex_effort=mapping.very_complex_effort,
    )


def _ensure_markdown_file(project_root: Path, settings: dict[str, Any]) -> None:
    """Create the markdown issues file with the default header if missing.

    Resolves the path through the same main-worktree resolver the provider
    uses at runtime, so running ``wade init`` from a linked worktree writes
    the file at the location the provider will later read from. Rejects
    paths that already exist as directories.

    Called from the init write phase (alongside the config write) so the
    wizard's "Modify" / "Cancel" paths don't leave stray files behind.
    """
    from wade.providers.markdown import (
        DEFAULT_FILE_HEADER,
        DEFAULT_FILE_NAME,
        _resolve_main_worktree,
    )

    raw = settings.get("path") or DEFAULT_FILE_NAME
    path_obj = Path(str(raw)).expanduser()
    if path_obj.is_absolute():
        md_path = path_obj
    else:
        anchor = _resolve_main_worktree(project_root) or project_root
        md_path = (anchor / path_obj).resolve()

    if md_path.exists():
        if not md_path.is_file():
            raise ValueError(f"Markdown issues path is not a regular file: {md_path}")
        return

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(DEFAULT_FILE_HEADER, encoding="utf-8")
    try:
        shown: Path | str = md_path.relative_to(project_root)
    except ValueError:
        shown = md_path
    console.success(f"Created {shown}")


def _normalize_knowledge_setup(
    project_root: Path,
    knowledge_setup: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate and canonicalize knowledge config before writing config."""
    if not knowledge_setup.get("enabled"):
        return knowledge_setup

    from wade.services.knowledge_service import resolve_knowledge_path

    path_value = str(knowledge_setup.get("path", "KNOWLEDGE.md")).strip() or "KNOWLEDGE.md"
    try:
        resolved = resolve_knowledge_path(
            project_root,
            KnowledgeConfig(enabled=True, path=path_value),
        )
    except ValueError as exc:
        console.error_with_fix(
            str(exc),
            "Use a relative file path inside the repository, for example "
            "KNOWLEDGE.md or docs/KNOWLEDGE.md",
        )
        return None

    normalized = resolved.relative_to(project_root.resolve()).as_posix()
    normalized_setup = dict(knowledge_setup)
    normalized_setup["path"] = normalized
    return normalized_setup


def _tier_yaml_value(model: str | None, effort: str | None) -> dict[str, Any] | None:
    """Return the YAML representation for a complexity tier.

    Always uses the structured ``{model, effort}`` form so the format is stable
    after migration from the legacy plain-string form.
    """
    if not model:
        return None
    return {"model": model, "effort": effort or None}


def _bot_review_config_dict(auto_trigger: bool) -> dict[str, Any]:
    """Build the ``bot_review`` YAML block: chosen auto_trigger + default bots (#431).

    The bots list is derived from :class:`BotReviewConfig`'s model defaults so the
    written block always matches the built-in CodeRabbit/Codex/Bugbot defaults —
    one source of truth, discoverable and fully overridable in ``.wade.yml``.
    """
    return {
        "auto_trigger": bool(auto_trigger),
        "bots": [bot.model_dump() for bot in BotReviewConfig().bots],
    }


def _write_config(
    config_path: Path,
    ai_tool: str | None,
    model_mapping: ComplexityModelMapping,
    project_settings: ProjectSettings | None = None,
    implement_tool: str | None = None,
    default_model: str | None = None,
    default_effort: str | None = None,
    default_yolo: bool | None = None,
    command_overrides: dict[str, dict[str, Any]] | None = None,
    hooks_setup: dict[str, Any] | None = None,
    provider_setup: dict[str, Any] | None = None,
    knowledge_setup: dict[str, Any] | None = None,
    bot_review_setup: dict[str, Any] | None = None,
) -> None:
    """Write a fresh .wade.yml config file."""
    config_dict: dict[str, Any] = {"version": 2}

    settings = project_settings or ProjectSettings()
    config_dict["project"] = {
        "main_branch": settings.main_branch or "main",
        "issue_label": settings.issue_label,
        "worktrees_dir": settings.worktrees_dir,
        "branch_prefix": settings.branch_prefix,
        "merge_strategy": settings.merge_strategy.value,
    }

    ai_section: dict[str, Any] = {}
    if ai_tool:
        ai_section["default_tool"] = str(ai_tool)
    if default_model:
        ai_section["default_model"] = default_model
    if default_effort:
        ai_section["effort"] = default_effort
    if default_yolo is not None:
        ai_section["yolo"] = default_yolo

    # Write implement tool override (only when different from default_tool)
    if implement_tool and implement_tool != ai_tool:
        ai_section["implement"] = {"tool": implement_tool}

    # Write per-command overrides.
    if command_overrides:
        for cmd_name in _COMMAND_OVERRIDE_NAMES:
            overrides = command_overrides.get(cmd_name, {})
            if overrides:
                cmd_section: dict[str, Any] = {}
                for key in ("tool", "model", "mode", "effort", "permission_mode"):
                    if overrides.get(key):
                        cmd_section[key] = overrides[key]
                # Handle boolean fields (stored as strings in overrides)
                if "enabled" in overrides:
                    cmd_section["enabled"] = overrides["enabled"] == "true"
                if "yolo" in overrides:
                    cmd_section["yolo"] = overrides["yolo"] == "true"
                if cmd_section:
                    ai_section[cmd_name] = cmd_section

    config_dict["ai"] = ai_section

    # models section keyed by implement_tool (or default tool)
    models_key = implement_tool or ai_tool
    has_any_model = any(
        getattr(model_mapping, k, None) for k in ("easy", "medium", "complex", "very_complex")
    )
    if models_key and has_any_model:
        config_dict["models"] = {
            str(models_key): {
                k: v
                for k, v in {
                    "easy": _tier_yaml_value(model_mapping.easy, model_mapping.easy_effort),
                    "medium": _tier_yaml_value(model_mapping.medium, model_mapping.medium_effort),
                    "complex": _tier_yaml_value(
                        model_mapping.complex, model_mapping.complex_effort
                    ),
                    "very_complex": _tier_yaml_value(
                        model_mapping.very_complex, model_mapping.very_complex_effort
                    ),
                }.items()
                if v
            }
        }

    # Build provider section from setup results
    provider_name = provider_setup.get("name", "github") if provider_setup else "github"
    provider_dict: dict[str, Any] = {"name": provider_name}
    if provider_setup:
        if provider_setup.get("api_token_env"):
            provider_dict["api_token_env"] = provider_setup["api_token_env"]
        if provider_setup.get("settings"):
            provider_dict["settings"] = provider_setup["settings"]
    config_dict["provider"] = provider_dict

    # Build hooks section (internal files like .wade.yml are auto-copied at runtime)
    hooks_dict: dict[str, Any] = {}
    if hooks_setup and hooks_setup.get("post_worktree_create"):
        hooks_dict["post_worktree_create"] = hooks_setup["post_worktree_create"]
    copy_files: list[str] = list(hooks_setup.get("copy_to_worktree", [])) if hooks_setup else []
    if copy_files:
        hooks_dict["copy_to_worktree"] = copy_files
    config_dict["hooks"] = hooks_dict

    # Build knowledge section
    if knowledge_setup and knowledge_setup.get("enabled"):
        config_dict["knowledge"] = {
            "enabled": True,
            "path": knowledge_setup.get("path", "KNOWLEDGE.md"),
        }

    # Build bot_review section (#431). Always written when the wizard ran so the
    # feature is discoverable and every field overridable, even though the models
    # default it safely when the block is absent.
    if bot_review_setup is not None:
        config_dict["bot_review"] = _bot_review_config_dict(
            bool(bot_review_setup.get("auto_trigger"))
        )

    config_path.write_text(
        yaml.dump(config_dict, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _patch_config(
    config_path: Path,
    ai_tool: str | None,
    model_mapping: ComplexityModelMapping,
    default_model: str | None = None,
    default_effort: str | None = None,
    default_yolo: bool | None = None,
    project_settings: ProjectSettings | None = None,
    implement_tool: str | None = None,
    command_overrides: dict[str, dict[str, Any]] | None = None,
    hooks_setup: dict[str, Any] | None = None,
    force: bool = False,
    provider_setup: dict[str, Any] | None = None,
    knowledge_setup: dict[str, Any] | None = None,
    bot_review_setup: dict[str, Any] | None = None,
) -> None:
    """Patch values into an existing config.

    When force=True, user-selected values overwrite existing entries.
    When force=False (default), only missing keys are filled in.
    """
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return

    try:
        validated = ensure_yaml_mapping(raw)
        raw = validated if validated is not None else {}
    except ConfigError:
        return

    changed = False

    # Ensure version
    if "version" not in raw:
        raw["version"] = 2
        changed = True

    # Patch project settings
    if project_settings:
        # mode="json" renders the merge_strategy enum as its "PR" string.
        dumped = project_settings.model_dump(mode="json")
        project = raw.get("project", {}) or {}
        for key in (
            "main_branch",
            "issue_label",
            "worktrees_dir",
            "branch_prefix",
            "merge_strategy",
        ):
            value = dumped.get(key)
            if value and (force or not project.get(key)):
                project[key] = value
                changed = True
        raw["project"] = project

    # Patch AI tool and default model
    ai = raw.get("ai", {}) or {}
    # Captured before the overwrite below: the implement-section patch further down
    # needs the *previous* default to tell whether the effective implement tool changed.
    old_default_tool = ai.get("default_tool")
    if ai_tool and (force or not ai.get("default_tool")):
        ai["default_tool"] = str(ai_tool)
        raw["ai"] = ai
        changed = True
    if default_model and (force or not ai.get("default_model")):
        ai["default_model"] = default_model
        raw["ai"] = ai
        changed = True
    if default_effort is not None:
        if default_effort == "":  # Sentinel: user explicitly cleared effort
            if force and "effort" in ai:
                del ai["effort"]
                raw["ai"] = ai
                changed = True
        elif force or not ai.get("effort"):
            ai["effort"] = default_effort
            raw["ai"] = ai
            changed = True
    if default_yolo is not None and (force or ai.get("yolo") is None):
        ai["yolo"] = default_yolo
        raw["ai"] = ai
        changed = True

    # Patch implement tool override. Manage ONLY the ``tool`` key: any other
    # implement-scoped keys a user set by hand (model / effort / mode /
    # permission_mode / yolo / enabled / timeout — all valid under ``ai.implement``
    # and honored by the loader) must survive a re-init. Replacing the section
    # wholesale or ``del``-ing it silently dropped them, so mutate a copy and keep
    # the section iff anything is left.
    if force:
        existing = ai.get("implement")
        section: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
        if implement_tool and implement_tool != ai_tool:
            section["tool"] = implement_tool
        else:
            section.pop("tool", None)
        # ``model`` and ``effort`` are the tool-specific keys here: resolve_model /
        # resolve_effort both give the command-scoped value precedence over the
        # freshly-written ``models.<tool>`` mapping, then reject it for a different
        # tool — an incompatible model or an unsupported effort (e.g. agy rejects
        # ``xhigh``) resolves to ``None`` — so a stale pin silently shadows the
        # re-init's choice. Drop both only on a *confirmed* effective-tool change;
        # portable keys (permission_mode / yolo / mode / enabled / timeout) survive.
        existing_tool = existing.get("tool") if isinstance(existing, dict) else None
        old_effective_tool = existing_tool or old_default_tool
        new_effective_tool = implement_tool or ai_tool
        if old_effective_tool and new_effective_tool and old_effective_tool != new_effective_tool:
            section.pop("model", None)
            section.pop("effort", None)
        if section:
            ai["implement"] = section
        elif "implement" in ai:
            del ai["implement"]
        if ai.get("implement") != existing:
            changed = True
        raw["ai"] = ai
    elif implement_tool and implement_tool != ai_tool and not ai.get("implement"):
        ai["implement"] = {"tool": implement_tool}
        raw["ai"] = ai
        changed = True

    # Patch command overrides.
    if command_overrides is not None:
        for cmd_name in _COMMAND_OVERRIDE_NAMES:
            overrides = command_overrides.get(cmd_name, {})
            if force:
                if overrides:
                    cmd_section: dict[str, Any] = {}
                    for key in ("tool", "model", "mode", "effort", "permission_mode"):
                        if overrides.get(key):
                            cmd_section[key] = overrides[key]
                    if "enabled" in overrides:
                        cmd_section["enabled"] = overrides["enabled"] == "true"
                    if "yolo" in overrides:
                        cmd_section["yolo"] = overrides["yolo"] == "true"
                    if cmd_section:
                        ai[cmd_name] = cmd_section
                        raw["ai"] = ai
                        changed = True
                elif cmd_name in ai:
                    del ai[cmd_name]
                    raw["ai"] = ai
                    changed = True
            elif overrides and not ai.get(cmd_name):
                cmd_section = {}
                for key in ("tool", "model", "mode", "effort", "permission_mode"):
                    if overrides.get(key):
                        cmd_section[key] = overrides[key]
                if "enabled" in overrides:
                    cmd_section["enabled"] = overrides["enabled"] == "true"
                if "yolo" in overrides:
                    cmd_section["yolo"] = overrides["yolo"] == "true"
                if cmd_section:
                    ai[cmd_name] = cmd_section
                    raw["ai"] = ai
                    changed = True

    # Patch models — keyed by implement_tool when provided (matching _write_config)
    tool_key = str(implement_tool or ai_tool) if (implement_tool or ai_tool) else None
    has_any_model = any(
        getattr(model_mapping, k, None) for k in ("easy", "medium", "complex", "very_complex")
    )
    if tool_key and has_any_model:
        models = raw.get("models", {}) or {}
        tool_models = models.get(tool_key, {}) or {}

        for tier in ("easy", "medium", "complex", "very_complex"):
            model_val = getattr(model_mapping, tier, None)
            effort_val = getattr(model_mapping, f"{tier}_effort", None)
            if model_val and (force or not tool_models.get(tier)):
                tool_models[tier] = _tier_yaml_value(model_val, effort_val)
                changed = True

        if tool_models:
            models[tool_key] = tool_models
            raw["models"] = models

    # Patch hooks — setup script and copy_to_worktree
    hooks = raw.get("hooks") or {}
    if hooks_setup:
        script = hooks_setup.get("post_worktree_create")
        if script and (force or not hooks.get("post_worktree_create")):
            hooks["post_worktree_create"] = script
            changed = True
        elif not script and force and "post_worktree_create" in hooks:
            del hooks["post_worktree_create"]
            changed = True
        user_files: list[str] = hooks_setup.get("copy_to_worktree", [])
        if user_files and (force or not hooks.get("copy_to_worktree")):
            existing_copy: list[str] = hooks.get("copy_to_worktree") or []
            for f in user_files:
                if f not in existing_copy:
                    existing_copy.append(f)
                    changed = True
            hooks["copy_to_worktree"] = existing_copy
    raw["hooks"] = hooks

    # Patch provider section
    if provider_setup:
        provider_raw = raw.get("provider")
        provider = provider_raw if isinstance(provider_raw, dict) else {}
        name = provider_setup.get("name")
        if name and (force or not provider.get("name")):
            old_name = provider.get("name")
            provider["name"] = name
            changed = True
            # When force-switching providers, clean up keys from the old provider
            if force and old_name != name:
                for orphan_key in ("api_token_env", "settings"):
                    if orphan_key not in provider_setup and orphan_key in provider:
                        del provider[orphan_key]
        # Only merge provider-specific fields when the effective provider matches
        effective_name = provider.get("name")
        if effective_name == provider_setup.get("name"):
            token_env = provider_setup.get("api_token_env")
            if token_env and (force or not provider.get("api_token_env")):
                provider["api_token_env"] = token_env
                changed = True
            settings = provider_setup.get("settings")
            if settings:
                settings_raw = provider.get("settings")
                existing_settings: dict[str, str] = (
                    settings_raw if isinstance(settings_raw, dict) else {}
                )
                for key, value in settings.items():
                    if value and (force or not existing_settings.get(key)):
                        existing_settings[key] = value
                        changed = True
                if existing_settings:
                    provider["settings"] = existing_settings
        raw["provider"] = provider

    # Patch knowledge section
    if knowledge_setup and knowledge_setup.get("enabled"):
        knowledge = raw.get("knowledge") or {}
        if force or not knowledge.get("enabled"):
            knowledge["enabled"] = True
            changed = True
        k_path = knowledge_setup.get("path", "KNOWLEDGE.md")
        if k_path and (force or not knowledge.get("path")):
            knowledge["path"] = k_path
            changed = True
        raw["knowledge"] = knowledge

    # Patch bot_review section (#431). Set auto_trigger from the wizard (force
    # overwrites; otherwise only fill when absent) and seed the default bots list
    # only when the key is entirely absent, so a re-init makes a fresh block
    # discoverable/overridable. A user-customized bots list — including a
    # deliberate empty ``bots: []`` (disable all triggers) — is never clobbered;
    # `"bots" not in section` (not a falsy check) is what preserves the empty list.
    if bot_review_setup is not None:
        existing_bot_review = raw.get("bot_review")
        section = existing_bot_review if isinstance(existing_bot_review, dict) else {}
        auto_trigger = bool(bot_review_setup.get("auto_trigger"))
        if (force or "auto_trigger" not in section) and section.get("auto_trigger") != auto_trigger:
            section["auto_trigger"] = auto_trigger
            changed = True
        if "bots" not in section:
            section["bots"] = [bot.model_dump() for bot in BotReviewConfig().bots]
            changed = True
        raw["bot_review"] = section

    if changed:
        config_path.write_text(
            yaml.dump(raw, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("config.patched", path=str(config_path))
