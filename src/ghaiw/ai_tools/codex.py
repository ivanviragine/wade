"""OpenAI Codex CLI adapter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import structlog

from ghaiw.ai_tools.base import AbstractAITool
from ghaiw.ai_tools.model_utils import (
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
from ghaiw.utils.process import run_with_transcript

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
        """Probe for available Codex models via web scraping.

        Codex has no model listing subcommand. Scrapes the OpenAI Codex
        docs page for `codex -m <model>` usage examples.

        Behavioral ref: lib/init.sh:_init_scrape_models_for_tool() codex case
        """
        scraped = scrape_models_from_docs("codex")
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
        logger.info("ai_tool.launch", tool="codex", model=model, cwd=str(worktree_path))
        return run_with_transcript(cmd, transcript_path, cwd=worktree_path)

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        from ghaiw.ai_tools.transcript import parse_codex_transcript

        return parse_codex_transcript(transcript_path)

    def plan_dir_args(self, plan_dir: str) -> list[str]:
        """Codex uses --add-dir for plan directory access."""
        return ["--add-dir", plan_dir]

    def is_model_compatible(self, model: str) -> bool:
        """Codex accepts codex-*, gpt-*, and o<digit>* model IDs."""
        lower = model.lower()
        if lower.startswith("codex-") or lower.startswith("gpt-"):
            return True
        # o1, o3, o4-mini etc.
        return bool(re.match(r"^o\d", lower))
