"""Resolve dynamic skill bindings and compose immutable session bundles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from wade.models.config import ProjectConfig
from wade.models.session_manifest import ResolvedBinding, SessionManifest
from wade.models.skill import SkillRef, SkillSlot
from wade.models.workflow import (
    DELEGATION_DEFINITIONS,
    SESSION_DEFINITIONS,
    DelegationKind,
    SessionKind,
)
from wade.skills.discovery import (
    SkillInventory,
    discover_project_skills,
    self_init_builtin_templates,
)
from wade.skills.materializer import (
    SkillMaterializationError,
    materialize_session_bundle,
    validate_session_bundle,
)
from wade.skills.resolver import SkillResolutionError
from wade.skills.validation import SkillValidationError
from wade.utils.safe_state import read_state_file, state_directory_present


class SessionCompositionError(RuntimeError):
    """A session cannot safely resolve, resume, or refresh its skill state."""


SESSION_CONFIG_KEYS: dict[SessionKind, str] = {
    SessionKind.PLAN: "plan",
    SessionKind.IMPLEMENTATION: "implementation",
    SessionKind.REVIEW_PR_COMMENTS: "review_pr_comments",
}

DELEGATION_CONFIG_KEYS: dict[DelegationKind, str] = {
    DelegationKind.PLAN_REVIEW: "plan_review",
    DelegationKind.CODE_REVIEW: "code_review",
    DelegationKind.BATCH_REVIEW: "batch_review",
    DelegationKind.DEPENDENCY_ANALYSIS: "dependency_analysis",
}

SESSION_REVIEW_DELEGATION: dict[SessionKind, DelegationKind] = {
    SessionKind.PLAN: DelegationKind.PLAN_REVIEW,
    SessionKind.IMPLEMENTATION: DelegationKind.CODE_REVIEW,
    SessionKind.REVIEW_PR_COMMENTS: DelegationKind.CODE_REVIEW,
}


@dataclass(frozen=True)
class ResolvedSessionRefs:
    """Ordered winning refs and auditable precedence sources per slot."""

    refs: dict[SkillSlot, tuple[SkillRef, ...]]
    sources: dict[SkillSlot, str]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionCompositionResult:
    """A newly materialized or frozen-resumed session."""

    manifest: SessionManifest
    reused: bool
    resolution: ResolvedSessionRefs | None


def parse_skill_overrides(
    values: list[str] | tuple[str, ...] | None,
) -> tuple[SkillRef, ...] | None:
    """Parse one repeatable CLI slot override, preserving user order."""

    if values is None:
        return None
    if not values:
        raise SessionCompositionError("A supplied skill override cannot be empty")
    try:
        return tuple(SkillRef.model_validate(value) for value in values)
    except ValidationError as exc:
        raise SessionCompositionError(f"Invalid skill override: {exc.errors()[0]['msg']}") from exc


def _delegation_config_refs(
    config: ProjectConfig, kind: DelegationKind
) -> tuple[SkillRef, ...] | None:
    key = DELEGATION_CONFIG_KEYS[kind]
    return cast(tuple[SkillRef, ...] | None, getattr(config.delegations, key).skills.work)


def resolve_session_refs(
    config: ProjectConfig,
    kind: SessionKind,
    *,
    work_override: tuple[SkillRef, ...] | None = None,
    review_override: tuple[SkillRef, ...] | None = None,
) -> ResolvedSessionRefs:
    """Apply CLI > session config > mapped delegation config > built-in defaults."""

    if kind not in SESSION_CONFIG_KEYS:
        raise SessionCompositionError(f"{kind.value} has no interactive skill slots")
    definition = SESSION_DEFINITIONS[kind]
    session_config = getattr(config.sessions, SESSION_CONFIG_KEYS[kind]).skills
    review_delegation = SESSION_REVIEW_DELEGATION[kind]
    delegation_review = _delegation_config_refs(config, review_delegation)
    warnings: list[str] = []

    refs: dict[SkillSlot, tuple[SkillRef, ...]] = {}
    sources: dict[SkillSlot, str] = {}
    if work_override is not None:
        refs[SkillSlot.WORK] = work_override
        sources[SkillSlot.WORK] = "cli"
    elif session_config.work is not None:
        refs[SkillSlot.WORK] = session_config.work
        sources[SkillSlot.WORK] = f"sessions.{SESSION_CONFIG_KEYS[kind]}.skills.work"
    else:
        refs[SkillSlot.WORK] = definition.default_skills[SkillSlot.WORK]
        sources[SkillSlot.WORK] = "builtin-default"

    if review_override is not None:
        refs[SkillSlot.REVIEW] = review_override
        sources[SkillSlot.REVIEW] = "cli"
    elif session_config.review is not None:
        refs[SkillSlot.REVIEW] = session_config.review
        sources[SkillSlot.REVIEW] = f"sessions.{SESSION_CONFIG_KEYS[kind]}.skills.review"
        if delegation_review is not None and delegation_review != session_config.review:
            warnings.append(
                f"sessions.{SESSION_CONFIG_KEYS[kind]}.skills.review shadows differing "
                f"delegations.{DELEGATION_CONFIG_KEYS[review_delegation]}.skills.work"
            )
    elif delegation_review is not None:
        refs[SkillSlot.REVIEW] = delegation_review
        sources[SkillSlot.REVIEW] = (
            f"delegations.{DELEGATION_CONFIG_KEYS[review_delegation]}.skills.work"
        )
    else:
        refs[SkillSlot.REVIEW] = definition.default_skills[SkillSlot.REVIEW]
        sources[SkillSlot.REVIEW] = "builtin-default"

    return ResolvedSessionRefs(refs=refs, sources=sources, warnings=tuple(warnings))


def resolve_delegation_refs(
    config: ProjectConfig,
    kind: DelegationKind,
    *,
    override: tuple[SkillRef, ...] | None = None,
) -> tuple[tuple[SkillRef, ...], str]:
    """Resolve a standalone/foreign delegation binding."""

    if override is not None:
        return override, "cli"
    configured = _delegation_config_refs(config, kind)
    if configured is not None:
        return configured, f"delegations.{DELEGATION_CONFIG_KEYS[kind]}.skills.work"
    return DELEGATION_DEFINITIONS[kind].default_skills, "builtin-default"


def discover_inventory(
    config: ProjectConfig,
    worktree_root: Path,
    main_root: Path,
) -> SkillInventory:
    """Discover the configured project inventory or return an explicit empty set."""

    policy = config.skills.project
    if not policy.discover:
        return SkillInventory(
            skills=(),
            builtin_templates=self_init_builtin_templates(worktree_root, main_root),
        )
    return discover_project_skills(
        worktree_root,
        main_root,
        include=policy.include,
        exclude=policy.exclude,
    )


def load_session_manifest(worktree_root: Path) -> SessionManifest | None:
    """Load a current manifest; malformed or symlinked state is never trusted."""
    raw = read_state_file(worktree_root, ("session",), "manifest.json")
    if raw is None:
        return None
    try:
        return SessionManifest.model_validate_json(raw)
    except (ValueError, ValidationError):
        return None


def session_state_present(worktree_root: Path) -> bool:
    """Whether canonical session state exists, even if it is unsafe or invalid."""

    return state_directory_present(worktree_root, ("session",))


def _validate_reusable_session_bundle(
    worktree_root: Path,
    manifest: SessionManifest,
) -> None:
    """Fail closed when frozen workflow metadata or physical content changed."""

    definition = SESSION_DEFINITIONS[manifest.session]
    if (
        manifest.workflow_revision != definition.workflow_revision
        or manifest.ai_command != definition.ai_command
    ):
        raise SessionCompositionError(
            "Active session workflow metadata is stale or invalid; run an explicit skill refresh"
        )
    try:
        validate_session_bundle(worktree_root, manifest)
    except (SkillMaterializationError, SkillValidationError) as exc:
        raise SessionCompositionError(
            f"Active session bundle failed integrity validation: {exc}; "
            "run an explicit skill refresh"
        ) from exc


def compose_session(
    worktree_root: Path,
    main_root: Path,
    config: ProjectConfig,
    *,
    kind: SessionKind,
    task_id: str | None,
    work_skills: list[str] | tuple[str, ...] | None = None,
    review_skills: list[str] | tuple[str, ...] | None = None,
    refresh: bool = False,
    display_root: str = ".wade/session",
) -> SessionCompositionResult:
    """Resume frozen state or explicitly resolve and replace the session bundle."""

    work_override = parse_skill_overrides(work_skills)
    review_override = parse_skill_overrides(review_skills)
    existing = load_session_manifest(worktree_root)
    if existing is None and session_state_present(worktree_root) and not refresh:
        raise SessionCompositionError(
            "Active session manifest is unreadable or invalid; run an explicit skill refresh"
        )
    if existing is not None and existing.session is kind and not refresh:
        _validate_reusable_session_bundle(worktree_root, existing)
        if work_override is not None or review_override is not None:
            raise SessionCompositionError(
                "Active session bindings are frozen; pass --refresh-skills to apply overrides"
            )
        return SessionCompositionResult(manifest=existing, reused=True, resolution=None)

    resolution = resolve_session_refs(
        config,
        kind,
        work_override=work_override,
        review_override=review_override,
    )
    try:
        inventory = discover_inventory(config, worktree_root, main_root)
    except SkillValidationError as exc:
        raise SessionCompositionError(f"Project skill discovery failed: {exc}") from exc
    review_enabled = (
        config.ai.review_plan.enabled is not False
        if kind is SessionKind.PLAN
        else config.ai.review_implementation.enabled is not False
    )

    from wade.skills.doc_targets import detect_doc_targets, format_doc_targets

    try:
        manifest = materialize_session_bundle(
            worktree_root,
            kind=kind,
            task_id=task_id,
            refs=resolution.refs,
            inventory=inventory,
            review_enabled=review_enabled,
            doc_targets=format_doc_targets(detect_doc_targets(worktree_root)),
            display_root=display_root,
        )
    except (SkillMaterializationError, SkillResolutionError, SkillValidationError) as exc:
        raise SessionCompositionError(f"Session skill resolution failed: {exc}") from exc
    return SessionCompositionResult(manifest=manifest, reused=False, resolution=resolution)


def mapped_session_review_binding(
    worktree_root: Path,
    kind: DelegationKind,
) -> tuple[ResolvedBinding, SessionKind] | None:
    """Return a host session's frozen REVIEW binding when mapping is applicable."""

    definition = DELEGATION_DEFINITIONS[kind]
    if definition.host_slot is not SkillSlot.REVIEW:
        return None
    manifest = load_session_manifest(worktree_root)
    if manifest is None:
        if session_state_present(worktree_root):
            raise SessionCompositionError(
                "Active session manifest is unreadable or invalid; refresh the session "
                "before running its mapped review"
            )
        return None
    if kind is DelegationKind.PLAN_REVIEW and manifest.session is not SessionKind.PLAN:
        return None
    if kind is DelegationKind.CODE_REVIEW and manifest.session not in {
        SessionKind.IMPLEMENTATION,
        SessionKind.REVIEW_PR_COMMENTS,
    }:
        return None
    _validate_reusable_session_bundle(worktree_root, manifest)
    return manifest.bindings[SkillSlot.REVIEW], manifest.session
