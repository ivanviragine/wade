"""Cursor CLI adapter."""

from __future__ import annotations

from typing import ClassVar

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import (
    AIToolCapabilities,
    AIToolID,
    AIToolType,
    EffortLevel,
)


class CursorAdapter(AbstractAITool):
    """Adapter for Cursor CLI (``agent`` binary).

    Cursor is an AI-powered IDE with a terminal CLI that supports plan mode,
    model selection, headless execution, and skill discovery.

    Cursor uses its own model ID namespace — e.g. ``sonnet-4.6``, ``opus-4.6``,
    ``gpt-5.3-codex`` — so no format normalization is needed.

    For high/max effort, Cursor uses thinking model variants (e.g.,
    ``sonnet-4.6-thinking``) rather than a separate effort flag.
    """

    TOOL_ID: ClassVar[AIToolID] = AIToolID.CURSOR

    def capabilities(self) -> AIToolCapabilities:
        return AIToolCapabilities(
            tool_id=AIToolID.CURSOR,
            display_name="Cursor",
            binary="agent",
            tool_type=AIToolType.TERMINAL,
            supports_model_flag=True,
            headless_flag="--print",
            supports_headless=True,
            supports_effort=True,
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

    def resolve_effort_model(self, model: str | None, effort: EffortLevel) -> str | None:
        """For high/max effort, append ``-thinking`` to the model ID."""
        if (
            effort in (EffortLevel.HIGH, EffortLevel.MAX)
            and model
            and not model.endswith("-thinking")
        ):
            return f"{model}-thinking"
        return model
