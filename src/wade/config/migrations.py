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
from wade.utils.knowledge_file import knowledge_copy_exclusions
from wade.utils.paths import collapse_relative_path

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


def strip_knowledge_from_copy_to_worktree(raw: dict[str, Any]) -> bool:
    """Remove the knowledge file + ratings sidecars from ``hooks.copy_to_worktree`` (#358).

    Pre-#358, ``wade init`` added the knowledge file and its ratings sidecar to
    ``hooks.copy_to_worktree``. #358 stops copying them — they are tracked, so a new
    worktree already checks out the committed version, and copying main's copy over
    it manufactures the stale snapshot #358 fixes. Strip them here so an
    already-inited project stops re-copying on its next bootstrap.

    Pure YAML-dict mutation with no services import (``config`` is a lower layer than
    ``services``). Derives the file name from ``knowledge.path`` (default
    ``KNOWLEDGE.md``) and strips it plus its ``.ratings.yml`` / ``.ratings.jsonl``
    siblings. Idempotent.
    """
    hooks = raw.get("hooks")
    if not isinstance(hooks, dict):
        return False
    copy_list = hooks.get("copy_to_worktree")
    if not isinstance(copy_list, list):
        return False

    kpath = "KNOWLEDGE.md"
    knowledge = raw.get("knowledge")
    if isinstance(knowledge, dict):
        configured = knowledge.get("path")
        if isinstance(configured, str) and configured:
            kpath = configured
    # Canonicalize both the configured targets and the copy-hook entries before comparing
    # so equivalent spellings (``./KNOWLEDGE.md``, ``docs/../KNOWLEDGE.md`` vs ``KNOWLEDGE.md``)
    # match — otherwise a ``./``- or ``..``-spelled config path would slip the filter and
    # bootstrap could re-copy main's dirty knowledge file. ``knowledge_copy_exclusions`` is the
    # single derivation bootstrap's ``_effective_copy_files`` shares, so the two sites can't drift.
    targets = knowledge_copy_exclusions(kpath)

    filtered = [
        item
        for item in copy_list
        if not isinstance(item, str) or collapse_relative_path(item) not in targets
    ]
    if len(filtered) == len(copy_list):
        return False
    hooks["copy_to_worktree"] = filtered
    return True


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
        changed = strip_knowledge_from_copy_to_worktree(raw) or changed

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
