"""OpenCode adapter — terminal AI coding agent with multi-provider model support."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from wade.ai_tools.base import AbstractAITool
from wade.ai_tools.model_utils import classify_tier_universal, has_date_suffix
from wade.models.ai import (
    AIModel,
    AIToolCapabilities,
    AIToolID,
    AIToolType,
    EffortLevel,
    TokenUsage,
)


class OpenCodeAdapter(AbstractAITool):
    """Adapter for OpenCode CLI.

    OpenCode is a terminal-based AI coding agent that supports 75+ LLM
    providers via the AI SDK. Models are specified as ``provider_id/model_id``
    (e.g., ``anthropic/claude-sonnet-4``).

    Headless mode: ``opencode run "text"`` runs a single
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
            headless_flag="run",
            supports_headless=True,
            supports_effort=True,
            supports_resume=True,
        )

    def get_models(self) -> list[AIModel]:
        """Return known OpenCode models from the static registry."""
        from wade.data import get_models_for_tool

        return [
            AIModel(
                id=mid,
                tier=classify_tier_universal(mid.split("/")[-1] if "/" in mid else mid),
                is_alias=not has_date_suffix(mid.split("/")[-1] if "/" in mid else mid),
            )
            for mid in get_models_for_tool(str(self.TOOL_ID))
        ]

    def build_resume_command(self, session_id: str) -> list[str] | None:
        """Resume an OpenCode session: ``opencode -s <session_id>``."""
        return ["opencode", "-s", session_id]

    def initial_message_args(self, prompt: str) -> list[str]:
        """OpenCode uses --prompt for the initial message."""
        return ["--prompt", prompt]

    def parse_transcript(self, transcript_path: Path) -> TokenUsage:
        return TokenUsage()

    def standardize_model_id(self, raw_model_id: str) -> str:
        """Convert OpenCode's dashed format back to the internal dotted format."""
        if "claude-" in raw_model_id:
            import re

            # Convert anthropic/claude-haiku-4-5 -> anthropic/claude-haiku-4.5
            return re.sub(r"(\d)-(\d)", r"\1.\2", raw_model_id)
        return raw_model_id

    def effort_args(self, effort: EffortLevel) -> list[str]:
        """OpenCode uses ``--variant <level>`` (xhigh and max both map to high)."""
        mapped = "high" if effort in (EffortLevel.XHIGH, EffortLevel.MAX) else effort.value
        return ["--variant", mapped]
