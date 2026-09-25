"""WADE-owned handoff from an ordinary native planning terminal."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wade.models.config import KnowledgeConfig, ProjectSettings, ProviderConfig
from wade.models.plan_bundle import PlanBundle, PlanKnowledgeVote


class InteractivePlanState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    session_id: str
    tool: str
    model: str | None = None
    effort: str | None = None
    sandbox: bool | None = None
    review_required: bool
    knowledge_required: bool = False
    bundle_digest: str
    bundle: PlanBundle | None = None
    completed: bool = False


class PlanHandoffProgress(BaseModel):
    """Durable parent-side progress for one completed planning handoff."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    session_id: str
    model: str | None = None
    # The external task identities below are meaningful only for these exact
    # settings. Optional fields let old retained files parse so recovery can
    # reject their missing binding with a clear, safe error.
    provider: ProviderConfig | None = None
    project: ProjectSettings | None = None
    knowledge: KnowledgeConfig | None = None
    knowledge_required: bool | None = None
    # The original vote set is persisted before staging any vote. This prevents
    # a recovered artifact with the same session ID from changing a vote while
    # retaining its already-delivered event identity.
    knowledge_votes: tuple[PlanKnowledgeVote, ...] | None = None
    persisted_issues: dict[str, str] = Field(default_factory=dict)
    # Each persisted task's draft PR contains this exact plan content. Recovery
    # must reject a reviewed edit rather than reusing the task with a stale PR.
    persisted_plan_digests: dict[str, str] = Field(default_factory=dict)
    # A task body carries each marker before its external creation.  If the
    # following local progress write fails, recovery can find that exact task
    # instead of creating a duplicate.
    pending_issue_markers: dict[str, str] = Field(default_factory=dict)
    # A recovered task may be renamed to match a reviewed plan after its draft
    # PR has been created. Retain the pre-rename title that identifies that PR's
    # branch until the task/PR mapping is durably persisted, so a retry does not
    # derive a second branch from the new task title.
    pending_issue_branch_titles: dict[str, str] = Field(default_factory=dict)
    # Once every split-plan task is durable, the original task's visible
    # supersede actions are recorded before finalization. Recovery can then
    # resume finalization without posting the comment or closing it again.
    superseded_issue_ids: list[str] = Field(default_factory=list)


class PlanHandoffBinding(BaseModel):
    """Immutable launch settings required to safely recover a completed handoff."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    model: str | None = None
    provider: ProviderConfig
    project: ProjectSettings
    knowledge: KnowledgeConfig
    knowledge_required: bool


class InteractivePlanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    bundle_digest: str
    content_digest: str
    self_review_pending: bool = False


class InteractivePlanReviews(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plans: dict[str, InteractivePlanReview] = Field(default_factory=dict)
