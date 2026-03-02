"""Model registry — reads models.json, source of truth for known model IDs."""

from __future__ import annotations

import json
from pathlib import Path

_REGISTRY_PATH = Path(__file__).parent / "models.json"
MODELS: dict[str, list[str]] = {
    k: v
    for k, v in json.loads(_REGISTRY_PATH.read_text()).items()
    if not k.startswith("_")  # skip _note and similar meta keys
}


def get_models_for_tool(tool: str) -> list[str]:
    """Return the known model list for a tool (empty list if unknown)."""
    return MODELS.get(tool, [])
