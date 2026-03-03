"""Gemini CLI adapter."""

from __future__ import annotations

from typing import ClassVar

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import (
    AIToolCapabilities,
    AIToolID,
    AIToolType,
)


class GeminiAdapter(AbstractAITool):
    """Adapter for Gemini CLI."""

    TOOL_ID: ClassVar[AIToolID] = AIToolID.GEMINI

    def capabilities(self) -> AIToolCapabilities:
        return AIToolCapabilities(
            tool_id=AIToolID.GEMINI,
            display_name="Gemini CLI",
            binary="gemini",
            tool_type=AIToolType.TERMINAL,
            supports_model_flag=True,
            headless_flag=None,
            supports_headless=False,
        )

    def initial_message_args(self, prompt: str) -> list[str]:
        """Gemini accepts the initial message as a positional argument."""
        return [prompt]

    def is_model_compatible(self, model: str) -> bool:
        """Gemini CLI accepts gemini-* model IDs."""
        return model.lower().startswith("gemini-")

    def plan_mode_args(self) -> list[str]:
        """Gemini supports --approval-mode plan."""
        return ["--approval-mode", "plan"]

    def plan_dir_args(self, plan_dir: str) -> list[str]:
        """Gemini uses --include-directories for plan directory access."""
        return ["--include-directories", plan_dir]
