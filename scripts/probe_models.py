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
from typing import Any

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
    diff_summary: list[str] = []

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
                diff_summary.append(f"For the '{tool}' tools list, ADD these items: {sorted(new)}")
            if missing:
                console.warn(f"OBSOLETE (found in {JSON_PATH.name} but not in probe):")
                for m in sorted(missing):
                    console.detail(f"  - {m}")
                diff_summary.append(
                    f"For the '{tool}' tools list, REMOVE these items: {sorted(missing)}"
                )
        console.empty()

    if not has_diff:
        console.success("All models strictly match the JSON registry!")
        return 0

    from ghaiw.ui import prompts

    msg = f"\nWould you like to use an AI agent to auto-correct {JSON_PATH.name}?"
    if not prompts.confirm(msg, default=True):
        console.error(f"Differences found. Please update {JSON_PATH} manually.")
        return 1

    installed = []
    for tool_id in AbstractAITool.detect_installed():
        try:
            adapter = AbstractAITool.get(tool_id)
            if adapter.capabilities().supports_headless:
                installed.append((tool_id, adapter))
        except ValueError:
            pass

    if not installed:
        console.error("No compatible headless AI tools installed to perform auto-correction.")
        return 1

    items = [f"{t[1].capabilities().display_name} ({t[0]})" for t in installed]
    idx = prompts.select("Select AI tool to use for correction", items)
    tool_id, adapter = installed[idx]

    prompt = (
        "You are tasked with updating a JSON file based on some diff instructions.\n"
        "Output ONLY valid JSON. Do not include markdown formatting (like ```json), "
        "intro, or outro text. Output raw JSON only.\n\n"
        "Here is the current JSON:\n"
        f"{json.dumps(registry_raw, indent=2)}\n\n"
        "Please apply the following changes to the lists:\n" + "\n".join(diff_summary)
    )

    import os
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # Prevent nested session crash if user runs this inside Claude Code

    expected_schema = {
        "type": "object",
        "additionalProperties": {
            "type": "array",
            "items": {"type": "string"},
        }
    }

    cmd = adapter.build_launch_command(prompt=prompt, json_schema=expected_schema)
    with console.status(f"Asking {adapter.capabilities().display_name} to fix {JSON_PATH.name}..."):
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)

    out = res.stdout.strip()

    # Try multiple strategies to find valid JSON
    def extract_json(text: str) -> str | None:
        def extract_payload(parsed: Any) -> str | None:
            # Claude and Copilot `--json-schema` wraps the answer inside `structured_output`
            if isinstance(parsed, dict) and "structured_output" in parsed:
                return json.dumps(parsed["structured_output"], indent=2)
            # Or it might just be the direct object itself
            is_dict = isinstance(parsed, dict)
            providers = ["claude", "copilot", "gemini", "codex", "opencode"]
            has_providers = any(k in parsed for k in providers)
            if is_dict and has_providers:
                return json.dumps(parsed, indent=2)
            return None

        # Strategy 1: The whole thing might be valid JSON
        try:
            parsed = json.loads(text)
            if payload := extract_payload(parsed):
                return payload
        except ValueError:
            pass

        # Strategy 2: Remove markdown formatting
        cleaned = re.sub(r"^```(?:json)?\n?", "", text, flags=re.MULTILINE)
        cleaned = re.sub(r"\n```$", "", cleaned)
        try:
            parsed = json.loads(cleaned)
            if payload := extract_payload(parsed):
                return payload
        except ValueError:
            pass

        # Strategy 3: Find first { and last }
        if "{" in text and "}" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start < end:
                substring = text[start:end]
                try:
                    parsed = json.loads(substring)
                    if payload := extract_payload(parsed):
                        return payload
                except ValueError:
                    pass
        return None

    valid_json = extract_json(out)
    if not valid_json:
        console.error("AI tool did not return valid JSON.")
        console.detail(f"Raw output was:\n{out}")
        return 1

    out = valid_json

    from rich.console import Console
    from rich.syntax import Syntax

    rc = Console()
    console.empty()
    console.header(f"Proposed {JSON_PATH.name}")
    rc.print(Syntax(out, "json", theme="monokai", word_wrap=True))
    console.empty()

    if prompts.confirm(f"Overwrite {JSON_PATH.name} with this new content?", default=True):
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            f.write(out + "\n")
        console.success(f"Successfully updated {JSON_PATH.name}.")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
