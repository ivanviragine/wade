"""Canonical session and bounded-delegation definitions.

The registries here are pure data and the single source of truth for workflow
identity, fixed lifecycle behavior, and dynamic methodology slots.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, model_validator

from wade.models.hooks import SessionPhase, StopGuard
from wade.models.skill import SkillRef, SkillSlot


class SessionKind(StrEnum):
    """A WADE-owned session shell with deterministic lifecycle behavior."""

    PLAN = "plan"
    IMPLEMENTATION = "implementation"
    REVIEW_PR_COMMENTS = "review-pr-comments"
    DEPS = "deps"


class DelegationKind(StrEnum):
    """A bounded AI operation invoked by a fixed WADE contract."""

    PLAN_REVIEW = "plan-review"
    CODE_REVIEW = "code-review"
    BATCH_REVIEW = "batch-review"
    DEPENDENCY_ANALYSIS = "dependency-analysis"


class AICommandKey(StrEnum):
    """Per-command AI configuration keys; deliberately not workflow identities."""

    PLAN = "plan"
    DEPS = "deps"
    IMPLEMENT = "implement"
    REVIEW_PLAN = "review_plan"
    REVIEW_IMPLEMENTATION = "review_implementation"
    REVIEW_BATCH = "review_batch"
    REVIEW_PR_COMMENTS = "review_pr_comments"


class CompletionPolicy(StrEnum):
    """Fixed deterministic completion policy associated with a session."""

    PLAN = "plan"
    IMPLEMENTATION = "implementation"
    REVIEW_PR_COMMENTS = "review-pr-comments"
    NONE = "none"


class DelegationHostMode(StrEnum):
    """Whether a delegation may consume a mapped host-session slot."""

    MAPPED = "mapped"
    ALWAYS_FOREIGN = "always-foreign"


class ReadinessRequirements(BaseModel, frozen=True):
    """Runtime capabilities required by an agent-side session."""

    requires_git_metadata_write: bool = False
    requires_github: bool = False
    supports_staged_knowledge_votes: bool = False


class SessionDefinition(BaseModel, frozen=True):
    """Typed definition for one supported session shell."""

    kind: SessionKind
    ai_command: AICommandKey
    workflow_template: str | None
    workflow_revision: int | None
    launch_prompt: str | None
    session_phase: SessionPhase | None
    steps: tuple[str, ...]
    default_skills: dict[SkillSlot, tuple[SkillRef, ...]]
    support_skills: tuple[str, ...]
    readiness: ReadinessRequirements
    plan_mode: bool
    stop_guard: StopGuard | None
    completion_policy: CompletionPolicy
    knowledge_label: str
    pr_phase_label: str

    @model_validator(mode="after")
    def _interactive_tuple_is_complete(self) -> SessionDefinition:
        interactive = (
            self.workflow_template,
            self.workflow_revision,
            self.launch_prompt,
            self.session_phase,
        )
        if any(value is not None for value in interactive) and any(
            value is None for value in interactive
        ):
            raise ValueError(
                "Interactive sessions require workflow template/revision, launch prompt, "
                "and SessionStart phase together"
            )
        if self.workflow_template is None:
            if self.steps or self.default_skills or self.support_skills or self.stop_guard:
                raise ValueError("Non-interactive sessions cannot expose workflow-owned fields")
            if self.plan_mode:
                raise ValueError("Non-interactive sessions cannot enable plan mode")
        elif not self.steps:
            raise ValueError("Interactive sessions require stable workflow step IDs")
        if self.plan_mode != (self.kind is SessionKind.PLAN):
            raise ValueError("Only the plan session may enable plan mode")
        if set(self.default_skills) - {SkillSlot.WORK, SkillSlot.REVIEW}:
            raise ValueError("Session definitions expose only work and review skill slots")
        return self


class DelegationDefinition(BaseModel, frozen=True):
    """Typed definition for one bounded AI operation."""

    kind: DelegationKind
    ai_command: AICommandKey
    contract_template: str
    default_skills: tuple[SkillRef, ...]
    input_contract: str
    result_contract: str
    containing_session: SessionKind | None = None
    host_mode: DelegationHostMode
    host_slot: SkillSlot | None = None

    @model_validator(mode="after")
    def _host_mapping_is_explicit(self) -> DelegationDefinition:
        if self.host_mode is DelegationHostMode.MAPPED:
            if self.host_slot is None:
                raise ValueError("Mapped delegations require a host slot")
        elif self.host_slot is not None:
            raise ValueError("Always-foreign delegations cannot declare a host slot")
        if not self.default_skills:
            raise ValueError("Delegations require at least one default skill")
        return self


SESSION_DEFINITIONS: dict[SessionKind, SessionDefinition] = {
    SessionKind.PLAN: SessionDefinition(
        kind=SessionKind.PLAN,
        ai_command=AICommandKey.PLAN,
        workflow_template="plan.md",
        workflow_revision=1,
        launch_prompt="plan-session.md",
        session_phase=SessionPhase.PLAN,
        steps=(
            "check-readiness",
            "understand-goal",
            "research",
            "design",
            "confirm-plan",
            "write-plan-files",
            "user-review",
            "method-review",
            "knowledge",
            "validate",
            "present-results",
        ),
        default_skills={
            SkillSlot.WORK: (SkillRef.model_validate("builtin:planning"),),
            SkillSlot.REVIEW: (SkillRef.model_validate("builtin:plan-review"),),
        },
        support_skills=("task", "knowledge"),
        readiness=ReadinessRequirements(supports_staged_knowledge_votes=True),
        plan_mode=True,
        stop_guard=StopGuard.PLAN_COMPLETE,
        completion_policy=CompletionPolicy.PLAN,
        knowledge_label="plan",
        pr_phase_label="Plan",
    ),
    SessionKind.IMPLEMENTATION: SessionDefinition(
        kind=SessionKind.IMPLEMENTATION,
        ai_command=AICommandKey.IMPLEMENT,
        workflow_template="implementation.md",
        workflow_revision=1,
        launch_prompt="implement-context.md",
        session_phase=SessionPhase.IMPLEMENT,
        steps=(
            "check-readiness",
            "catch-up",
            "understand-plan",
            "implement",
            "verify",
            "method-review",
            "documentation",
            "knowledge",
            "pr-summary",
            "sync",
            "done",
            "present-results",
        ),
        default_skills={
            SkillSlot.WORK: (SkillRef.model_validate("builtin:implementation"),),
            SkillSlot.REVIEW: (SkillRef.model_validate("builtin:code-review"),),
        },
        support_skills=("task", "knowledge"),
        readiness=ReadinessRequirements(
            requires_git_metadata_write=True,
            requires_github=True,
        ),
        plan_mode=False,
        stop_guard=StopGuard.SESSION_COMPLETE,
        completion_policy=CompletionPolicy.IMPLEMENTATION,
        knowledge_label="implement",
        pr_phase_label="Implement",
    ),
    SessionKind.REVIEW_PR_COMMENTS: SessionDefinition(
        kind=SessionKind.REVIEW_PR_COMMENTS,
        ai_command=AICommandKey.REVIEW_PR_COMMENTS,
        workflow_template="review-pr-comments.md",
        workflow_revision=1,
        launch_prompt="review-pr-comments.md",
        session_phase=SessionPhase.REVIEW,
        steps=(
            "check-readiness",
            "fetch-comments",
            "verify-feedback",
            "address-feedback",
            "verify",
            "method-review",
            "documentation",
            "knowledge",
            "pr-summary",
            "sync",
            "resolve-threads",
            "done",
            "present-results",
        ),
        default_skills={
            SkillSlot.WORK: (SkillRef.model_validate("builtin:review-comments"),),
            SkillSlot.REVIEW: (SkillRef.model_validate("builtin:code-review"),),
        },
        support_skills=("task", "knowledge"),
        readiness=ReadinessRequirements(
            requires_git_metadata_write=True,
            requires_github=True,
        ),
        plan_mode=False,
        stop_guard=StopGuard.SESSION_COMPLETE,
        completion_policy=CompletionPolicy.REVIEW_PR_COMMENTS,
        knowledge_label="review",
        pr_phase_label="Review",
    ),
    SessionKind.DEPS: SessionDefinition(
        kind=SessionKind.DEPS,
        ai_command=AICommandKey.DEPS,
        workflow_template=None,
        workflow_revision=None,
        launch_prompt=None,
        session_phase=None,
        steps=(),
        default_skills={},
        support_skills=(),
        readiness=ReadinessRequirements(supports_staged_knowledge_votes=True),
        plan_mode=False,
        stop_guard=None,
        completion_policy=CompletionPolicy.NONE,
        knowledge_label="deps",
        pr_phase_label="Dependencies",
    ),
}


DELEGATION_DEFINITIONS: dict[DelegationKind, DelegationDefinition] = {
    DelegationKind.PLAN_REVIEW: DelegationDefinition(
        kind=DelegationKind.PLAN_REVIEW,
        ai_command=AICommandKey.REVIEW_PLAN,
        contract_template="review-plan.md",
        default_skills=(SkillRef.model_validate("builtin:plan-review"),),
        input_contract="plan",
        result_contract="review-findings",
        containing_session=SessionKind.PLAN,
        host_mode=DelegationHostMode.MAPPED,
        host_slot=SkillSlot.REVIEW,
    ),
    DelegationKind.CODE_REVIEW: DelegationDefinition(
        kind=DelegationKind.CODE_REVIEW,
        ai_command=AICommandKey.REVIEW_IMPLEMENTATION,
        contract_template="review-code.md",
        default_skills=(SkillRef.model_validate("builtin:code-review"),),
        input_contract="diff",
        result_contract="review-findings",
        host_mode=DelegationHostMode.MAPPED,
        host_slot=SkillSlot.REVIEW,
    ),
    DelegationKind.BATCH_REVIEW: DelegationDefinition(
        kind=DelegationKind.BATCH_REVIEW,
        ai_command=AICommandKey.REVIEW_BATCH,
        contract_template="review-batch.md",
        default_skills=(SkillRef.model_validate("builtin:batch-review"),),
        input_contract="batch-context",
        result_contract="review-findings",
        host_mode=DelegationHostMode.ALWAYS_FOREIGN,
    ),
    DelegationKind.DEPENDENCY_ANALYSIS: DelegationDefinition(
        kind=DelegationKind.DEPENDENCY_ANALYSIS,
        ai_command=AICommandKey.DEPS,
        contract_template="deps-analysis.md",
        default_skills=(SkillRef.model_validate("builtin:dependency-analysis"),),
        input_contract="issue-list",
        result_contract="dependency-edges",
        containing_session=SessionKind.DEPS,
        host_mode=DelegationHostMode.ALWAYS_FOREIGN,
    ),
}


SESSION_TO_COMPLETION_KIND: dict[SessionKind, CompletionPolicy] = {
    kind: definition.completion_policy for kind, definition in SESSION_DEFINITIONS.items()
}
