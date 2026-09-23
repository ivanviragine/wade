"""WADE-owned handoff from an ordinary native planning terminal."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wade.models.plan_bundle import PlanBundle


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
    persisted_issues: dict[str, str] = Field(default_factory=dict)
    # A task body carries each marker before its external creation.  If the
    # following local progress write fails, recovery can find that exact task
    # instead of creating a duplicate.
    pending_issue_markers: dict[str, str] = Field(default_factory=dict)


class InteractivePlanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    bundle_digest: str
    content_digest: str
    self_review_pending: bool = False


class InteractivePlanReviews(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plans: dict[str, InteractivePlanReview] = Field(default_factory=dict)
