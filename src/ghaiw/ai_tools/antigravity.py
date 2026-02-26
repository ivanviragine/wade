"""Antigravity CLI adapter."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import structlog

from ghaiw.ai_tools.base import AbstractAITool
from ghaiw.models.ai import (
    AIModel,
    AIToolCapabilities,
    AIToolID,
    AIToolType,
    TokenUsage,
)
from ghaiw.utils.process import run_with_transcript

logger = structlog.get_logger()


class AntigravityAdapter(AbstractAITool):
    """Adapter for Antigravity CLI."""

    TOOL_ID: ClassVar[AIToolID] = AIToolID.ANTIGRAVITY

    def capabilities(self) -> AIToolCapabilities:
        return AIToolCapabilities(
            tool_id=AIToolID.ANTIGRAVITY,
            display_name="Antigravity",
            binary="antigravity",
            tool_type=AIToolType.TERMINAL,
            supports_model_flag=False,
            headless_flag=None,
            supports_headless=False,
        )

    def get_models(self) -> list[AIModel]:
        return []

    def launch(
        self,
        worktree_path: Path,
        model: str | None = None,
        prompt: str | None = None,
        detach: bool = False,
        transcript_path: Path | None = None,
        trusted_dirs: list[str] | None = None,
    ) -> int:
        cmd = [self.capabilities().binary, "."]
        logger.info("ai_tool.launch", tool="antigravity", cwd=str(worktree_path))
        return run_with_transcript(cmd, transcript_path, cwd=worktree_path)

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        return TokenUsage()
