"""Cursor CLI adapter."""

from __future__ import annotations

from typing import ClassVar

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import (
    AIToolCapabilities,
    AIToolID,
    AIToolType,
)


class CursorAdapter(AbstractAITool):
    """Adapter for Cursor CLI (``agent`` binary).

    Cursor is an AI-powered IDE with a terminal CLI that supports plan mode,
    model selection, headless execution, and skill discovery.
    """

    TOOL_ID: ClassVar[AIToolID] = AIToolID.CURSOR

    def capabilities(self) -> AIToolCapabilities:
        return AIToolCapabilities(
            tool_id=AIToolID.CURSOR,
            display_name="Cursor",
            binary="cursor",
            tool_type=AIToolType.TERMINAL,
            supports_model_flag=True,
            headless_flag="--print",
            supports_headless=True,
        )

    def initial_message_args(self, prompt: str) -> list[str]:
        """Cursor accepts the initial message as a positional argument."""
        return [prompt]

    def is_model_compatible(self, model: str) -> bool:
        """Cursor accepts all model IDs."""
        return True

    def plan_mode_args(self) -> list[str]:
        """Cursor supports ``--mode plan``."""
        return ["--mode", "plan"]

    def normalize_model_format(self, model_id: str) -> str:
        """Cursor uses dotted format for Claude models."""
        if model_id.startswith("claude-"):
            import re

            # Convert claude-haiku-4-5 -> claude-haiku-4.5
            return re.sub(r"(\d)-(\d)", r"\1.\2", model_id)
        return model_id
