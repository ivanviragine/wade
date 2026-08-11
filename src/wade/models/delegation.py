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
    # the budget mid-run. Override per command via ``ai.<command>.timeout``. The
    # review/deps services compute the real budget with ``effective_timeout``
    # (scales from payload size + effort unless a config value is set); this
    # default is the fallback for direct constructions that skip that path.
    timeout: int = 600
    # True when ``timeout`` came from an explicit ``ai.<command>.timeout`` rather
    # than from scaling. The headless path honors an explicit budget verbatim —
    # **no automatic retry** — because it is the escape hatch for orchestrators
    # with a hard tool-timeout (the user set it to stay under a fixed cap; a
    # retry would silently blow past that cap). Scaled budgets (this False) retry.
    explicit_timeout: bool = False
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
    # True only when a headless subprocess exceeded its budget. Stays
    # ``success=False`` (a timeout is still a non-success), but lets callers tell
    # a timeout — which may carry partial output and is worth retrying longer —
    # apart from a crash. A crash (CommandError / non-zero exit) keeps this False.
    timed_out: bool = False
