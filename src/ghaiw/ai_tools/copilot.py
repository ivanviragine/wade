"""GitHub Copilot CLI adapter."""

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
            model_flag="--model",
            headless_flag="--prompt",
            supports_headless=True,
        )

    def get_models(self) -> list[AIModel]:
        # Copilot accepts any model — no probing command available
        return []

    def launch(
        self,
        worktree_path: Path,
        model: str | None = None,
        prompt: str | None = None,
        detach: bool = False,
    ) -> int:
        cmd = self.build_launch_command(model=model, prompt=prompt)
        logger.info("ai_tool.launch", tool="copilot", model=model, cwd=str(worktree_path))
        result = subprocess.run(cmd, cwd=worktree_path)
        return result.returncode

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        from ghaiw.ai_tools.transcript import parse_copilot_transcript

        return parse_copilot_transcript(transcript_path)

    def is_model_compatible(self, model: str) -> bool:
        """Copilot accepts all model IDs."""
        return True
