"""Gemini CLI adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar

import structlog

from ghaiw.ai_tools.base import AbstractAITool
from ghaiw.ai_tools.model_utils import (
    classify_tier_gemini,
    parse_model_list_output,
    raw_ids_to_models,
    scrape_models_from_docs,
)
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
        """Probe for available Gemini models.

        Strategy: try `gemini --list-models` first, then fall back to
        web scraping geminicli.com docs.

        Behavioral ref: lib/init.sh:_init_probe_models_for_tool() gemini case
        """
        # Strategy 1: `gemini --list-models` CLI flag
        try:
            result = subprocess.run(
                ["gemini", "--list-models"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
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
                if models:
                    return models
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # Strategy 2: Web scrape geminicli.com docs
        scraped = scrape_models_from_docs("gemini")
        if scraped:
            return raw_ids_to_models(scraped)

        return []

    def launch(
        self,
        worktree_path: Path,
        model: str | None = None,
        prompt: str | None = None,
        detach: bool = False,
        transcript_path: Path | None = None,
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

    def plan_mode_args(self) -> list[str]:
        """Gemini supports --approval-mode plan."""
        return ["--approval-mode", "plan"]

    def plan_dir_args(self, plan_dir: str) -> list[str]:
        """Gemini uses --include-directories for plan directory access."""
        return ["--include-directories", plan_dir]
