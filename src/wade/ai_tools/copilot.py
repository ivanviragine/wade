"""GitHub Copilot CLI adapter."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import (
    AIToolCapabilities,
    AIToolID,
    AIToolType,
    TokenUsage,
)


class CopilotAdapter(AbstractAITool):
    """Adapter for GitHub Copilot CLI."""

    TOOL_ID: ClassVar[AIToolID] = AIToolID.COPILOT

    def capabilities(self) -> AIToolCapabilities:
        return AIToolCapabilities(
            tool_id=AIToolID.COPILOT,
            display_name="GitHub Copilot",
            binary="copilot",
            tool_type=AIToolType.TERMINAL,
            supports_model_flag=True,
            headless_flag="--prompt",
            supports_headless=True,
        )

    def initial_message_args(self, prompt: str) -> list[str]:
        """Copilot uses -i for the initial message."""
        return ["-i", prompt]

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        from wade.ai_tools.transcript import parse_copilot_transcript

        return parse_copilot_transcript(transcript_path)

    def is_model_compatible(self, model: str) -> bool:
        """Copilot accepts all model IDs."""
        return True

    def plan_dir_args(self, plan_dir: str) -> list[str]:
        """Copilot uses --add-dir for plan directory access."""
        return ["--add-dir", plan_dir]

    def normalize_model_format(self, model_id: str) -> str:
        """Copilot uses dotted format for Claude models."""
        if model_id.startswith("claude-"):
            import re

            # Convert claude-haiku-4-5 -> claude-haiku-4.5
            # Only convert version number separators (digit-digit)
            return re.sub(r"(\d)-(\d)", r"\1.\2", model_id)
        return model_id
