"""Init service shell integration — profiles, statusline, gemini experimental.

Configures shell-init sourcing in the user's profile, the Claude Code statusline,
and Gemini experimental settings, with their interactive prompt wrappers. Leaf
module — imports nothing from siblings.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

from wade.ui.console import console

logger = structlog.get_logger()

__all__ = [
    "_configure_gemini_experimental",
    "_configure_shell_integration",
    "_configure_statusline",
    "_get_shell_profile",
    "_is_shell_integration_configured",
    "_prompt_configure_gemini_experimental",
    "_prompt_configure_shell_integration",
    "_prompt_configure_statusline",
]


def _configure_gemini_experimental() -> None:
    """Write Gemini experimental settings for plan mode support.

    Writes {"experimental":{"plan":true}} to ~/.gemini/settings.json,
    merging non-destructively with existing content.
    """
    import contextlib
    import json

    settings_path = Path.home() / ".gemini" / "settings.json"

    existing: dict[str, Any] = {}
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw

    experimental: dict[str, Any] = existing.get("experimental", {})
    if not isinstance(experimental, dict):
        experimental = {}
    if experimental.get("plan") is True:
        return  # Already configured

    experimental["plan"] = True
    existing["experimental"] = experimental

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(existing, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("gemini.experimental_configured", path=str(settings_path))


def _prompt_configure_gemini_experimental(non_interactive: bool) -> None:
    """Prompt user to enable Gemini experimental plan mode, skipping if already configured."""
    import contextlib
    import json

    from wade.ui import prompts

    settings_path = Path.home() / ".gemini" / "settings.json"

    existing: dict[str, Any] = {}
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw

    experimental = existing.get("experimental", {})
    if isinstance(experimental, dict) and experimental.get("plan") is True:
        return  # Already configured — skip section entirely

    if not non_interactive:
        console.rule("Gemini")

    if non_interactive:
        return

    if prompts.confirm(
        "Enable Gemini experimental.plan mode (required for planning)?",
        default=True,
    ):
        _configure_gemini_experimental()
        console.success("Enabled experimental.plan in ~/.gemini/settings.json")


def _configure_statusline() -> None:
    """Install Claude Code statusline script and register it in ~/.claude/settings.json.

    Copies templates/statusline-command.sh to ~/.claude/statusline-command.sh and
    writes ``{"statusLine": {"type": "command", ...}}`` into ~/.claude/settings.json,
    merging non-destructively with existing content.
    """
    import contextlib
    import json

    from wade.skills.installer import get_templates_dir

    dest = Path.home() / ".claude" / "statusline-command.sh"
    script_src = get_templates_dir() / "statusline-command.sh"

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(script_src.read_text(encoding="utf-8"), encoding="utf-8")

    settings_path = Path.home() / ".claude" / "settings.json"
    existing: dict[str, Any] = {}
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw

    existing["statusLine"] = {
        "type": "command",
        "command": "bash ~/.claude/statusline-command.sh",
    }
    settings_path.write_text(
        json.dumps(existing, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("claude.statusline_configured", path=str(dest))


def _prompt_configure_statusline(non_interactive: bool) -> None:
    """Prompt user to install Claude Code statusline, skipping if already configured."""
    import contextlib
    import json

    from wade.ui import prompts

    settings_path = Path.home() / ".claude" / "settings.json"
    existing: dict[str, Any] = {}
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw

    if "statusLine" in existing:
        return  # Already configured — skip silently (idempotent)

    if non_interactive:
        return

    if prompts.confirm(
        "Install Claude Code statusline script (token counts + context bar)?",
        default=True,
    ):
        _configure_statusline()
        console.success("Installed ~/.claude/statusline-command.sh")


def _get_shell_profile(shell_env: str) -> Path | None:
    """Detect shell profile path from SHELL env var.

    Returns:
        Path to the shell profile file, or None if shell is unknown.
    """
    import sys

    # Extract shell name from full path (e.g., /bin/zsh -> zsh)
    shell_name = Path(shell_env).name.lower()

    if "zsh" in shell_name:
        return Path.home() / ".zshrc"
    elif "fish" in shell_name:
        return Path.home() / ".config" / "fish" / "config.fish"
    elif "bash" in shell_name:
        # bash uses different profile on macOS vs Linux
        if sys.platform == "darwin":
            return Path.home() / ".bash_profile"
        else:
            return Path.home() / ".bashrc"
    else:
        return None


def _is_shell_integration_configured(profile: Path) -> bool:
    """Check if shell integration is already in the profile file."""
    if not profile.is_file():
        return False
    try:
        content = profile.read_text(encoding="utf-8")
        return "wade shell-init" in content
    except (OSError, UnicodeDecodeError):
        return False


def _configure_shell_integration(profile: Path, is_fish: bool) -> None:
    """Append shell integration to the profile file.

    Creates the profile if it doesn't exist.
    """
    line = "wade shell-init | source" if is_fish else 'eval "$(wade shell-init)"'
    block = f"\n# wade shell integration\n{line}\n"

    profile.parent.mkdir(parents=True, exist_ok=True)

    if profile.is_file():
        existing = profile.read_text(encoding="utf-8")
        profile.write_text(existing.rstrip("\n") + block, encoding="utf-8")
    else:
        profile.write_text(block.lstrip("\n"), encoding="utf-8")

    logger.info("shell.integration_configured", path=str(profile))


def _prompt_configure_shell_integration(non_interactive: bool) -> None:
    """Prompt user to add shell integration, skipping if already configured."""
    from wade.ui import prompts

    shell_env = os.environ.get("SHELL", "/bin/bash")
    profile = _get_shell_profile(shell_env)

    # If we can't detect the shell, show manual instructions
    if profile is None:
        shell_name = Path(shell_env).name
        console.hint(
            f"Shell '{shell_name}' not auto-detected. "
            f"To enable 'wade cd', add to your shell profile:\n"
            f'  eval "$(wade shell-init)"'
        )
        return

    # If already configured, skip section entirely
    if _is_shell_integration_configured(profile):
        return

    # If non-interactive, skip silently
    if non_interactive:
        return

    console.rule("Shell")

    # Prompt the user
    is_fish = "fish" in shell_env.lower()
    if prompts.confirm(
        "Add shell integration for 'wade cd' (changes to shell profile)?",
        default=True,
    ):
        _configure_shell_integration(profile, is_fish=is_fish)
        console.success(f"Added shell integration to {profile}")
