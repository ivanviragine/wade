"""Delegation domain models — generic "delegate and wait" infrastructure."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from wade.models.permission import PermissionMode


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
    # Default headless subprocess budget, in seconds. 600s (not 300s) so a
    # high-effort review/deps run over a large diff finishes rather than tripping
    # the budget mid-run. Override per command via ``ai.<command>.timeout``.
    timeout: int = 600
    output_file: Path | None = None
    trusted_dirs: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=list)
    permission_mode: PermissionMode = PermissionMode.DEFAULT


class DelegationResult(BaseModel):
    """Outcome of a delegation request."""

    success: bool
    feedback: str
    mode: DelegationMode
    exit_code: int = 0
    skipped: bool = False
