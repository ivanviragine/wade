"""Persisted PR-comment review-cycle context."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

REVIEW_CYCLE_SCHEMA_VERSION = 1
MAX_REVIEW_FEEDBACK_CHARS = 200_000
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")


class ReviewCycleContext(BaseModel, frozen=True):
    """The immutable baseline and mutable feedback snapshot for one PR cycle."""

    schema_version: int = Field(default=REVIEW_CYCLE_SCHEMA_VERSION)
    issue_number: str
    pr_number: int = Field(gt=0)
    baseline_commit: str
    feedback: str = Field(min_length=1, max_length=MAX_REVIEW_FEEDBACK_CHARS)

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value: int) -> int:
        if value != REVIEW_CYCLE_SCHEMA_VERSION:
            raise ValueError(f"Unsupported review-cycle schema {value}")
        return value

    @field_validator("issue_number")
    @classmethod
    def _issue_number(cls, value: str) -> str:
        normalized = value.strip().lstrip("#")
        if not normalized.isdigit() or int(normalized) <= 0:
            raise ValueError("Review-cycle issue number must be positive")
        return str(int(normalized))

    @field_validator("baseline_commit")
    @classmethod
    def _commit(cls, value: str) -> str:
        if not _COMMIT_RE.fullmatch(value):
            raise ValueError("Review-cycle baseline must be a lowercase hexadecimal git object id")
        return value

    @field_validator("feedback")
    @classmethod
    def _feedback(cls, value: str) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            raise ValueError("Review-cycle feedback cannot be empty")
        return normalized
