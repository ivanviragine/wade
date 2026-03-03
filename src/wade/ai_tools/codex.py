"""OpenAI Codex CLI adapter."""

from __future__ import annotations

import re
from typing import ClassVar

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import (
    AIToolCapabilities,
    AIToolID,
    AIToolType,
)


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
            headless_flag=None,
            supports_headless=False,
        )

    def initial_message_args(self, prompt: str) -> list[str]:
        """Codex accepts the initial message as a positional argument."""
        return [prompt]

    def plan_dir_args(self, plan_dir: str) -> list[str]:
        """Codex uses --add-dir for plan directory access."""
        return ["--add-dir", plan_dir]

    def trusted_dirs_args(self, dirs: list[str]) -> list[str]:
        """Codex requires workspace-write sandbox mode for --add-dir to take effect."""
        result = ["--sandbox", "workspace-write"]
        for d in dirs:
            result.extend(self.plan_dir_args(d))
        return result

    def is_model_compatible(self, model: str) -> bool:
        """Codex accepts codex-*, gpt-*, and o<digit>* model IDs."""
        lower = model.lower()
        if lower.startswith("codex-") or lower.startswith("gpt-"):
            return True
        # o1, o3, o4-mini etc.
        return bool(re.match(r"^o\d", lower))
