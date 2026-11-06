"""Token → USD cost *estimates* for the stats dashboard.

Costs are estimates, never billing truth: CLI subscriptions, enterprise
discounts, cache-write surcharges and long-context tiers are not modeled, and
transcripts do not separate cache reads from cache writes (all cached tokens
are priced at the cache-read rate).

Built-in prices cover Anthropic Claude model families at first-party API list
prices (USD per million tokens). Every other model is unpriced unless the user
adds it to ``pricing.yml`` (see :func:`default_pricing_path`)::

    models:
      gpt-5-codex: {input: 1.25, output: 10.0, cached: 0.125}
      gemini-3-pro: {input: 2.0, output: 12.0}

User keys match a normalized model name exactly or as a prefix
(``gpt-5`` prices ``gpt-5-codex-high``) and always win over built-ins.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

PRICING_AS_OF = "2026-11"


class ModelPrice(BaseModel):
    """USD per million tokens."""

    input: float
    output: float
    cached: float | None = None  # defaults to 10% of input

    @property
    def cached_rate(self) -> float:
        return self.cached if self.cached is not None else self.input * 0.1

    def cost(self, input_tokens: int, output_tokens: int, cached_tokens: int) -> float:
        return (
            input_tokens * self.input
            + output_tokens * self.output
            + cached_tokens * self.cached_rate
        ) / 1_000_000


# (family, version) → price. Version uses dash separators ("5-5", "4-6").
_CLAUDE_PRICES: dict[tuple[str, str], ModelPrice] = {
    ("fable", "5-1"): ModelPrice(input=10.0, output=50.0, cached=0.25),
    ("mythos", "5-1"): ModelPrice(input=10.0, output=50.0, cached=0.25),
    ("fable", "5"): ModelPrice(input=10.0, output=50.0, cached=1.0),
    ("mythos", "5"): ModelPrice(input=10.0, output=50.0, cached=1.0),
    ("opus", "5-5"): ModelPrice(input=4.0, output=20.0, cached=0.20),
    ("opus", "5"): ModelPrice(input=5.0, output=25.0, cached=0.50),
    ("opus", "4-8"): ModelPrice(input=5.0, output=25.0, cached=0.50),
    ("opus", "4-7"): ModelPrice(input=5.0, output=25.0, cached=0.50),
    ("opus", "4-6"): ModelPrice(input=5.0, output=25.0, cached=0.50),
    ("opus", "4-5"): ModelPrice(input=5.0, output=25.0, cached=0.50),
    ("opus", "4-1"): ModelPrice(input=15.0, output=75.0, cached=1.50),
    ("opus", "4"): ModelPrice(input=15.0, output=75.0, cached=1.50),
    ("sonnet", "5-5"): ModelPrice(input=2.0, output=10.0, cached=0.10),
    ("sonnet", "5"): ModelPrice(input=2.0, output=10.0, cached=0.20),
    ("sonnet", "4-6"): ModelPrice(input=3.0, output=15.0, cached=0.30),
    ("sonnet", "4-5"): ModelPrice(input=3.0, output=15.0, cached=0.30),
    ("sonnet", "4"): ModelPrice(input=3.0, output=15.0, cached=0.30),
    # Haiku 5.5 doubles past 100K-token prompts; per-request prompt size isn't
    # in transcripts, so the base tier is used (an underestimate for long prompts).
    ("haiku", "5-5"): ModelPrice(input=0.10, output=0.50, cached=0.01),
    ("haiku", "4-5"): ModelPrice(input=1.0, output=5.0, cached=0.10),
}

_FAMILIES = "fable|mythos|opus|sonnet|haiku"
# A version is a major number plus an optional *single-digit* minor, so a date
# suffix ("opus-4-20250514") is never read as a minor version.
_VERSION = r"\d+(?:-\d(?!\d))?"
_FAMILY_THEN_VERSION = re.compile(rf"(?:^|-)({_FAMILIES})-({_VERSION})")
_VERSION_THEN_FAMILY = re.compile(rf"(?:^|-)({_VERSION})-({_FAMILIES})(?:-|$)")


def normalize_model(name: str) -> str:
    """Lowercase and unify separators: ``Claude Opus 5.5`` → ``claude-opus-5-5``."""
    lowered = name.strip().lower()
    lowered = re.sub(r"[\s._/:@]+", "-", lowered)
    return re.sub(r"-+", "-", lowered).strip("-")


def _claude_key(normalized: str) -> tuple[str, str] | None:
    if m := _FAMILY_THEN_VERSION.search(normalized):
        return m.group(1), m.group(2)
    if m := _VERSION_THEN_FAMILY.search(normalized):  # cursor: claude-4-6-opus-high
        return m.group(2), m.group(1)
    return None


def default_pricing_path() -> Path:
    explicit = os.environ.get("WADE_STATS_PRICING")
    if explicit:
        return Path(explicit).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "wade" / "pricing.yml"


class PriceBook:
    """Resolves a (possibly tool-specific) model name to a price."""

    def __init__(self, overrides: dict[str, ModelPrice] | None = None) -> None:
        self._overrides = {normalize_model(k): v for k, v in (overrides or {}).items()}
        self._cache: dict[str, ModelPrice | None] = {}

    @classmethod
    def load(cls, path: Path | None = None) -> PriceBook:
        """Built-ins plus the user's ``pricing.yml`` (ignored when unreadable)."""
        path = path or default_pricing_path()
        overrides: dict[str, ModelPrice] = {}
        if path.is_file():
            try:
                import yaml

                raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                models = raw.get("models", {}) if isinstance(raw, dict) else {}
                for key, value in (models or {}).items():
                    if isinstance(value, dict):
                        overrides[str(key)] = ModelPrice.model_validate(value)
            except Exception:
                overrides = {}
        return cls(overrides)

    def price_for(self, model: str | None) -> ModelPrice | None:
        if not model:
            return None
        if model in self._cache:
            return self._cache[model]
        norm = normalize_model(model)
        price: ModelPrice | None = self._overrides.get(norm)
        if price is None:
            prefixes = [k for k in self._overrides if norm.startswith(k + "-")]
            if prefixes:
                price = self._overrides[max(prefixes, key=len)]
        if price is None and (key := _claude_key(norm)) is not None:
            price = _CLAUDE_PRICES.get(key)
        self._cache[model] = price
        return price

    def cost(
        self, model: str | None, input_tokens: int, output_tokens: int, cached_tokens: int
    ) -> float | None:
        price = self.price_for(model)
        if price is None:
            return None
        return price.cost(input_tokens, output_tokens, cached_tokens)
