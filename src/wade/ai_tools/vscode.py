"""VS Code adapter — opens the worktree in VS Code."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import structlog

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import (
    AIToolCapabilities,
    AIToolID,
    AIToolType,
    TokenUsage,
)
from wade.utils.process import run_with_transcript

logger = structlog.get_logger()


class VSCodeAdapter(AbstractAITool):
    """Adapter for Visual Studio Code."""

    TOOL_ID: ClassVar[AIToolID] = AIToolID.VSCODE

    def capabilities(self) -> AIToolCapabilities:
        return AIToolCapabilities(
            tool_id=AIToolID.VSCODE,
            display_name="VS Code",
            binary="code",
            tool_type=AIToolType.GUI,
            supports_model_flag=False,
            supports_headless=False,
            supports_initial_message=False,
            blocks_until_exit=False,
        )

    def launch(
        self,
        worktree_path: Path,
        model: str | None = None,
        prompt: str | None = None,
        detach: bool = False,
        transcript_path: Path | None = None,
        trusted_dirs: list[str] | None = None,
    ) -> int:
        cmd = ["code", str(worktree_path)]
        logger.info("ai_tool.launch", tool="vscode", cwd=str(worktree_path))
        return run_with_transcript(cmd, transcript_path, cwd=worktree_path)

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        return TokenUsage()
