"""Claude Code adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar

import structlog

from ghaiw.ai_tools.base import AbstractAITool
from ghaiw.ai_tools.model_utils import (
    classify_tier_claude,
    parse_model_list_output,
)
from ghaiw.models.ai import (
    AIModel,
    AIToolCapabilities,
    AIToolID,
    AIToolType,
    TokenUsage,
)

logger = structlog.get_logger()


class ClaudeAdapter(AbstractAITool):
    """Adapter for Claude Code CLI."""

    TOOL_ID: ClassVar[AIToolID] = AIToolID.CLAUDE

    def capabilities(self) -> AIToolCapabilities:
        return AIToolCapabilities(
            tool_id=AIToolID.CLAUDE,
            display_name="Claude Code",
            binary="claude",
            tool_type=AIToolType.TERMINAL,
            supports_model_flag=True,
            model_flag="--model",
            headless_flag="--print",
            supports_headless=True,
        )

    def get_models(self) -> list[AIModel]:
        try:
            result = subprocess.run(
                ["claude", "models"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                return []

            models = parse_model_list_output(result.stdout)
            # Apply Claude-specific tier classification
            for model in models:
                tier = classify_tier_claude(model.id)
                if tier:
                    # Create new model with tier (AIModel is frozen)
                    idx = models.index(model)
                    models[idx] = AIModel(
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
        cmd = self.build_launch_command(model=model, prompt=prompt)
        logger.info("ai_tool.launch", tool="claude", model=model, cwd=str(worktree_path))

        result = subprocess.run(cmd, cwd=worktree_path)
        return result.returncode

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        # Delegated to transcript.py — this is a stub
        from ghaiw.ai_tools.transcript import parse_claude_transcript

        return parse_claude_transcript(transcript_path)

    def is_model_compatible(self, model: str) -> bool:
        """Claude CLI accepts only claude-* model IDs."""
        return model.lower().startswith("claude-")
