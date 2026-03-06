"""Claude Code adapter."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, ClassVar

import structlog

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import (
    AIToolCapabilities,
    AIToolID,
    AIToolType,
    TokenUsage,
)

logger = structlog.get_logger()


class ClaudeAdapter(AbstractAITool):
    """Adapter for Claude Code CLI."""

    TOOL_ID: ClassVar[AIToolID] = AIToolID.CLAUDE

    def capabilities(self) -> AIToolCapabilities:
        return AIToolCapabilities(
            tool_id=AIToolID.CLAUDE,
            display_name="Claude Code",
            binary="claude",
            tool_type=AIToolType.TERMINAL,
            supports_model_flag=True,
            headless_flag="--print",
            supports_headless=True,
        )

    def initial_message_args(self, prompt: str) -> list[str]:
        """Claude accepts the initial message as a positional argument."""
        return [prompt]

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        usage = super().parse_transcript(transcript_path)
        # Standardize model IDs from Claude's dashed format to canonical dotted format
        for breakdown in usage.model_breakdown:
            breakdown.model = self.standardize_model_id(breakdown.model)
        return usage

    def is_model_compatible(self, model: str) -> bool:
        """Claude CLI accepts only claude-* model IDs."""
        return model.lower().startswith("claude-")

    def plan_mode_args(self) -> list[str]:
        """Claude supports --permission-mode plan."""
        return ["--permission-mode", "plan"]

    def plan_dir_args(self, plan_dir: str) -> list[str]:
        """Claude uses --add-dir for plan directory access."""
        return ["--add-dir", plan_dir]

    def normalize_model_format(self, model_id: str) -> str:
        """Claude uses dashed format for model IDs."""
        if model_id.startswith("claude-"):
            import re

            # Convert claude-haiku-4.5 -> claude-haiku-4-5
            return re.sub(r"(\d)\.(\d)", r"\1-\2", model_id)
        return model_id

    def standardize_model_id(self, raw_model_id: str) -> str:
        """Convert Claude's dashed format back to the internal dotted format."""
        if raw_model_id.startswith("claude-"):
            import re

            # Convert claude-haiku-4-5 -> claude-haiku-4.5
            return re.sub(r"(\d)-(\d)", r"\1.\2", raw_model_id)
        return raw_model_id

    def preserve_session_data(self, worktree_path: Path, main_checkout_path: Path) -> bool:
        """Copy Claude Code session data from worktree to main checkout's project dir.

        Claude Code stores sessions in ``~/.claude/projects/<encoded-path>/``.
        The path encoding replaces every ``/`` with ``-``, so
        ``/Users/foo/bar`` becomes ``-Users-foo-bar``.

        Files are copied without overwriting any that already exist in the
        main checkout's session directory, so existing memory and settings are
        preserved.
        """
        claude_projects_dir = Path.home() / ".claude" / "projects"

        wt_encoded = str(worktree_path).replace("/", "-")
        main_encoded = str(main_checkout_path).replace("/", "-")

        wt_session_dir = claude_projects_dir / wt_encoded
        main_session_dir = claude_projects_dir / main_encoded

        if not wt_session_dir.exists():
            logger.debug(
                "claude.preserve_session_data.no_source",
                worktree=str(worktree_path),
            )
            return True

        main_session_dir.mkdir(parents=True, exist_ok=True)

        copied = 0
        for item in wt_session_dir.iterdir():
            dest = main_session_dir / item.name
            if dest.exists():
                continue
            if item.is_file():
                shutil.copy2(item, dest)
                copied += 1
            elif item.is_dir():
                shutil.copytree(item, dest)
                copied += 1

        logger.info(
            "claude.preserve_session_data.copied",
            worktree=str(worktree_path),
            main=str(main_checkout_path),
            items=copied,
        )
        return True

    def session_data_dirs(self) -> list[str]:
        return [".claude"]

    def allowed_commands_args(self, commands: list[str]) -> list[str]:
        """Translate canonical patterns to Claude --allowedTools flags.

        Canonical ``"cmd args"`` becomes ``"Bash(cmd:args)"``.
        """
        from wade.config.claude_allowlist import canonical_to_claude

        patterns = [canonical_to_claude(cmd) for cmd in commands]
        if not patterns:
            return []
        return ["--allowedTools", *patterns]

    def structured_output_args(self, json_schema: dict[str, Any]) -> list[str]:
        import json

        return ["--output-format", "json", "--json-schema", json.dumps(json_schema)]
