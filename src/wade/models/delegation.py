"""Delegation domain models — generic "delegate and wait" infrastructure."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class DelegationMode(StrEnum):
    """How a delegation request is executed."""

    PROMPT = "prompt"
    INTERACTIVE = "interactive"
    HEADLESS = "headless"


class DelegationRequest(BaseModel):
    """What to delegate and how."""

    mode: DelegationMode
    prompt: str
    ai_tool: str | None = None
    model: str | None = None
    effort: str | None = None
    cwd: Path | None = None
    timeout: int = 120
    output_file: Path | None = None
    trusted_dirs: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=list)


class DelegationResult(BaseModel):
    """Outcome of a delegation request."""

    success: bool
    feedback: str
    mode: DelegationMode
    exit_code: int = 0
