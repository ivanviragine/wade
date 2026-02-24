"""Model utility functions — tier classification, date suffix detection, model discovery.

Behavioral reference: lib/init.sh:_init_probe_models_for_tool(),
_init_scrape_models_for_tool(), _init_list_available_models_from_tool()
"""

from __future__ import annotations

import re
import shutil
import subprocess

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


# ── Model discovery functions ──────────────────────────────────────────────────

# Docs URLs for web scraping model lists
_DOCS_URLS: dict[str, str] = {
    "claude": "https://docs.anthropic.com/en/docs/about-claude/models/overview",
    "gemini": "https://geminicli.com/docs/cli/model/",
    "codex": "https://developers.openai.com/codex/models/",
}

# Regex patterns per tool for extracting model IDs from HTML
_SCRAPE_PATTERNS: dict[str, str] = {
    "claude": r"claude-[a-z]+-[0-9]+[-\.][0-9]+[a-zA-Z0-9._-]*",
    "gemini": r"gemini-[0-9][.0-9]*-(flash|pro|ultra)[a-z0-9._-]*",
    "codex": r"gpt-[a-z0-9._-]+",
}


def scrape_models_from_docs(tool: str) -> list[str]:
    """Scrape model IDs from a tool's official documentation page.

    Uses curl subprocess (no Python HTTP dependency needed).
    Returns a sorted, deduplicated list of model ID strings.

    Behavioral ref: lib/init.sh:_init_scrape_models_for_tool()
    """
    if tool not in _DOCS_URLS or not shutil.which("curl"):
        return []

    url = _DOCS_URLS[tool]
    pattern = _SCRAPE_PATTERNS[tool]

    try:
        result = subprocess.run(
            ["curl", "-fsSL", "--max-time", "10", url],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return []

        # For codex, the page has `codex -m gpt-...` patterns — extract the model part
        if tool == "codex":
            full_matches = re.findall(r"codex -m (gpt-[a-z0-9._-]+)", result.stdout)
            if full_matches:
                return sorted(set(full_matches), reverse=True)

        matches = re.findall(pattern, result.stdout)
        # Gemini regex has a capture group — flatten to strings
        if tool == "gemini":
            matches = [m if isinstance(m, str) else m[0] for m in matches]
            # Rebuild full match from HTML since findall returns capture groups
            matches = re.findall(
                r"gemini-[0-9][.0-9]*-(?:flash|pro|ultra)[a-z0-9._-]*", result.stdout
            )

        return sorted(set(matches), reverse=(tool in ("codex", "gemini")))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        logger.debug("model_discovery.scrape_failed", tool=tool, url=url)
        return []


def probe_copilot_models() -> list[str]:
    """Probe Copilot CLI for available models via --model validation error.

    Copilot has no `models` subcommand. Passing an invalid model triggers a
    validation error that lists all valid model names.

    Error format: "...Allowed choices are claude-sonnet-4.6, gpt-5.3-codex, ..."

    Behavioral ref: lib/init.sh:_init_list_available_models_from_tool() copilot case
    """
    if not shutil.which("copilot"):
        return []

    try:
        result = subprocess.run(
            ["copilot", "--model", "x"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Combine stdout and stderr — error may appear in either
        output = result.stdout + result.stderr

        # Extract model-like IDs from the validation error output
        matches = re.findall(r"(?:claude|gpt|gemini|codex|o[0-9])[a-zA-Z0-9._-]*", output)
        # Clean trailing punctuation
        cleaned = [re.sub(r"[.,;]+$", "", m) for m in matches if not m.startswith(".")]
        return sorted(set(cleaned))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        logger.debug("model_discovery.copilot_probe_failed")
        return []


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
