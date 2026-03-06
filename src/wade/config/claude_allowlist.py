"""Claude Code .claude/settings.json allowlist management.

Configures the Claude Code permission allowlist to include `wade` commands
and project scripts, so agents can run them without manual approval.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Allowlist entry pattern for wade commands
WADE_ALLOW_PATTERN = "Bash(wade *)"


def canonical_to_claude(pattern: str) -> str:
    """Convert a canonical command pattern to Claude Code allowlist syntax.

    Canonical patterns use shell-style ``"cmd args"`` notation.
    Claude expects ``"Bash(cmd args)"`` — the command string wrapped in ``Bash(…)``.

    Examples::

        "wade *"                → "Bash(wade *)"
        "./scripts/check.sh *"  → "Bash(./scripts/check.sh *)"
        "./scripts/check.sh"    → "Bash(./scripts/check.sh)"
    """
    return f"Bash({pattern})"


def is_allowlist_configured(project_root: Path) -> bool:
    """Return True if Bash(wade *) is present in the allowlist at project_root."""
    settings_path = project_root / ".claude" / "settings.json"
    if not settings_path.is_file():
        return False
    with contextlib.suppress(json.JSONDecodeError, OSError):
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            allow = raw.get("permissions", {}).get("allow", [])
            return isinstance(allow, list) and WADE_ALLOW_PATTERN in allow
    return False


def configure_allowlist(
    project_root: Path,
    extra_patterns: list[str] | None = None,
) -> None:
    """Add wade commands to .claude/settings.json permissions allowlist.

    Args:
        project_root: Project directory containing ``.claude/``.
        extra_patterns: Additional canonical command patterns (e.g.
            ``["./scripts/check.sh *"]``).  Translated to Claude syntax
            and merged into the allowlist.

    Idempotent — each pattern is added at most once.  Non-destructive
    merge with existing settings.
    """
    settings_path = project_root / ".claude" / "settings.json"

    existing: dict[str, object] = {}
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw

    permissions = existing.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        permissions = {}
        existing["permissions"] = permissions

    allow_list = permissions.setdefault("allow", [])
    if not isinstance(allow_list, list):
        allow_list = []
        permissions["allow"] = allow_list

    # Build the full set of patterns to ensure
    all_patterns = [WADE_ALLOW_PATTERN]
    for pat in extra_patterns or []:
        claude_pat = canonical_to_claude(pat)
        if claude_pat not in all_patterns:
            all_patterns.append(claude_pat)

    changed = False
    for pat in all_patterns:
        if pat not in allow_list:
            allow_list.append(pat)
            changed = True

    if not changed:
        return  # All patterns already present

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(existing, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("claude_allowlist.configured", path=str(settings_path))
