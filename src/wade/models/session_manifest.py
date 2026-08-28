"""Versioned immutable session, delegation, and review binding records."""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from wade.models.skill import BindingComponent, ResolvedSkill, SkillSlot
from wade.models.workflow import AICommandKey, DelegationKind, SessionKind

SESSION_MANIFEST_SCHEMA_VERSION = 1
DELEGATION_MANIFEST_SCHEMA_VERSION = 1
REVIEW_RECORD_SCHEMA_VERSION = 1
DOCUMENTATION_RECEIPT_SCHEMA_VERSION = 1
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")


def compute_binding_digest(components: tuple[BindingComponent, ...]) -> str:
    """Hash ordered binding components using the documented canonical JSON form."""

    payload = [
        {
            "position": component.position,
            "canonical_ref": component.canonical_ref,
            "content_digest": component.content_digest,
        }
        for component in components
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def binding_components(skills: tuple[ResolvedSkill, ...]) -> tuple[BindingComponent, ...]:
    """Build canonical ordered digest inputs from resolved skills."""

    return tuple(
        BindingComponent(
            position=position,
            canonical_ref=skill.canonical_ref,
            content_digest=skill.content_digest,
        )
        for position, skill in enumerate(skills)
    )


class ResolvedBinding(BaseModel, frozen=True):
    """An ordered materialized binding plus its composite identity."""

    digest: str
    skills: tuple[ResolvedSkill, ...]

    @field_validator("digest")
    @classmethod
    def _digest_shape(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("Binding digest must be sha256:<64 lowercase hex characters>")
        return value

    @model_validator(mode="after")
    def _digest_matches_components(self) -> ResolvedBinding:
        if not self.skills:
            raise ValueError("A resolved binding cannot be empty")
        expected = compute_binding_digest(binding_components(self.skills))
        if self.digest != expected:
            raise ValueError("Binding digest does not match its ordered skills")
        return self

    @classmethod
    def from_skills(cls, skills: tuple[ResolvedSkill, ...]) -> ResolvedBinding:
        components = binding_components(skills)
        return cls(digest=compute_binding_digest(components), skills=skills)


class SessionManifest(BaseModel, frozen=True):
    """Frozen workflow and active skill state for one interactive session."""

    schema_version: int = Field(default=SESSION_MANIFEST_SCHEMA_VERSION)
    session: SessionKind
    workflow_revision: int
    bundle_digest: str
    task_id: str | None = None
    ai_command: AICommandKey
    bindings: dict[SkillSlot, ResolvedBinding]

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value: int) -> int:
        if value != SESSION_MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"Unsupported session manifest schema {value}")
        return value

    @field_validator("bundle_digest")
    @classmethod
    def _bundle_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("Bundle digest must be sha256:<64 lowercase hex characters>")
        return value

    @model_validator(mode="after")
    def _interactive_bindings(self) -> SessionManifest:
        if set(self.bindings) != {SkillSlot.WORK, SkillSlot.REVIEW}:
            raise ValueError("Interactive session manifests require work and review bindings")
        return self


class DelegationManifest(BaseModel, frozen=True):
    """Frozen active skill state for one bounded delegation invocation."""

    schema_version: int = Field(default=DELEGATION_MANIFEST_SCHEMA_VERSION)
    delegation: DelegationKind
    invocation_id: str
    host_session: SessionKind | None = None
    ai_command: AICommandKey
    binding: ResolvedBinding

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value: int) -> int:
        if value != DELEGATION_MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"Unsupported delegation manifest schema {value}")
        return value

    @field_validator("invocation_id")
    @classmethod
    def _invocation(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}", value):
            raise ValueError("invocation_id must be 32 lowercase hex characters")
        return value


class ReviewOutcome(StrEnum):
    """Deterministic result of one binding-aware review attempt."""

    REVIEWED = "reviewed"
    NO_DIFF = "no-diff"
    TIMED_OUT = "timed-out"
    NOTHING_STAGED = "nothing-staged"

    @property
    def consumes_pass(self) -> bool:
        return self in {ReviewOutcome.REVIEWED, ReviewOutcome.TIMED_OUT}

    @property
    def satisfies_review(self) -> bool:
        return self in {ReviewOutcome.REVIEWED, ReviewOutcome.NO_DIFF}


class ReviewBinding(BaseModel, frozen=True):
    """Minimal ordered binding identity stored in a durable review record."""

    digest: str
    skills: tuple[BindingComponent, ...]

    @field_validator("digest")
    @classmethod
    def _digest_shape(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("Binding digest must be sha256:<64 lowercase hex characters>")
        return value

    @model_validator(mode="after")
    def _binding_is_canonical(self) -> ReviewBinding:
        if not self.skills:
            raise ValueError("A review binding cannot be empty")
        if tuple(component.position for component in self.skills) != tuple(range(len(self.skills))):
            raise ValueError("Review binding positions must be contiguous and ordered")
        if compute_binding_digest(self.skills) != self.digest:
            raise ValueError("Review binding digest does not match its ordered skills")
        return self

    @classmethod
    def from_resolved(cls, binding: ResolvedBinding) -> ReviewBinding:
        return cls(digest=binding.digest, skills=binding_components(binding.skills))


class ReviewRecord(BaseModel, frozen=True):
    """One idempotent review outcome for a commit and methodology binding."""

    schema_version: int = Field(default=REVIEW_RECORD_SCHEMA_VERSION)
    commit: str
    delegation: DelegationKind
    outcome: ReviewOutcome
    consumes_pass: bool
    binding: ReviewBinding

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value: int) -> int:
        if value != REVIEW_RECORD_SCHEMA_VERSION:
            raise ValueError(f"Unsupported review record schema {value}")
        return value

    @field_validator("commit")
    @classmethod
    def _commit(cls, value: str) -> str:
        if not _COMMIT_RE.fullmatch(value):
            raise ValueError("Review commit must be a lowercase hexadecimal git object id")
        return value

    @model_validator(mode="after")
    def _outcome_semantics_are_fixed(self) -> ReviewRecord:
        if self.consumes_pass is not self.outcome.consumes_pass:
            raise ValueError("Review outcome and consumes_pass disagree")
        return self

    @property
    def satisfies_review(self) -> bool:
        return self.outcome.satisfies_review


class DocumentationDecision(StrEnum):
    """Explicit result of the mandatory workflow documentation decision."""

    UPDATED = "updated"
    NOT_NEEDED = "not-needed"


class DocumentationReceipt(BaseModel, frozen=True):
    """Deterministic evidence that documentation impact was considered."""

    schema_version: int = Field(default=DOCUMENTATION_RECEIPT_SCHEMA_VERSION)
    commit: str
    session: SessionKind
    decision: DocumentationDecision
    reason: str | None = None

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value: int) -> int:
        if value != DOCUMENTATION_RECEIPT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported documentation receipt schema {value}")
        return value

    @field_validator("commit")
    @classmethod
    def _commit(cls, value: str) -> str:
        if not _COMMIT_RE.fullmatch(value):
            raise ValueError("Documentation commit must be a lowercase hexadecimal object id")
        return value

    @field_validator("reason")
    @classmethod
    def _reason_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Documentation reason cannot be empty")
        return normalized

    @model_validator(mode="after")
    def _decision_has_expected_reason(self) -> DocumentationReceipt:
        if self.decision is DocumentationDecision.NOT_NEEDED and self.reason is None:
            raise ValueError("not-needed documentation decisions require a reason")
        if self.decision is DocumentationDecision.UPDATED and self.reason is not None:
            raise ValueError("updated documentation decisions do not accept a reason")
        if self.session not in {SessionKind.IMPLEMENTATION, SessionKind.REVIEW_PR_COMMENTS}:
            raise ValueError("Documentation receipts apply only to change-producing sessions")
        return self
