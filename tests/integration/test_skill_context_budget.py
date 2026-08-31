"""Ratified context ceilings for decoupled workflows and methodology skills.

The legacy combined phase-skill surfaces measured before extraction were:
implementation 14,495 chars, planning 14,486, and PR-comment review 14,544.
The new split surfaces use explicit independent ceilings so workflow growth,
replaceable methodology growth, and launch-prompt growth cannot hide each other.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wade.models.skill import SkillSlot
from wade.models.workflow import SESSION_DEFINITIONS, SessionKind
from wade.skills.discovery import SkillInventory
from wade.skills.materializer import (
    MAX_ACTIVE_WORK_SKILL_CHARS,
    MAX_ALWAYS_READ_CHARS,
    MAX_INTERACTIVE_LAUNCH_CHARS,
    MAX_WORKFLOW_CHARS,
    materialize_session_bundle,
)
from wade.utils.templates import get_templates_dir

_INTERACTIVE = (
    SessionKind.PLAN,
    SessionKind.IMPLEMENTATION,
    SessionKind.REVIEW_PR_COMMENTS,
)
_DOC_TARGETS_WORST_CASE = (
    "- `README.md`\n- `AGENTS.md`\n- `CLAUDE.md`\n- `CONTRIBUTING.md`\n- `docs/`"
)


@pytest.mark.parametrize("kind", _INTERACTIVE, ids=lambda kind: kind.value)
def test_default_session_surfaces_stay_within_independent_budgets(
    kind: SessionKind,
    tmp_path: Path,
) -> None:
    definition = SESSION_DEFINITIONS[kind]
    manifest = materialize_session_bundle(
        tmp_path,
        kind=kind,
        task_id="42",
        refs=definition.default_skills,
        inventory=SkillInventory(skills=()),
        review_enabled=True,
        doc_targets=_DOC_TARGETS_WORST_CASE,
    )
    bundle = tmp_path / ".wade" / "session"
    workflow = (bundle / "WORKFLOW.md").read_text(encoding="utf-8")
    launch = (get_templates_dir() / "prompts" / str(definition.launch_prompt)).read_text(
        encoding="utf-8"
    )
    work_skill_chars = sum(
        len((tmp_path / skill.materialized_path / "SKILL.md").read_text(encoding="utf-8"))
        for skill in manifest.bindings[SkillSlot.WORK].skills
    )

    assert len(launch) <= MAX_INTERACTIVE_LAUNCH_CHARS
    assert len(workflow) <= MAX_WORKFLOW_CHARS
    assert work_skill_chars <= MAX_ACTIVE_WORK_SKILL_CHARS
    assert len(launch) + len(workflow) + work_skill_chars <= MAX_ALWAYS_READ_CHARS


@pytest.mark.parametrize(
    "filename",
    ("session-start-plan.md", "session-start-implement.md", "session-start-review.md"),
)
def test_session_start_reminders_remain_compact(filename: str) -> None:
    content = (get_templates_dir() / "prompts" / filename).read_text(encoding="utf-8")
    assert len(content) <= 800
