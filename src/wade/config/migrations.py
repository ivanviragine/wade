"""Config migration pipeline — idempotent YAML mutations.

Each migration takes the raw YAML dict, mutates in place, and returns True
if anything changed. `run_all_migrations` orchestrates them in order.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

from wade.config.loader import ConfigError, ensure_yaml_mapping

logger = structlog.get_logger()


def ensure_version(raw: dict[str, Any]) -> bool:
    """Set version: 2 if missing."""
    if "version" not in raw:
        raw["version"] = 2
        return True
    return False


_TIER_KEYS = ("easy", "medium", "complex", "very_complex")


def migrate_string_tiers_to_tier_config(raw: dict[str, Any]) -> bool:
    """Upgrade legacy string-valued complexity tiers to ``{model, effort}`` form.

    Converts ``easy: claude-haiku-4.5`` → ``easy: {model: claude-haiku-4.5, effort: null}``.
    Already-structured values are left untouched. Idempotent.
    """
    models = raw.get("models")
    if not isinstance(models, dict):
        return False

    changed = False
    for tool_mapping in models.values():
        if not isinstance(tool_mapping, dict):
            continue
        for tier in _TIER_KEYS:
            val = tool_mapping.get(tier)
            if val is None:
                continue
            if isinstance(val, str):
                tool_mapping[tier] = {"model": val, "effort": None}
                changed = True
            # dict form already canonical — leave alone
    return changed


def run_all_migrations(config_path: Path) -> bool:
    """Run all migrations on a .wade.yml file.

    Loads the file, runs each migration in order, writes back if changed.
    Returns True if any migration made changes. If any migration step
    fails, the original file content is restored and RuntimeError is raised.
    """
    try:
        original_content = config_path.read_text(encoding="utf-8")
        raw = yaml.safe_load(original_content)
    except (yaml.YAMLError, OSError) as e:
        logger.warning("migrations.load_failed", path=str(config_path), error=str(e))
        return False

    try:
        validated = ensure_yaml_mapping(raw)
        raw = validated if validated is not None else {}
    except ConfigError:
        logger.warning("migrations.invalid_shape", path=str(config_path))
        return False

    try:
        changed = ensure_version(raw)
        changed = migrate_string_tiers_to_tier_config(raw) or changed

        if changed:
            config_path.write_text(
                yaml.dump(raw, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
            logger.info("migrations.applied", path=str(config_path))
    except Exception as e:
        config_path.write_text(original_content, encoding="utf-8")
        raise RuntimeError(f"Migration failed; config file restored to original. Error: {e}") from e

    return changed
