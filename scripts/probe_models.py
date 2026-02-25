#!/usr/bin/env python3
"""Developer utility: probe AI CLIs and websites to discover new models.

Compares discovered models against src/ghaiw/data/models.json and reports
any differences. Exits 1 if updates are needed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from ghaiw.ai_tools.base import AbstractAITool

# Must match src/ghaiw/data/models.json structure
JSON_PATH = Path(__file__).parent.parent / "src" / "ghaiw" / "data" / "models.json"

_DOCS_URLS: dict[str, str] = {
    "claude": "https://docs.anthropic.com/en/docs/about-claude/models/overview",
    "gemini": "https://geminicli.com/docs/cli/model/",
    "codex": "https://developers.openai.com/codex/models/",
}

_SCRAPE_PATTERNS: dict[str, str] = {
    "claude": r"claude-[a-z]+-[0-9]+[-\.][0-9]+[a-zA-Z0-9._-]*",
    "gemini": r"gemini-[0-9][.0-9]*-(flash|pro|ultra)[a-z0-9._-]*",
    "codex": r"gpt-[a-z0-9._-]+",
}


def _scrape_models(tool: str) -> set[str]:
    """Scrape model IDs from docs."""
    if tool not in _DOCS_URLS or not shutil.which("curl"):
        return set()

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
            return set()

        if tool == "codex":
            full_matches = re.findall(r"codex -m (gpt-[a-z0-9._-]+)", result.stdout)
            if full_matches:
                return set(full_matches)

        matches = re.findall(pattern, result.stdout)
        if tool == "gemini":
            # Rebuild full match from HTML since findall returns capture groups
            matches = re.findall(
                r"gemini-[0-9][.0-9]*-(?:flash|pro|ultra)[a-z0-9._-]*", result.stdout
            )
        return set(m if isinstance(m, str) else m[0] for m in matches)
    except Exception:
        return set()


def probe_claude() -> set[str]:
    """Probe claude CLI, fallback to scrape."""
    try:
        res = subprocess.run(["claude", "models"], capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            models = set()
            for line in res.stdout.splitlines():
                if line.strip() and not line.startswith(("#", "-")):
                    parts = line.split()
                    if parts and parts[0].lower() not in ("model", "name", "id"):
                        models.add(parts[0])
            if models:
                return models
    except Exception:
        pass
    return _scrape_models("claude")


def probe_copilot() -> set[str]:
    if not shutil.which("copilot"):
        return set()
    try:
        res = subprocess.run(
            ["copilot", "--model", "x"], capture_output=True, text=True, timeout=15
        )
        out = res.stdout + res.stderr
        matches = re.findall(r"(?:claude|gpt|gemini|codex|o[0-9])[a-zA-Z0-9._-]*", out)
        return {re.sub(r"[.,;]+$", "", m) for m in matches if not m.startswith(".")}
    except Exception:
        return set()


def probe_gemini() -> set[str]:
    try:
        res = subprocess.run(
            ["gemini", "--list-models"], capture_output=True, text=True, timeout=15
        )
        if res.returncode == 0:
            models = set()
            for line in res.stdout.splitlines():
                if line.strip() and not line.startswith(("#", "-")):
                    parts = line.split()
                    if parts and parts[0].lower() not in ("model", "name", "id"):
                        models.add(parts[0])
            if models:
                return models
    except Exception:
        pass
    return _scrape_models("gemini")


def probe_codex() -> set[str]:
    return _scrape_models("codex")


def probe_opencode() -> set[str]:
    try:
        res = subprocess.run(["opencode", "models"], capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            models = set()
            for line in res.stdout.splitlines():
                if line.strip() and not line.startswith(("#", "-")):
                    parts = line.split()
                    if parts and "/" in parts[0]:
                        models.add(parts[0])
            if models:
                return models
    except Exception:
        pass
    return set()


def main() -> int:
    from ghaiw.ui.console import console

    with open(JSON_PATH, encoding="utf-8") as f:
        registry_raw: Mapping[str, list[str]] = json.load(f)

    registry: dict[str, set[str]] = {
        k: set(v) for k, v in registry_raw.items() if not k.startswith("_")
    }

    found: dict[str, set[str]] = {}
    with console.status("Probing external AI providers..."):
        found = {
            "claude": probe_claude(),
            "copilot": probe_copilot(),
            "gemini": probe_gemini(),
            "codex": probe_codex(),
            "opencode": probe_opencode(),
        }

    has_diff = False
    console.empty()
    for tool, expected in registry.items():
        actual_raw = found.get(tool, set())

        # If we found nothing, maybe the CLI is missing or network failed.
        if not actual_raw:
            console.warn(f"[{tool}] Could not probe (CLI missing or offline). Skipping diff.")
            continue

        try:
            adapter = AbstractAITool.get(tool)
        except ValueError:
            adapter = None

        actual = {adapter.standardize_model_id(m) if adapter else m for m in actual_raw}

        missing = expected - actual
        new = actual - expected

        console.header(f"Provider: {tool}")
        if not missing and not new:
            console.detail("✓ Up to date.")
        else:
            has_diff = True
            if new:
                console.warn(f"NEW (found in probe but not in {JSON_PATH.name}):")
                for m in sorted(new):
                    console.detail(f"  + {m}")
            if missing:
                console.warn(f"OBSOLETE (found in {JSON_PATH.name} but not in probe):")
                for m in sorted(missing):
                    console.detail(f"  - {m}")
        console.empty()

    if has_diff:
        console.error(f"Differences found. Please update {JSON_PATH} manually.")
        return 1

    console.success("All models strictly match the JSON registry!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
