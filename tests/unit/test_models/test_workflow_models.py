"""Contracts for canonical session, delegation, and skill identity."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from wade.models.hooks import SessionPhase
from wade.models.readiness import READINESS_REQUIREMENTS, ReadinessPhase
from wade.models.skill import SkillRef, SkillSlot
from wade.models.workflow import (
    DELEGATION_DEFINITIONS,
    SESSION_DEFINITIONS,
    AICommandKey,
    DelegationHostMode,
    DelegationKind,
    SessionKind,
)


def test_readiness_phase_is_a_compatibility_alias() -> None:
    assert ReadinessPhase is SessionKind
    assert set(READINESS_REQUIREMENTS) == set(SessionKind)


def test_every_session_has_exactly_one_definition() -> None:
    assert set(SESSION_DEFINITIONS) == set(SessionKind)
    assert {definition.kind for definition in SESSION_DEFINITIONS.values()} == set(SessionKind)


def test_interactive_tuple_or_explicit_deps_opt_out() -> None:
    for definition in SESSION_DEFINITIONS.values():
        interactive = (
            definition.workflow_template,
            definition.workflow_revision,
            definition.launch_prompt,
            definition.session_phase,
        )
        if definition.kind is SessionKind.DEPS:
            assert interactive == (None, None, None, None)
            assert definition.steps == ()
            assert definition.default_skills == {}
        else:
            assert all(value is not None for value in interactive)
            assert definition.steps
            assert set(definition.default_skills) == {SkillSlot.WORK, SkillSlot.REVIEW}


def test_session_phase_mapping_is_complete_and_unique() -> None:
    phases = [
        definition.session_phase
        for definition in SESSION_DEFINITIONS.values()
        if definition.session_phase is not None
    ]
    assert set(phases) == set(SessionPhase)
    assert len(phases) == len(set(phases))


def test_readiness_and_ai_command_are_registry_derived() -> None:
    for kind, definition in SESSION_DEFINITIONS.items():
        requirements = READINESS_REQUIREMENTS[kind]
        assert requirements.ai_command == definition.ai_command.value
        assert requirements.requires_github is definition.readiness.requires_github
        assert (
            requirements.requires_git_metadata_write
            is definition.readiness.requires_git_metadata_write
        )


def test_every_delegation_has_an_explicit_host_relationship() -> None:
    assert set(DELEGATION_DEFINITIONS) == set(DelegationKind)
    for definition in DELEGATION_DEFINITIONS.values():
        if definition.host_mode is DelegationHostMode.MAPPED:
            assert definition.host_slot is SkillSlot.REVIEW
        else:
            assert definition.host_slot is None

    assert (
        DELEGATION_DEFINITIONS[DelegationKind.BATCH_REVIEW].host_mode
        is DelegationHostMode.ALWAYS_FOREIGN
    )
    assert (
        DELEGATION_DEFINITIONS[DelegationKind.DEPENDENCY_ANALYSIS].host_mode
        is DelegationHostMode.ALWAYS_FOREIGN
    )


def test_ai_command_keys_remain_distinct_from_workflow_identity() -> None:
    assert AICommandKey.REVIEW_IMPLEMENTATION.value == "review_implementation"
    assert SessionKind.IMPLEMENTATION.value == "implementation"
    assert DelegationKind.CODE_REVIEW.value == "code-review"


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("builtin:implementation", "builtin:implementation"),
        ("project:security-review", "project:security-review"),
        ("path:.agents/skills/security-review", "path:.agents/skills/security-review"),
    ],
)
def test_skill_reference_parsing(raw: str, canonical: str) -> None:
    assert SkillRef.model_validate(raw).canonical == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "implementation",
        "builtin:",
        "project:bad/name",
        "path:/tmp/skill",
        "path:../skill",
        "path:.agents/../outside",
        "path:.agents\\skills\\x",
    ],
)
def test_skill_reference_rejects_ambiguous_or_unsafe_values(raw: str) -> None:
    with pytest.raises(ValidationError):
        SkillRef.model_validate(raw)
