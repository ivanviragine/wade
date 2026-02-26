"""Claude Code adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import structlog

from ghaiw.ai_tools.base import AbstractAITool
from ghaiw.ai_tools.model_utils import classify_tier_universal, has_date_suffix
from ghaiw.models.ai import (
    AIModel,
    AIToolCapabilities,
    AIToolID,
    AIToolType,
    TokenUsage,
)
from ghaiw.utils.process import run_with_transcript

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
            model_flag="--model",
            headless_flag="--print",
            supports_headless=True,
        )

    def get_models(self) -> list[AIModel]:
        """Return known Claude models from the static registry."""
        from ghaiw.data import get_models_for_tool

        return [
            AIModel(
                id=mid,
                tier=classify_tier_universal(mid),
                is_alias=not has_date_suffix(mid),
            )
            for mid in get_models_for_tool(str(self.TOOL_ID))
        ]

    def launch(
        self,
        worktree_path: Path,
        model: str | None = None,
        prompt: str | None = None,
        detach: bool = False,
        transcript_path: Path | None = None,
        trusted_dirs: list[str] | None = None,
    ) -> int:
        cmd = self.build_launch_command(model=model, prompt=prompt, trusted_dirs=trusted_dirs)

        logger.info("ai_tool.launch", tool="claude", model=model, cwd=str(worktree_path))

        return run_with_transcript(cmd, transcript_path, cwd=worktree_path)

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        # Delegated to transcript.py — this is a stub
        from ghaiw.ai_tools.transcript import parse_claude_transcript

        return parse_claude_transcript(transcript_path)

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

    def structured_output_args(self, json_schema: dict[str, Any]) -> list[str]:
        import json

        return ["--output-format", "json", "--json-schema", json.dumps(json_schema)]
