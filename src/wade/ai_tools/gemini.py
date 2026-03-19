"""Gemini CLI adapter."""

from __future__ import annotations

from typing import Any, ClassVar

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
            # -p/--prompt triggers non-interactive (headless) mode.
            # Note: -p is marked deprecated in favour of positional prompts,
            # but it is specifically what prevents launching interactive mode.
            headless_flag="-p",
            supports_headless=True,
            supports_yolo=True,
            supports_resume=True,
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

    def yolo_args(self) -> list[str]:
        """Gemini uses ``--yolo``."""
        return ["--yolo"]

    def build_resume_command(self, session_id: str) -> list[str] | None:
        """Resume a Gemini session: ``gemini --resume <session_id>``."""
        return ["gemini", "--resume", session_id]

    def structured_output_args(self, json_schema: dict[str, Any]) -> list[str]:
        """Gemini uses ``--output-format json`` for structured output."""
        return ["--output-format", "json"]

    def allowed_commands_args(self, commands: list[str]) -> list[str]:
        """Translate canonical patterns to Gemini --allowed-tools flags.

        Canonical ``"cmd args"`` becomes ``"shell(cmd:args)"``.
        """
        result: list[str] = []
        for cmd in commands:
            parts = cmd.split(None, 1)
            binary = parts[0]
            args = parts[1] if len(parts) > 1 else ""
            pattern = f"shell({binary}:{args})" if args else f"shell({binary})"
            result.extend(["--allowed-tools", pattern])
        return result
