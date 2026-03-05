"""AI tool domain models — AIToolID, AIModel, ModelTier, TokenUsage."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel


class AIToolID(StrEnum):
    """Canonical identifiers for all supported AI tools."""

    CLAUDE = "claude"
    COPILOT = "copilot"
    GEMINI = "gemini"
    CODEX = "codex"
    ANTIGRAVITY = "antigravity"
    VSCODE = "vscode"
    OPENCODE = "opencode"
    CURSOR = "cursor"


class AIToolType(StrEnum):
    """How the AI tool runs."""

    TERMINAL = "terminal"
    GUI = "gui"


class ModelTier(StrEnum):
    """Capability tier — maps to complexity levels for auto-selection."""

    FAST = "fast"
    BALANCED = "balanced"
    POWERFUL = "powerful"


class AIModel(BaseModel, frozen=True):
    """A concrete model available through an AI tool.

    Models are discovered at runtime by querying the tool (e.g., `claude models`).
    The model ID comes directly from the tool — no format conversion needed.
    """

    id: str
    display_name: str | None = None
    tier: ModelTier | None = None
    is_alias: bool = False

    def __str__(self) -> str:
        return self.id


class AIToolCapabilities(BaseModel, frozen=True):
    """What an AI tool can do — declared by each adapter."""

    tool_id: AIToolID
    display_name: str
    binary: str
    tool_type: AIToolType
    supports_model_flag: bool = True
    model_flag: str = "--model"
    headless_flag: str | None = None
    supports_headless: bool = False
    supports_initial_message: bool = True
    blocks_until_exit: bool = True


class TokenUsage(BaseModel):
    """Token usage metrics from an AI session."""

    total_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    premium_requests: int | None = None
    model_breakdown: list[ModelBreakdown] = []
    raw_transcript_path: Path | None = None
    session_id: str | None = None  # full resume command or session ID as printed by the tool


class ModelBreakdown(BaseModel):
    """Per-model token usage within a session."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    premium_requests: int = 0
