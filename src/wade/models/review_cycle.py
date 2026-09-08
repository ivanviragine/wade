"""Persisted PR-comment review-cycle context."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, field_validator, model_validator

REVIEW_CYCLE_SCHEMA_VERSION = 1
# A persisted context includes JSON syntax and other fields, so its actual
# serialized byte length remains the authoritative limit.
MAX_REVIEW_CYCLE_PAYLOAD_BYTES = 256 * 1024
MAX_REVIEW_FEEDBACK_CHARS = MAX_REVIEW_CYCLE_PAYLOAD_BYTES
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

    def serialized_payload(self) -> bytes:
        """Return the exact UTF-8 state-file representation for this context."""

        return (
            json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=True)
            + "\n"
        ).encode("utf-8")

    @model_validator(mode="after")
    def _serialized_payload_fits_state_file(self) -> ReviewCycleContext:
        if len(self.serialized_payload()) > MAX_REVIEW_CYCLE_PAYLOAD_BYTES:
            raise ValueError("Review-cycle serialized payload exceeds the 256 KiB limit")
        return self
