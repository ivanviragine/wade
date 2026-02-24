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
        """Probe for available Claude models.

        Strategy: try `claude models` first (works in modern Claude Code),
        then fall back to web scraping docs.anthropic.com.

        Behavioral ref: lib/init.sh:_init_probe_models_for_tool() claude case
        """
        # Strategy 1: `claude models` CLI subcommand
        try:
            result = subprocess.run(
                ["claude", "models"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                models = parse_model_list_output(result.stdout)
                for i, model in enumerate(models):
                    tier = classify_tier_claude(model.id)
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

        # Strategy 2: Web scrape Anthropic docs
        scraped = scrape_models_from_docs("claude")
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

    def plan_mode_args(self) -> list[str]:
        """Claude supports --permission-mode plan."""
        return ["--permission-mode", "plan"]

    def plan_dir_args(self, plan_dir: str) -> list[str]:
        """Claude uses --add-dir for plan directory access."""
        return ["--add-dir", plan_dir]

    def normalize_model_format(self, model_id: str) -> str:
        """Claude uses dashed format for model IDs."""
        if model_id.startswith("claude-"):
            import re

            # Convert claude-haiku-4.5 -> claude-haiku-4-5
            return re.sub(r"(\d)\.(\d)", r"\1-\2", model_id)
        return model_id
