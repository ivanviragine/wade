"""Config migration pipeline — idempotent YAML mutations.

Each migration takes the raw YAML dict, mutates in place, and returns True
if anything changed. `run_all_migrations` orchestrates them in order.

Behavioral reference: lib/init.sh (various _init_* migration helpers)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog
import yaml

from ghaiw.config.defaults import get_defaults

logger = structlog.get_logger()

# Deprecated model values that should be replaced
_DEPRECATED_MODELS: dict[str, str] = {
    "gemini-3-flash": "claude-haiku-4-5",
}

# Claude model ID format: dashes for Claude CLI, dots for Copilot
_CLAUDE_DASH_TO_DOT_RE = re.compile(r"(claude-\w+-\d+)-(\d+)")
_CLAUDE_DOT_TO_DASH_RE = re.compile(r"(claude-\w+-\d+)\.(\d+)")


def _get_ai_tool(raw: dict[str, Any]) -> str | None:
    """Extract the AI tool from config, handling both v1 and v2 formats."""
    ai = raw.get("ai")
    if isinstance(ai, dict):
        tool = ai.get("default_tool")
        if tool:
            return str(tool)
    # Legacy v1 key
    legacy = raw.get("ai_tool")
    if legacy:
        return str(legacy)
    return None


# ---------------------------------------------------------------------------
# Migration 1: Ensure version key
# ---------------------------------------------------------------------------


def ensure_version(raw: dict[str, Any]) -> bool:
    """Set version: 2 if missing."""
    if "version" not in raw:
        raw["version"] = 2
        return True
    return False


# ---------------------------------------------------------------------------
# Migration 2: Replace deprecated model values
# ---------------------------------------------------------------------------


def migrate_deprecated_model_values(raw: dict[str, Any], ai_tool: str | None) -> bool:
    """Replace known-bad model values with current defaults.

    Behavioral ref: _init_replace_deprecated_model_values
    """
    changed = False
    models = raw.get("models")
    if not isinstance(models, dict):
        return False

    for _tool_key, mapping in models.items():
        if not isinstance(mapping, dict):
            continue
        for tier_key, model_val in list(mapping.items()):
            if isinstance(model_val, str) and model_val in _DEPRECATED_MODELS:
                mapping[tier_key] = _DEPRECATED_MODELS[model_val]
                changed = True

    return changed


# ---------------------------------------------------------------------------
# Migration 3: Migrate flat v1 model keys to nested v2 format
# ---------------------------------------------------------------------------


def migrate_flat_to_nested_models(raw: dict[str, Any], ai_tool: str | None) -> bool:
    """Move v1 flat model_easy etc. to v2 models.<tool>.easy.

    Behavioral ref: _init_migrate_flat_model_keys
    """
    flat_keys = ("model_easy", "model_medium", "model_complex", "model_very_complex")
    has_flat = any(k in raw for k in flat_keys)
    if not has_flat:
        return False

    if not ai_tool:
        return False

    models = raw.setdefault("models", {})
    tool_models = models.setdefault(ai_tool, {})

    changed = False
    mapping = {
        "model_easy": "easy",
        "model_medium": "medium",
        "model_complex": "complex",
        "model_very_complex": "very_complex",
    }

    for flat_key, nested_key in mapping.items():
        val = raw.pop(flat_key, None)
        if val and not tool_models.get(nested_key):
            tool_models[nested_key] = val
            changed = True

    return changed


# ---------------------------------------------------------------------------
# Migration 4: Normalize model format (dashes vs dots)
# ---------------------------------------------------------------------------


def _to_dashed(model_id: str) -> str:
    """Convert claude-haiku-4.5 -> claude-haiku-4-5."""
    return _CLAUDE_DOT_TO_DASH_RE.sub(r"\1-\2", model_id)


def _to_dotted(model_id: str) -> str:
    """Convert claude-haiku-4-5 -> claude-haiku-4.5."""
    return _CLAUDE_DASH_TO_DOT_RE.sub(r"\1.\2", model_id)


def _tool_uses_dotted(tool: str) -> bool:
    """Return True if the tool expects dotted Claude model IDs."""
    return tool in ("copilot",)


def normalize_model_format(raw: dict[str, Any], ai_tool: str | None) -> bool:
    """Normalize Claude model IDs to the tool's expected format.

    Claude CLI = dashes (claude-haiku-4-5)
    Copilot = dots (claude-haiku-4.5)
    Non-Claude models are left unchanged.

    Behavioral ref: _init_normalize_model_values_for_tool
    """
    if not ai_tool:
        return False

    models = raw.get("models")
    if not isinstance(models, dict):
        return False

    use_dots = _tool_uses_dotted(ai_tool)
    converter = _to_dotted if use_dots else _to_dashed
    changed = False

    for _tool_key, mapping in models.items():
        if not isinstance(mapping, dict):
            continue
        for tier_key, model_val in list(mapping.items()):
            if isinstance(model_val, str) and model_val.startswith("claude-"):
                new_val = converter(model_val)
                if new_val != model_val:
                    mapping[tier_key] = new_val
                    changed = True

    return changed


# ---------------------------------------------------------------------------
# Migration 5: Backfill missing model tier keys
# ---------------------------------------------------------------------------


def backfill_missing_model_keys(raw: dict[str, Any], ai_tool: str | None) -> bool:
    """Fill in any missing tier keys with defaults.

    Behavioral ref: _init_patch_missing_model_keys
    """
    if not ai_tool:
        return False

    defaults = get_defaults(ai_tool)
    if not defaults.easy:
        return False

    models = raw.setdefault("models", {})
    tool_models = models.setdefault(ai_tool, {})

    changed = False
    for key in ("easy", "medium", "complex", "very_complex"):
        default_val = getattr(defaults, key, None)
        if default_val and not tool_models.get(key):
            tool_models[key] = default_val
            changed = True

    return changed


# ---------------------------------------------------------------------------
# Migration 6: Backfill per-command AI sections
# ---------------------------------------------------------------------------


def backfill_per_command_keys(raw: dict[str, Any]) -> bool:
    """Ensure ai.plan, ai.deps, ai.work sections exist.

    Behavioral ref: _init_patch_missing_per_command_keys
    """
    ai = raw.setdefault("ai", {})
    if not isinstance(ai, dict):
        raw["ai"] = ai = {}

    changed = False
    for cmd in ("plan", "deps", "work"):
        if cmd not in ai or not isinstance(ai[cmd], dict):
            ai[cmd] = {}
            changed = True

    return changed


# ---------------------------------------------------------------------------
# Migration 7: Normalize merge strategy casing
# ---------------------------------------------------------------------------


def normalize_merge_strategy(raw: dict[str, Any]) -> bool:
    """Normalize merge_strategy: 'pr' -> 'PR', 'direct' unchanged.

    Behavioral ref: _init_normalize_merge_strategy
    """
    project = raw.get("project")
    if not isinstance(project, dict):
        return False

    strategy = project.get("merge_strategy")
    if isinstance(strategy, str) and strategy.lower() == "pr" and strategy != "PR":
        project["merge_strategy"] = "PR"
        return True

    return False


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_all_migrations(config_path: Path) -> bool:
    """Run all migrations on a .ghaiw.yml file.

    Loads the file, runs each migration in order, writes back if changed.
    Returns True if any migration made changes.
    """
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as e:
        logger.warning("migrations.load_failed", path=str(config_path), error=str(e))
        return False

    if not isinstance(raw, dict):
        raw = {}

    ai_tool = _get_ai_tool(raw)

    migrations: list[Callable[[dict[str, object]], bool]] = [
        lambda r: ensure_version(r),
        lambda r: migrate_deprecated_model_values(r, ai_tool),
        lambda r: migrate_flat_to_nested_models(r, ai_tool),
        lambda r: normalize_model_format(r, ai_tool),
        lambda r: backfill_missing_model_keys(r, ai_tool),
        lambda r: backfill_per_command_keys(r),
        lambda r: normalize_merge_strategy(r),
    ]

    changed = False
    for migration in migrations:
        if migration(raw):
            changed = True

    if changed:
        config_path.write_text(
            yaml.dump(raw, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("migrations.applied", path=str(config_path))

    return changed
