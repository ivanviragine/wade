"""Token → USD cost *estimates* for the stats dashboard.

Costs are API-equivalent estimates, never billing truth: CLI subscriptions,
enterprise discounts, batch/flex/fast tiers, cache-write surcharges and
long-context tiers are not modeled (the base tier is used), and transcripts do
not separate cache reads from cache writes (all cached tokens are priced at the
cache-read rate).

Built-in prices are the providers' published standard-tier list prices in USD
per million tokens, as of :data:`PRICING_AS_OF`:

- Anthropic Claude — platform.claude.com/docs/en/about-claude/pricing
- OpenAI GPT / Codex — developers.openai.com/api/docs/pricing
- Google Gemini — aistudio.google.com/docs/pricing (scheduled increases are
  applied by session date)
- xAI Grok — docs.x.ai/docs/models

Moonshot Kimi and other models are unpriced: their pricing pages could not be
read reliably. Add any model to ``pricing.yml`` (see :func:`default_pricing_path`)::

    models:
      kimi-k3: {input: 0.60, output: 2.50, cached: 0.15}

User keys match a normalized model name exactly or as a prefix
(``kimi-k3`` prices ``kimi-k3-preview``) and always win over built-ins.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

PRICING_AS_OF = "2026-11-06"


class ModelPrice(BaseModel):
    """USD per million tokens."""

    input: float
    output: float
    cached: float | None = None  # defaults to 10% of input

    @property
    def cached_rate(self) -> float:
        return self.cached if self.cached is not None else self.input * 0.1

    def cost(self, input_tokens: int, output_tokens: int, cached_tokens: int) -> float:
        """Cost for *disjoint* counts: fresh input, output, and cache reads."""
        return (
            input_tokens * self.input
            + output_tokens * self.output
            + cached_tokens * self.cached_rate
        ) / 1_000_000


def _p(inp: float, out: float, cached: float) -> ModelPrice:
    return ModelPrice(input=inp, output=out, cached=cached)


# A schedule is [(effective_from, price), ...] in date order; None = always.
PriceSchedule = list[tuple[date | None, ModelPrice]]

# (family, version) → price. Version uses dash separators ("5-5", "4-6").
_CLAUDE_PRICES: dict[tuple[str, str], ModelPrice] = {
    ("fable", "5-1"): _p(10.0, 50.0, 0.25),
    ("mythos", "5-1"): _p(10.0, 50.0, 0.25),
    ("fable", "5"): _p(10.0, 50.0, 1.0),
    ("mythos", "5"): _p(10.0, 50.0, 1.0),
    ("opus", "5-5"): _p(4.0, 20.0, 0.20),
    ("opus", "5"): _p(5.0, 25.0, 0.50),
    ("opus", "4-8"): _p(5.0, 25.0, 0.50),
    ("opus", "4-7"): _p(5.0, 25.0, 0.50),
    ("opus", "4-6"): _p(5.0, 25.0, 0.50),
    ("opus", "4-5"): _p(5.0, 25.0, 0.50),
    ("opus", "4-1"): _p(15.0, 75.0, 1.50),
    ("opus", "4"): _p(15.0, 75.0, 1.50),
    ("sonnet", "5-5"): _p(2.0, 10.0, 0.10),
    ("sonnet", "5"): _p(2.0, 10.0, 0.20),
    ("sonnet", "4-6"): _p(3.0, 15.0, 0.30),
    ("sonnet", "4-5"): _p(3.0, 15.0, 0.30),
    ("sonnet", "4"): _p(3.0, 15.0, 0.30),
    # Haiku 5.5 doubles past 100K-token prompts; prompt size isn't in transcripts.
    ("haiku", "5-5"): _p(0.10, 0.50, 0.01),
    ("haiku", "4-5"): _p(1.0, 5.0, 0.10),
}

# Non-Claude models, keyed by normalized name (see normalize_model).
_NAMED_PRICES: dict[str, PriceSchedule] = {
    # --- OpenAI (standard tier, <=272K context; cached input = cache-read rate)
    "gpt-6-astra": [(None, _p(10.0, 50.0, 1.0))],
    "gpt-6-1-sol": [(None, _p(2.0, 10.0, 0.10))],
    "gpt-6-1-terra": [(None, _p(1.0, 5.0, 0.05))],
    "gpt-6-sol": [(None, _p(2.0, 10.0, 0.20))],
    "gpt-6-luna": [(None, _p(0.10, 0.50, 0.01))],
    "gpt-5-6-sol": [(None, _p(4.0, 20.0, 0.40))],  # promotional through 2026-11-30
    "gpt-5-6-terra": [(None, _p(2.0, 12.0, 0.20))],
    "gpt-5-6-luna": [(None, _p(0.20, 1.20, 0.02))],
    "gpt-5-5": [(None, _p(5.0, 30.0, 0.50))],
    "gpt-5-4": [(None, _p(2.50, 15.0, 0.25))],
    "gpt-5-4-mini": [(None, _p(0.75, 4.50, 0.075))],
    "gpt-5-4-nano": [(None, _p(0.20, 1.25, 0.02))],
    "gpt-5-3-codex": [(None, _p(1.75, 14.0, 0.175))],
    "gpt-5-2": [(None, _p(1.75, 14.0, 0.175))],
    "gpt-5": [(None, _p(1.25, 10.0, 0.125))],
    "gpt-5-mini": [(None, _p(0.25, 2.0, 0.025))],
    "gpt-5-nano": [(None, _p(0.05, 0.40, 0.005))],
    "gpt-4-1": [(None, _p(2.0, 8.0, 0.50))],
    "gpt-4-1-mini": [(None, _p(0.40, 1.60, 0.10))],
    "gpt-4o": [(None, _p(2.50, 10.0, 1.25))],
    "gpt-4o-mini": [(None, _p(0.15, 0.60, 0.075))],
    "o3": [(None, _p(2.0, 8.0, 0.50))],
    # --- Google Gemini (standard paid tier, <=200K prompts)
    "gemini-4-argon": [
        (None, _p(2.0, 10.0, 0.10)),
        (date(2026, 12, 1), _p(4.0, 20.0, 0.20)),
    ],
    "gemini-3-8-flash": [
        (None, _p(0.75, 3.75, 0.075)),
        (date(2027, 1, 1), _p(1.50, 7.50, 0.15)),
    ],
    "gemini-3-6-flash": [
        (None, _p(0.75, 3.75, 0.075)),
        (date(2027, 1, 1), _p(1.50, 7.50, 0.15)),
    ],
    "gemini-3-5-flash-lite": [(None, _p(0.30, 2.50, 0.03))],
    "gemini-3-1-flash-lite": [(None, _p(0.25, 1.50, 0.025))],
    "gemini-3-1-pro": [(None, _p(2.0, 12.0, 0.20))],
    "gemini-3-flash": [(None, _p(0.50, 3.0, 0.05))],
    "gemini-2-5-pro": [(None, _p(1.25, 10.0, 0.125))],
    "gemini-2-5-flash": [(None, _p(0.30, 2.50, 0.03))],
    "gemini-2-5-flash-lite": [(None, _p(0.10, 0.40, 0.01))],
    # --- xAI Grok (<200K prompt tokens)
    "grok-4-7": [(None, _p(2.0, 6.0, 0.50))],
    "grok-4-6": [(None, _p(2.0, 6.0, 0.50))],
    "grok-4-5": [(None, _p(2.0, 6.0, 0.30))],
    "grok-4-3": [(None, _p(1.25, 2.50, 0.20))],
}

# Suffixes tools append to a model name that don't change its list price
# (reasoning effort, thinking toggles, preview tags). Speed tiers ("fast",
# "ultrafast", "priority") cost more and are deliberately NOT here, so they stay
# unpriced instead of being silently under-estimated.
_NEUTRAL_SUFFIXES = frozenset(
    {
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "thinking",
        "reasoning",
        "preview",
        "latest",
        "customtools",
    }
)
# Routing prefixes some tools put in front of the model id.
_PROVIDER_PREFIXES = (
    "github-copilot-",
    "openai-",
    "anthropic-",
    "google-antigravity-",
    "google-",
    "xai-",
    "opencode-",
    "antigravity-",
)

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


def _strip_provider(normalized: str) -> str:
    for prefix in _PROVIDER_PREFIXES:
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def _claude_key(normalized: str) -> tuple[str, str] | None:
    if m := _FAMILY_THEN_VERSION.search(normalized):
        return m.group(1), m.group(2)
    if m := _VERSION_THEN_FAMILY.search(normalized):  # cursor: claude-4-6-opus-high
        return m.group(2), m.group(1)
    return None


def _named_key(normalized: str) -> str | None:
    """Exact key, or the longest key followed only by price-neutral suffixes.

    ``gpt-5-6-sol-high`` → ``gpt-5-6-sol``; ``gpt-5-6`` never falls back to
    ``gpt-5`` (a version digit is not a neutral suffix), and an unknown variant
    such as ``gpt-5-codex`` stays unpriced rather than borrowing ``gpt-5``.
    """
    if normalized in _NAMED_PRICES:
        return normalized
    best: str | None = None
    for key in _NAMED_PRICES:
        if not normalized.startswith(key + "-"):
            continue
        rest = normalized[len(key) + 1 :].split("-")
        if all(part in _NEUTRAL_SUFFIXES or re.fullmatch(r"\d{4,}", part) for part in rest) and (
            best is None or len(key) > len(best)
        ):
            best = key
    return best


def _at(schedule: PriceSchedule, on: date | None) -> ModelPrice:
    price = schedule[0][1]
    for effective, candidate in schedule:
        if effective is None or (on is not None and on >= effective):
            price = candidate
    return price


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
        self._cache: dict[str, ModelPrice | PriceSchedule | None] = {}

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

    def _resolve(self, model: str) -> ModelPrice | PriceSchedule | None:
        if model in self._cache:
            return self._cache[model]
        norm = normalize_model(model)
        found: ModelPrice | PriceSchedule | None = None
        for candidate in (norm, _strip_provider(norm)):
            if candidate in self._overrides:
                found = self._overrides[candidate]
                break
            prefixes = [k for k in self._overrides if candidate.startswith(k + "-")]
            if prefixes:
                found = self._overrides[max(prefixes, key=len)]
                break
        if found is None:
            stripped = _strip_provider(norm)
            if (claude := _claude_key(stripped)) is not None:
                found = _CLAUDE_PRICES.get(claude)
            elif (key := _named_key(stripped)) is not None:
                found = _NAMED_PRICES[key]
        self._cache[model] = found
        return found

    def price_for(self, model: str | None, on: date | None = None) -> ModelPrice | None:
        """Price for *model* on date *on* (today's schedule entry when omitted)."""
        if not model:
            return None
        found = self._resolve(model)
        if found is None or isinstance(found, ModelPrice):
            return found
        return _at(found, on or date.today())

    def cost(
        self,
        model: str | None,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        on: date | None = None,
    ) -> float | None:
        price = self.price_for(model, on)
        if price is None:
            return None
        return price.cost(input_tokens, output_tokens, cached_tokens)
