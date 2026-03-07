"""OpenAI Codex CLI adapter."""

from __future__ import annotations

import re
from typing import ClassVar

from wade.ai_tools.base import AbstractAITool
from wade.models.ai import (
    AIToolCapabilities,
    AIToolID,
    AIToolType,
    EffortLevel,
)

# Codex uses "xhigh" for our "max" level
_CODEX_EFFORT_MAP: dict[EffortLevel, str] = {
    EffortLevel.LOW: "low",
    EffortLevel.MEDIUM: "medium",
    EffortLevel.HIGH: "high",
    EffortLevel.MAX: "xhigh",
}


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
            supports_effort=True,
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

    def effort_args(self, effort: EffortLevel) -> list[str]:
        """Codex uses ``-c model_reasoning_effort="<mapped>"``."""
        mapped = _CODEX_EFFORT_MAP.get(effort, effort.value)
        return ["-c", f'model_reasoning_effort="{mapped}"']
