"""Gemini CLI policy file writer for allowed commands.

Translates WADE's canonical allowed_commands list into a TOML policy file at
``.gemini/policies/wade.toml``.  Gemini CLI uses the Policy Engine (TOML files)
instead of the deprecated ``--allowed-tools`` CLI flag.

Each ``"cmd args_pattern"`` entry in allowed_commands becomes a ``[[rule]]``
block with ``commandPrefix = "cmd"`` and ``decision = "allow"``.
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger()

_PRIORITY = 100


def _command_to_rule(cmd: str) -> str:
    """Translate a canonical command pattern to a TOML [[rule]] block."""
    parts = cmd.split(None, 1)
    binary = parts[0]
    escaped = binary.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "[[rule]]\n"
        f'toolName = "run_shell_command"\n'
        f'commandPrefix = "{escaped}"\n'
        f'decision = "allow"\n'
        f"priority = {_PRIORITY}\n"
    )


def write_gemini_policy(worktree_path: Path, allowed_commands: list[str]) -> None:
    """Write ``.gemini/policies/wade.toml`` from the canonical allowed_commands list.

    Overwrites any previously written policy file.  Idempotent when called
    with the same list.  Removes any existing policy file if *allowed_commands*
    is empty, to prevent stale allow-rules in reused worktrees.
    """
    policy_dir = worktree_path / ".gemini" / "policies"
    policy_file = policy_dir / "wade.toml"

    valid_commands = [cmd for cmd in allowed_commands if cmd.strip()]
    if not valid_commands:
        if policy_file.exists():
            policy_file.unlink()
            logger.info("gemini_policy.removed", path=str(policy_file))
        return

    rules = [_command_to_rule(cmd) for cmd in valid_commands]
    content = "\n".join(rules)

    policy_dir.mkdir(parents=True, exist_ok=True)
    policy_file.write_text(content, encoding="utf-8")
    logger.info("gemini_policy.written", path=str(policy_file), rules=len(rules))
