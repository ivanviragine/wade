"""Model utility functions — tier classification, date suffix detection."""

from __future__ import annotations

import re

from ghaiw.models.ai import AIModel, ModelTier


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
