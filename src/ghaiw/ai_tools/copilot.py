"""GitHub Copilot CLI adapter."""

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
        """Return known Copilot models from the static registry."""
        from ghaiw.data import get_models_for_tool

        return [
            AIModel(
                id=mid,
                tier=classify_tier_universal(mid),
                is_alias=not has_date_suffix(mid),
            )
            for mid in get_models_for_tool(str(self.TOOL_ID))
        ]

    def launch(
        self,
        worktree_path: Path,
        model: str | None = None,
        prompt: str | None = None,
        detach: bool = False,
        transcript_path: Path | None = None,
    ) -> int:
        cmd = self.build_launch_command(model=model, prompt=prompt)
        logger.info("ai_tool.launch", tool="copilot", model=model, cwd=str(worktree_path))
        return run_with_transcript(cmd, transcript_path, cwd=worktree_path)

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        from ghaiw.ai_tools.transcript import parse_copilot_transcript

        return parse_copilot_transcript(transcript_path)

    def is_model_compatible(self, model: str) -> bool:
        """Copilot accepts all model IDs."""
        return True

    def plan_dir_args(self, plan_dir: str) -> list[str]:
        """Copilot uses --add-dir for plan directory access."""
        return ["--add-dir", plan_dir]

    def normalize_model_format(self, model_id: str) -> str:
        """Copilot uses dotted format for Claude models."""
        if model_id.startswith("claude-"):
            import re

            # Convert claude-haiku-4-5 -> claude-haiku-4.5
            # Only convert version number separators (digit-digit)
            return re.sub(r"(\d)-(\d)", r"\1.\2", model_id)
        return model_id
