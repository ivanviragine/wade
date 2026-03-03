"""Model utility functions — tier classification, date suffix detection."""

from __future__ import annotations

import re

from wade.models.ai import ModelTier


def has_date_suffix(model_id: str) -> bool:
    """Check if a model ID has a YYYYMMDD date suffix.

    Examples:
        claude-haiku-4-5-20251001 → True
        claude-haiku-4-5 → False
        gemini-2.0-flash → False
    """
    return bool(re.search(r"-\d{8}$", model_id))


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
