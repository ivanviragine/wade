"""Typed, operation-scoped proof for conservative leftover-worktree cleanup."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class MergeEvidenceState(StrEnum):
    VERIFIED = "verified"
    NOT_MATCHED = "not_matched"
    UNKNOWN = "unknown"


class GitHubRepository(BaseModel):
    host: str
    owner: str
    name: str
    origin_url: str


class MergedPR(BaseModel):
    number: int
    head: str
    merge_commit: str


class MergedPRLookup(BaseModel):
    state: MergeEvidenceState
    reason: str
    pr: MergedPR | None = None


class MergeAssessment(BaseModel):
    state: MergeEvidenceState
    reason: str
    branch: str
    head: str = ""
    base_ref: str = ""
    base_commit: str = ""
    origin_url: str = ""
    pr: MergedPR | None = None


class MergeAssessmentCache(BaseModel):
    """Owned by a single list/remove operation, never persisted across calls."""

    entries: dict[str, MergeAssessment] = Field(default_factory=dict)
