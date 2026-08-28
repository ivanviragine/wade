"""Read-only diagnostics for dynamic skill discovery and binding precedence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wade.config.loader import load_config
from wade.git import repo as git_repo
from wade.models.config import ProjectConfig
from wade.models.session_manifest import compute_binding_digest
from wade.models.skill import BindingComponent, SkillRef, SkillSlot
from wade.models.workflow import (
    DELEGATION_DEFINITIONS,
    SESSION_DEFINITIONS,
    DelegationKind,
    SessionKind,
)
from wade.services.session_composition_service import (
    DELEGATION_CONFIG_KEYS,
    SESSION_CONFIG_KEYS,
    SESSION_REVIEW_DELEGATION,
    discover_inventory,
    load_session_manifest,
    resolve_delegation_refs,
    resolve_session_refs,
)
from wade.skills.catalog import builtin_skills
from wade.skills.discovery import SkillInventory
from wade.skills.resolver import SkillResolutionError, resolve_skill_refs


class SkillDiagnosticsError(RuntimeError):
    """Skill state or configuration cannot be diagnosed safely."""


@dataclass(frozen=True)
class BindingCandidate:
    rank: int
    source: str
    refs: tuple[str, ...]
    selected: bool = False


@dataclass(frozen=True)
class SlotResolution:
    slot: str
    digest: str | None
    candidates: tuple[BindingCandidate, ...]


@dataclass(frozen=True)
class ResolutionReport:
    identity: str
    slots: tuple[SlotResolution, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillCheckReport:
    builtins: int
    project_skills: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def project_context(cwd: Path | None = None) -> tuple[Path, Path, ProjectConfig, SkillInventory]:
    """Resolve checkout roots, configuration, and the filtered project inventory."""

    start = (cwd or Path.cwd()).resolve()
    try:
        root = git_repo.get_repo_root(start)
    except Exception:
        root = start
    config = load_config(root)
    try:
        main_root = git_repo.main_checkout_root(root)
    except Exception:
        main_root = root
    try:
        inventory = discover_inventory(config, root, main_root)
    except Exception as exc:
        raise SkillDiagnosticsError(f"Project skill discovery failed: {exc}") from exc
    return root, main_root, config, inventory


def _refs(values: tuple[SkillRef, ...] | None) -> tuple[str, ...]:
    return tuple(ref.canonical for ref in values or ())


def _resolved_digest(refs: tuple[SkillRef, ...], inventory: SkillInventory) -> str:
    selections = resolve_skill_refs(refs, inventory)
    components = tuple(
        BindingComponent(
            position=index,
            canonical_ref=selection.ref.canonical,
            content_digest=selection.skill.descriptor.content_digest,
        )
        for index, selection in enumerate(selections)
    )
    return compute_binding_digest(components)


def _session_candidates(
    config: ProjectConfig,
    kind: SessionKind,
    slot: SkillSlot,
    *,
    selected_source: str | None,
) -> tuple[BindingCandidate, ...]:
    """Build the shared precedence ladder below an optional active manifest."""

    session_key = SESSION_CONFIG_KEYS[kind]
    session_config = getattr(config.sessions, session_key).skills
    delegation = SESSION_REVIEW_DELEGATION[kind]
    delegation_key = DELEGATION_CONFIG_KEYS[delegation]
    candidates = [BindingCandidate(2, "cli-override (not supplied)", ())]
    configured = getattr(session_config, slot.value)
    if configured is not None:
        source = f"sessions.{session_key}.skills.{slot.value}"
        candidates.append(BindingCandidate(3, source, _refs(configured), selected_source == source))
    if slot is SkillSlot.REVIEW:
        delegated = getattr(config.delegations, delegation_key).skills.work
        if delegated is not None:
            source = f"delegations.{delegation_key}.skills.work"
            candidates.append(
                BindingCandidate(4, source, _refs(delegated), selected_source == source)
            )
    default = SESSION_DEFINITIONS[kind].default_skills[slot]
    candidates.append(
        BindingCandidate(
            5,
            "builtin-default",
            _refs(default),
            selected_source == "builtin-default",
        )
    )
    return tuple(candidates)


def resolve_session_report(
    kind: SessionKind,
    *,
    cwd: Path | None = None,
) -> ResolutionReport:
    """Report frozen or configured precedence for every session skill slot."""

    if kind not in SESSION_CONFIG_KEYS:
        raise SkillDiagnosticsError(f"{kind.value} has no interactive skill bindings")
    root, _main, config, inventory = project_context(cwd)
    manifest = load_session_manifest(root)
    if manifest is not None and manifest.session is kind:
        resolution = resolve_session_refs(config, kind)
        manifest_slots = tuple(
            SlotResolution(
                slot=slot.value,
                digest=manifest.bindings[slot].digest,
                candidates=(
                    BindingCandidate(
                        rank=1,
                        source="active-session-manifest",
                        refs=tuple(skill.canonical_ref for skill in manifest.bindings[slot].skills),
                        selected=True,
                    ),
                    *_session_candidates(config, kind, slot, selected_source=None),
                ),
            )
            for slot in (SkillSlot.WORK, SkillSlot.REVIEW)
        )
        return ResolutionReport(
            identity=f"session:{kind.value}",
            slots=manifest_slots,
            warnings=resolution.warnings,
        )

    resolution = resolve_session_refs(config, kind)
    configured_slots: list[SlotResolution] = []
    for slot in (SkillSlot.WORK, SkillSlot.REVIEW):
        winner = resolution.refs[slot]
        configured_slots.append(
            SlotResolution(
                slot=slot.value,
                digest=_resolved_digest(winner, inventory),
                candidates=_session_candidates(
                    config,
                    kind,
                    slot,
                    selected_source=resolution.sources[slot],
                ),
            )
        )
    return ResolutionReport(
        identity=f"session:{kind.value}",
        slots=tuple(configured_slots),
        warnings=resolution.warnings,
    )


def resolve_delegation_report(
    kind: DelegationKind,
    *,
    cwd: Path | None = None,
) -> ResolutionReport:
    """Report configured precedence for a standalone bounded delegation."""

    _root, _main, config, inventory = project_context(cwd)
    winner, source = resolve_delegation_refs(config, kind)
    configured = getattr(config.delegations, DELEGATION_CONFIG_KEYS[kind]).skills.work
    candidates = [BindingCandidate(2, "cli-override (not supplied)", ())]
    if configured is not None:
        configured_source = f"delegations.{DELEGATION_CONFIG_KEYS[kind]}.skills.work"
        candidates.append(
            BindingCandidate(3, configured_source, _refs(configured), source == configured_source)
        )
    default = DELEGATION_DEFINITIONS[kind].default_skills
    candidates.append(
        BindingCandidate(4, "builtin-default", _refs(default), source == "builtin-default")
    )
    return ResolutionReport(
        identity=f"delegation:{kind.value}",
        slots=(
            SlotResolution(
                slot=SkillSlot.WORK.value,
                digest=_resolved_digest(winner, inventory),
                candidates=tuple(candidates),
            ),
        ),
    )


def check_project_skills(cwd: Path | None = None) -> SkillCheckReport:
    """Validate built-ins, discovered trees, configured refs, and shadowing."""

    try:
        _root, _main, config, inventory = project_context(cwd)
        builtins = builtin_skills(inventory.builtin_templates)
    except Exception as exc:
        return SkillCheckReport(0, 0, (str(exc),), ())

    errors: list[str] = []
    warnings: list[str] = list(inventory.warnings)
    for kind, key in SESSION_CONFIG_KEYS.items():
        section = getattr(config.sessions, key).skills
        for slot in (SkillSlot.WORK, SkillSlot.REVIEW):
            refs = getattr(section, slot.value)
            if refs is not None:
                try:
                    resolve_skill_refs(refs, inventory)
                except SkillResolutionError as exc:
                    errors.append(f"sessions.{key}.skills.{slot.value}: {exc}")
        warnings.extend(resolve_session_refs(config, kind).warnings)
    for _kind, key in DELEGATION_CONFIG_KEYS.items():
        refs = getattr(config.delegations, key).skills.work
        if refs is not None:
            try:
                resolve_skill_refs(refs, inventory)
            except SkillResolutionError as exc:
                errors.append(f"delegations.{key}.skills.work: {exc}")
    return SkillCheckReport(
        builtins=len(builtins),
        project_skills=len(inventory.skills),
        errors=tuple(errors),
        warnings=tuple(dict.fromkeys(warnings)),
    )
