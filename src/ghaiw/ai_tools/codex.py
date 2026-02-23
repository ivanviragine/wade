"""OpenAI Codex CLI adapter."""

from __future__ import annotations

import re
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


class CodexAdapter(AbstractAITool):
    """Adapter for OpenAI Codex CLI."""

    TOOL_ID: ClassVar[AIToolID] = AIToolID.CODEX

    def capabilities(self) -> AIToolCapabilities:
        return AIToolCapabilities(
            tool_id=AIToolID.CODEX,
            display_name="Codex CLI",
            binary="codex",
            tool_type=AIToolType.TERMINAL,
            supports_model_flag=True,
            model_flag="--model",
            headless_flag=None,
            supports_headless=False,
        )

    def get_models(self) -> list[AIModel]:
        # Codex doesn't have a built-in model listing command
        return []

    def launch(
        self,
        worktree_path: Path,
        model: str | None = None,
        prompt: str | None = None,
        detach: bool = False,
    ) -> int:
        cmd = self.build_launch_command(model=model)
        logger.info("ai_tool.launch", tool="codex", model=model, cwd=str(worktree_path))
        result = subprocess.run(cmd, cwd=worktree_path)
        return result.returncode

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        from ghaiw.ai_tools.transcript import parse_codex_transcript

        return parse_codex_transcript(transcript_path)

    def is_model_compatible(self, model: str) -> bool:
        """Codex accepts codex-*, gpt-*, and o<digit>* model IDs."""
        lower = model.lower()
        if lower.startswith("codex-") or lower.startswith("gpt-"):
            return True
        # o1, o3, o4-mini etc.
        return bool(re.match(r"^o\d", lower))
