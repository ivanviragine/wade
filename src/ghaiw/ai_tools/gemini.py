"""Gemini CLI adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar

import structlog

from ghaiw.ai_tools.base import AbstractAITool
from ghaiw.ai_tools.model_utils import classify_tier_gemini, parse_model_list_output
from ghaiw.models.ai import (
    AIModel,
    AIToolCapabilities,
    AIToolID,
    AIToolType,
    TokenUsage,
)

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
        try:
            result = subprocess.run(
                ["gemini", "--list-models"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                return []

            models = parse_model_list_output(result.stdout)
            for i, model in enumerate(models):
                tier = classify_tier_gemini(model.id)
                if tier:
                    models[i] = AIModel(
                        id=model.id,
                        display_name=model.display_name,
                        tier=tier,
                        is_alias=model.is_alias,
                    )
            return models
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def launch(
        self,
        worktree_path: Path,
        model: str | None = None,
        prompt: str | None = None,
        detach: bool = False,
    ) -> int:
        cmd = self.build_launch_command(model=model)
        logger.info("ai_tool.launch", tool="gemini", model=model, cwd=str(worktree_path))
        result = subprocess.run(cmd, cwd=worktree_path)
        return result.returncode

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        from ghaiw.ai_tools.transcript import parse_gemini_transcript

        return parse_gemini_transcript(transcript_path)

    def is_model_compatible(self, model: str) -> bool:
        """Gemini CLI accepts gemini-* model IDs."""
        return model.lower().startswith("gemini-")
