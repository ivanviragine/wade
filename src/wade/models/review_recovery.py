"""Private data shared by guarded Git recovery and its service."""

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RecoveryPhase(StrEnum):
    PREPARING = "preparing"
    RESERVING = "reserving"
    RESERVED = "reserved"
    ALIGNING = "aligning"
    ALIGNED = "aligned"
    POPULATING = "populating"
    ATTACHING = "attaching"
    VERIFYING = "verifying"


class RecoveryBlock(StrEnum):
    DESTINATION = "destination"
    OWNERSHIP = "ownership"
    PRESERVATION = "preservation"
    CREATION = "creation"
    POPULATION = "population"
    VERIFICATION = "verification"
    STATE = "state"


class RecoveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    branch: str = Field(min_length=1)
    target: Path
    selected_oid: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    phase: RecoveryPhase = RecoveryPhase.PREPARING
    backup_ref: str | None = None
    error: str | None = None


class RecoveredWorktree(BaseModel):
    path: Path
    selected_oid: str
    backup_ref: str | None = None
