"""Knowledge receipts, proposals and derived reports; no I/O or orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal, TypeAlias
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
ObjectID = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
EntryID = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")]


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator(
        "schema_version",
        "body_digest_version",
        "duplicate_digest_version",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def _version_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Knowledge schema/digest versions require integer versions")
        return value

    @field_validator("recorded_at", "accepted_at", "proposed_at", "at", check_fields=False)
    @classmethod
    def _timestamps(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Knowledge timestamps require an explicit timezone")
        return value.astimezone(UTC)


class GitFileEvidence(KnowledgeModel):
    type: Literal["git_file"] = "git_file"
    path: str
    checked_commit: ObjectID
    blob: ObjectID
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or "\0" in value
            or "\\" in value
            or path.is_absolute()
            or path.as_posix() != value
            or ".." in path.parts
            or value == "."
        ):
            raise ValueError("Evidence paths must be normalized repository-relative literal paths")
        return value

    @model_validator(mode="after")
    def _lines(self) -> GitFileEvidence:
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("Evidence line ranges require both inclusive endpoints")
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("Evidence line range is reversed")
        return self


class TestRunEvidence(KnowledgeModel):
    type: Literal["test_run"] = "test_run"
    check: str = Field(min_length=1)
    command: str = Field(min_length=1)
    checked_commit: ObjectID
    outcome: Literal["passed", "failed"]
    result_digest: Digest | None = None


class ExternalURLEvidence(KnowledgeModel):
    type: Literal["external_url"] = "external_url"
    url: str
    fetched_at: datetime
    content_digest: Digest

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("External evidence requires an HTTP(S) URL without credentials")
        return value

    @field_validator("fetched_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Fetched evidence requires a timezone-aware timestamp")
        return value.astimezone(UTC)


class DecisionEvidence(KnowledgeModel):
    type: Literal["decision"] = "decision"
    reference: str = Field(min_length=1)


Evidence: TypeAlias = Annotated[
    GitFileEvidence | TestRunEvidence | ExternalURLEvidence | DecisionEvidence,
    Field(discriminator="type"),
]


class KnowledgeEvent(KnowledgeModel):
    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1, max_length=200)
    entry_id: EntryID


class AssessmentEvent(KnowledgeEvent):
    type: Literal["assessment"] = "assessment"
    body_digest_version: Literal[1] = 1
    body_digest: Digest
    outcome: Literal["current", "contradicted"]
    checked_at: datetime
    note: str = Field(min_length=1)
    evidence: tuple[Evidence, ...] = Field(min_length=1)
    replaces: tuple[str, ...] = ()
    proposal_id: str | None = None

    @field_validator("checked_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Assessment timestamps require a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _replacement_ids(self) -> AssessmentEvent:
        if self.event_id in self.replaces or len(set(self.replaces)) != len(self.replaces):
            raise ValueError("Assessment replacements must be distinct and cannot replace self")
        return self


class UtilityEvent(KnowledgeEvent):
    type: Literal["utility"] = "utility"
    feedback: Literal["useful", "not-useful"]
    recorded_at: datetime
    note: str = ""
    proposal_id: str | None = None


class RelationshipEvent(KnowledgeEvent):
    type: Literal["relationship"] = "relationship"
    relation: Literal["superseded_by", "retired"]
    related_id: EntryID | None = None
    note: str = ""

    @model_validator(mode="after")
    def _target(self) -> RelationshipEvent:
        if self.relation == "superseded_by" and (
            not self.related_id or self.related_id == self.entry_id
        ):
            raise ValueError("Supersession requires a different target entry")
        return self


class DuplicateEvent(KnowledgeEvent):
    type: Literal["duplicate_of"] = "duplicate_of"
    representative_id: EntryID
    duplicate_digest_version: Literal[1] = 1
    duplicate_digest: Digest

    @model_validator(mode="after")
    def _ordered(self) -> DuplicateEvent:
        if self.representative_id >= self.entry_id:
            raise ValueError("Duplicate representatives must be lexicographically smaller")
        return self


class HistoricalFeedbackEvent(KnowledgeEvent):
    type: Literal["historical_feedback"] = "historical_feedback"
    direction: Literal["up", "down", "stale"]
    recorded_at: datetime
    proposal_id: str | None = None


class ProposalAcceptanceEvent(KnowledgeEvent):
    type: Literal["proposal_accepted"] = "proposal_accepted"
    proposal_id: str
    body_digest: Digest
    accepted_at: datetime


TypedKnowledgeEvent: TypeAlias = Annotated[
    AssessmentEvent
    | UtilityEvent
    | RelationshipEvent
    | DuplicateEvent
    | HistoricalFeedbackEvent
    | ProposalAcceptanceEvent,
    Field(discriminator="type"),
]


class ProposalOwner(KnowledgeModel):
    repository_id: Digest
    operation_id: str = Field(min_length=1, max_length=200)
    session_id: str | None = None
    provider: str | None = None
    task_id: str | None = None
    base_commit: ObjectID


class KnowledgeProposal(KnowledgeModel):
    schema_version: Literal[1] = 1
    proposal_id: str = Field(min_length=1, max_length=200)
    owner: ProposalOwner
    entry_id: EntryID
    body_digest_version: Literal[1] = 1
    body_digest: Digest
    original_payload: dict[str, object]
    proposed_at: datetime
    plan_filename: str | None = None


class ProposalDisposition(KnowledgeModel):
    schema_version: Literal[1] = 1
    proposal_id: str
    disposition_id: str
    action: Literal["retained", "discarded", "accepted", "assessed"]
    at: datetime
    note: str
    event_id: str | None = None


class TaskOperationBinding(KnowledgeModel):
    schema_version: Literal[1] = 1
    repository_id: Digest
    operation_id: str
    session_id: str
    provider: str
    task_id: str = Field(min_length=1)
    originating_task_id: str | None = Field(default=None, min_length=1)
    base_commit: ObjectID
    plan_filename: str | None = None


class EvidenceCheck(KnowledgeModel):
    evidence: Evidence
    applicable: bool
    expected: str | None = None
    actual: str | None = None
    reason: str
    historical: bool = False


class AssessmentReport(KnowledgeModel):
    event: AssessmentEvent
    publication: Literal["integrated", "branch-local", "unavailable"]
    applicable: bool
    active: bool = True
    checks: tuple[EvidenceCheck, ...]


class EntryReport(KnowledgeModel):
    entry_id: str | None
    body_digest: Digest
    kind: str = "note"
    claim_key: str | None = None
    publication: Literal["integrated", "branch-local", "unavailable"]
    assessment: Literal["unverified", "checked-current", "contradicted", "needs-recheck"]
    reasons: tuple[str, ...] = ()
    assessments: tuple[AssessmentReport, ...] = ()
    historical: tuple[str, ...] = ()
    utility_useful: int = 0
    utility_not_useful: int = 0
    legacy_stale: int = 0
    representative_id: str | None = None
    branch_touched: bool = False


class DuplicateGroup(KnowledgeModel):
    representative_id: EntryID
    member_ids: tuple[EntryID, ...]
    duplicate_digest: Digest


class DecodedHistory(KnowledgeModel):
    events: tuple[TypedKnowledgeEvent, ...] = ()
    legacy_records: tuple[dict[str, object], ...] = ()
    problems: tuple[str, ...] = ()


class ReconcileReport(KnowledgeModel):
    schema_version: Literal[1] = 1
    checked_commit: str
    main_commit: str | None = None
    effective_base_commit: str | None = None
    entries: tuple[EntryReport, ...] = ()
    duplicate_groups: tuple[DuplicateGroup, ...] = ()
    problems: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    local_dirty_paths: tuple[str, ...] = ()
    normalization_events_appended: int = 0
    history: DecodedHistory = Field(default_factory=DecodedHistory)

    @property
    def valid(self) -> bool:
        return not self.problems


class GitBlobSnapshot(KnowledgeModel):
    path: str
    object_id: ObjectID
    mode: str


class KnowledgeOperationState(KnowledgeModel):
    schema_version: Literal[1] = 1
    operation_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    base_commit: ObjectID


class KnowledgeActor(KnowledgeModel):
    root: str
    repository_id: Digest
    operation_id: str
    session_id: str | None = None
    provider: str | None = None
    task_id: str | None = None
    session_kind: str | None = None
    base_commit: ObjectID
    author: bool = False


class DetachedObserverOperationBinding(KnowledgeModel):
    """Original detached parent/task association captured before worker launch."""

    schema_version: Literal[1] = 1
    owner: ProposalOwner
    frozen_bundle_digest: Digest
    parent_worktree: str
    parent_session_id: str | None = None
    plan_filename: str | None = None
    task_ids: tuple[str, ...] = ()


class ObserverOperationBinding(KnowledgeModel):
    """Trusted existing-task parent proof captured before a bounded worker starts."""

    schema_version: Literal[1] = 1
    task: TaskOperationBinding
    frozen_bundle_digest: Digest
    parent_worktree: str
