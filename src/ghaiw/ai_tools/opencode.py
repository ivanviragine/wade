"""OpenCode adapter — terminal AI coding agent with multi-provider model support."""

from __future__ import annotations

import subprocess
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


class OpenCodeAdapter(AbstractAITool):
    """Adapter for OpenCode CLI.

    OpenCode is a terminal-based AI coding agent that supports 75+ LLM
    providers via the AI SDK. Models are specified as ``provider_id/model_id``
    (e.g., ``anthropic/claude-sonnet-4``).

    Headless mode: ``opencode --prompt "text"`` (or ``-p``) runs a single
    prompt non-interactively without launching the TUI.
    """

    TOOL_ID: ClassVar[AIToolID] = AIToolID.OPENCODE

    def capabilities(self) -> AIToolCapabilities:
        return AIToolCapabilities(
            tool_id=AIToolID.OPENCODE,
            display_name="OpenCode",
            binary="opencode",
            tool_type=AIToolType.TERMINAL,
            supports_model_flag=True,
            model_flag="--model",
            headless_flag="--prompt",
            supports_headless=True,
        )

    def get_models(self) -> list[AIModel]:
        """Probe for available models via ``opencode models``.

        OpenCode lists models from all configured providers. Model IDs are
        in ``provider/model`` format. Tier classification uses the model
        component after the ``/`` separator.
        """
        try:
            result = subprocess.run(
                ["opencode", "models"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                models: list[AIModel] = []
                for line in result.stdout.strip().split("\n"):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if not parts:
                        continue
                    model_id = parts[0]
                    # Skip header rows
                    if model_id.lower() in ("model", "name", "id", "provider"):
                        continue
                    # Use the model name part (after provider/) for tier classification
                    model_part = model_id.split("/")[-1] if "/" in model_id else model_id
                    tier = classify_tier_universal(model_part)
                    is_alias = not has_date_suffix(model_part)
                    models.append(AIModel(id=model_id, tier=tier, is_alias=is_alias))
                if models:
                    return models
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            logger.debug("model_discovery.opencode_probe_failed")

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
        logger.info("ai_tool.launch", tool="opencode", model=model, cwd=str(worktree_path))
        return run_with_transcript(cmd, transcript_path, cwd=worktree_path)

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        return TokenUsage()
