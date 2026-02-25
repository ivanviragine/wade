"""Model utility functions — tier classification, date suffix detection.

Behavioral reference: lib/init.sh:_init_probe_models_for_tool(),
_init_scrape_models_for_tool(), _init_list_available_models_from_tool()
"""

from __future__ import annotations

import re

import structlog

from ghaiw.models.ai import AIModel, ModelTier

logger = structlog.get_logger()


def has_date_suffix(model_id: str) -> bool:
    """Check if a model ID has a YYYYMMDD date suffix.

    Examples:
        claude-haiku-4-5-20251001 → True
        claude-haiku-4-5 → False
        gemini-2.0-flash → False
    """
    return bool(re.search(r"-\d{8}$", model_id))


def classify_tier_claude(model_id: str) -> ModelTier | None:
    """Classify a Claude model into a tier.

    haiku → FAST, sonnet → BALANCED, opus → POWERFUL
    """
    lower = model_id.lower()
    if "haiku" in lower:
        return ModelTier.FAST
    if "sonnet" in lower:
        return ModelTier.BALANCED
    if "opus" in lower:
        return ModelTier.POWERFUL
    return None


def classify_tier_gemini(model_id: str) -> ModelTier | None:
    """Classify a Gemini model into a tier.

    flash → FAST, pro → BALANCED, ultra → POWERFUL
    """
    lower = model_id.lower()
    if "flash" in lower:
        return ModelTier.FAST
    if "pro" in lower:
        return ModelTier.BALANCED
    if "ultra" in lower:
        return ModelTier.POWERFUL
    return None


def classify_tier_codex(model_id: str) -> ModelTier | None:
    """Classify a Codex model into a tier.

    mini → FAST for easy/medium, non-mini → POWERFUL
    """
    lower = model_id.lower()
    if "mini" in lower:
        return ModelTier.FAST
    return ModelTier.POWERFUL


def parse_model_list_output(output: str, classifier: object | None = None) -> list[AIModel]:
    """Parse the output of a model listing command (e.g., `claude models`).

    Returns a list of AIModel instances with tier and is_alias set.
    """
    models: list[AIModel] = []
    for line in output.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue

        # Extract model ID (first word or first column)
        parts = line.split()
        if not parts:
            continue
        model_id = parts[0]

        # Skip header-like lines
        if model_id.lower() in ("model", "name", "id"):
            continue

        is_alias = not has_date_suffix(model_id)

        models.append(
            AIModel(
                id=model_id,
                is_alias=is_alias,
            )
        )

    return models


def _has_component(model_id: str, keyword: str) -> bool:
    """Check if keyword appears as a model ID component (delimited by '-' or '.')."""
    # Match keyword at start, end, or between delimiters
    return bool(re.search(rf"(?:^|[-.]){re.escape(keyword)}(?:[-.]|$)", model_id))


def classify_tier_universal(model_id: str) -> ModelTier:
    """Classify any model into a tier using universal keywords.

    Used when processing raw model IDs from scraping/probing.

    Tier mapping (matches Bash _init_probe_models_for_tool):
        easy/medium  — haiku, flash, spark, mini
        complex      — sonnet, or unrecognized mid-tier models
        very_complex — opus, pro, ultra, max

    Note: uses component-level matching to avoid false positives like
    "gemini" matching "mini". Keywords must appear as distinct components
    separated by '-' or '.'.

    Behavioral ref: lib/init.sh lines 780-788
    """
    lower = model_id.lower()
    if any(_has_component(lower, kw) for kw in ("haiku", "flash", "spark", "mini")):
        return ModelTier.FAST
    if any(_has_component(lower, kw) for kw in ("opus", "pro", "ultra", "max")):
        return ModelTier.POWERFUL
    if _has_component(lower, "sonnet"):
        return ModelTier.BALANCED
    # Default: unrecognized models go to balanced tier
    return ModelTier.BALANCED


def raw_ids_to_models(
    model_ids: list[str],
    classifier: ModelTier | None = None,
) -> list[AIModel]:
    """Convert raw model ID strings to AIModel instances with tier classification.

    If classifier is provided, all models get that tier. Otherwise uses
    classify_tier_universal().
    """
    models: list[AIModel] = []
    for mid in model_ids:
        mid = mid.strip()
        if not mid:
            continue
        tier = classifier or classify_tier_universal(mid)
        models.append(
            AIModel(
                id=mid,
                is_alias=not has_date_suffix(mid),
                tier=tier,
            )
        )
    return models
