"""Gemini CLI adapter."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

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
            model_flag="--model",
            headless_flag=None,
            supports_headless=False,
        )

    def get_models(self) -> list[AIModel]:
        """Return known Gemini models from the static registry."""
        from ghaiw.data import get_models_for_tool

        return [
            AIModel(
                id=mid,
                tier=classify_tier_universal(mid),
                is_alias=not has_date_suffix(mid),
            )
            for mid in get_models_for_tool(str(self.TOOL_ID))
        ]

    def initial_message_args(self, prompt: str) -> list[str]:
        """Gemini accepts the initial message as a positional argument."""
        return [prompt]

    def launch(
        self,
        worktree_path: Path,
        model: str | None = None,
        prompt: str | None = None,
        detach: bool = False,
        transcript_path: Path | None = None,
        trusted_dirs: list[str] | None = None,
    ) -> int:
        cmd = self.build_launch_command(
            model=model, initial_message=prompt, trusted_dirs=trusted_dirs
        )
        logger.info("ai_tool.launch", tool="gemini", model=model, cwd=str(worktree_path))
        return run_with_transcript(cmd, transcript_path, cwd=worktree_path)

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        from ghaiw.ai_tools.transcript import parse_gemini_transcript

        return parse_gemini_transcript(transcript_path)

    def is_model_compatible(self, model: str) -> bool:
        """Gemini CLI accepts gemini-* model IDs."""
        return model.lower().startswith("gemini-")

    def plan_mode_args(self) -> list[str]:
        """Gemini supports --approval-mode plan."""
        return ["--approval-mode", "plan"]

    def plan_dir_args(self, plan_dir: str) -> list[str]:
        """Gemini uses --include-directories for plan directory access."""
        return ["--include-directories", plan_dir]
