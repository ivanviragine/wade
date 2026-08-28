"""Diagnostics for project skill inventory and binding precedence."""

from __future__ import annotations

import json
from pathlib import Path

from wade.models.session_manifest import ResolvedBinding, SessionManifest
from wade.models.skill import ResolvedSkill, SkillSlot
from wade.models.workflow import AICommandKey, DelegationKind, SessionKind
from wade.services.skill_diagnostics_service import (
    check_project_skills,
    resolve_delegation_report,
    resolve_session_report,
)


def _config(root: Path, body: str) -> None:
    (root / ".wade.yml").write_text("version: 2\n" + body, encoding="utf-8")


def _skill(root: Path, name: str) -> None:
    directory = root / ".agents" / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test {name}\n---\n\nMethod.\n",
        encoding="utf-8",
    )


def test_session_report_names_winner_shadowed_candidate_and_digest(tmp_path: Path) -> None:
    _skill(tmp_path, "security")
    _config(
        tmp_path,
        """sessions:
  implementation:
    skills:
      review: [project:security]
delegations:
  code_review:
    skills:
      work: [builtin:code-review]
""",
    )
    report = resolve_session_report(SessionKind.IMPLEMENTATION, cwd=tmp_path)
    review = next(slot for slot in report.slots if slot.slot == "review")
    winner = next(candidate for candidate in review.candidates if candidate.selected)
    assert winner.source == "sessions.implementation.skills.review"
    assert winner.refs == ("project:security",)
    assert review.digest is not None and review.digest.startswith("sha256:")
    assert any("shadows differing" in warning for warning in report.warnings)


def test_delegation_report_uses_config_then_builtin_candidate(tmp_path: Path) -> None:
    _config(
        tmp_path,
        """delegations:
  dependency_analysis:
    skills:
      work: [builtin:dependency-analysis]
""",
    )
    report = resolve_delegation_report(DelegationKind.DEPENDENCY_ANALYSIS, cwd=tmp_path)
    selected = [candidate for candidate in report.slots[0].candidates if candidate.selected]
    assert len(selected) == 1
    assert selected[0].source == "delegations.dependency_analysis.skills.work"


def test_skill_check_validates_project_refs_and_reports_counts(tmp_path: Path) -> None:
    _skill(tmp_path, "security")
    _config(
        tmp_path,
        """sessions:
  implementation:
    skills:
      review: [project:security]
""",
    )
    report = check_project_skills(tmp_path)
    assert report.valid
    assert report.builtins == 7
    assert report.project_skills == 1


def test_skill_check_reports_unknown_ref_without_materializing(tmp_path: Path) -> None:
    _config(
        tmp_path,
        """delegations:
  batch_review:
    skills:
      work: [project:missing]
""",
    )
    report = check_project_skills(tmp_path)
    assert not report.valid
    assert any("project:missing" in error for error in report.errors)
    assert not (tmp_path / ".wade").exists()


def test_active_session_report_keeps_manifest_winner_and_shadowed_ladder(
    tmp_path: Path,
) -> None:
    _config(
        tmp_path,
        """sessions:
  implementation:
    skills:
      work: [builtin:planning]
""",
    )
    work = ResolvedBinding.from_skills(
        (
            ResolvedSkill(
                canonical_ref="builtin:implementation",
                source_path="templates/skills/implementation",
                materialized_path=".wade/session/skills/builtin/implementation",
                content_digest=f"sha256:{'1' * 64}",
                files=("SKILL.md",),
            ),
        )
    )
    review = ResolvedBinding.from_skills(
        (
            ResolvedSkill(
                canonical_ref="builtin:code-review",
                source_path="templates/skills/code-review",
                materialized_path=".wade/session/skills/builtin/code-review",
                content_digest=f"sha256:{'2' * 64}",
                files=("SKILL.md",),
            ),
        )
    )
    session_dir = tmp_path / ".wade/session"
    session_dir.mkdir(parents=True)
    manifest = SessionManifest(
        session=SessionKind.IMPLEMENTATION,
        workflow_revision=1,
        ai_command=AICommandKey.IMPLEMENT,
        bindings={SkillSlot.WORK: work, SkillSlot.REVIEW: review},
    )
    (session_dir / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json")), encoding="utf-8"
    )

    report = resolve_session_report(SessionKind.IMPLEMENTATION, cwd=tmp_path)
    work_report = next(slot for slot in report.slots if slot.slot == "work")

    assert [candidate.rank for candidate in work_report.candidates] == [1, 2, 3, 5]
    assert [candidate.source for candidate in work_report.candidates if candidate.selected] == [
        "active-session-manifest"
    ]
    assert any(candidate.refs == ("builtin:planning",) for candidate in work_report.candidates)
