"""Antigravity CLI adapter."""

from __future__ import annotations

import subprocess
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
    ) -> int:
        cmd = [self.capabilities().binary, "."]
        logger.info("ai_tool.launch", tool="antigravity", cwd=str(worktree_path))
        result = subprocess.run(cmd, cwd=worktree_path)
        return result.returncode

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        return TokenUsage()
