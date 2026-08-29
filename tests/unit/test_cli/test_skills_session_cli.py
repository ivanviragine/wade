"""CLI contracts for skill diagnostics and active-session inspection."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from wade.cli.main import app
from wade.config.loader import load_config
from wade.models.session_manifest import ResolvedBinding, SessionManifest
from wade.models.skill import ResolvedSkill, SkillSlot
from wade.models.workflow import AICommandKey, SessionKind
from wade.services.session_composition_service import compose_session

runner = CliRunner()


def _config(root: Path) -> None:
    (root / ".wade.yml").write_text("version: 2\n", encoding="utf-8")


def _binding(name: str) -> ResolvedBinding:
    return ResolvedBinding.from_skills(
        (
            ResolvedSkill(
                canonical_ref=f"builtin:{name}",
                source_path=f"templates/skills/{name}",
                materialized_path=f".wade/session/skills/builtin/{name}",
                content_digest=f"sha256:{'1' * 64}",
                files=("SKILL.md",),
            ),
        )
    )


def test_skills_resolve_requires_exactly_one_identity(tmp_path: Path, monkeypatch) -> None:
    _config(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["skills", "resolve"])
    assert result.exit_code == 2
    assert "exactly one" in result.output


def test_skills_resolve_prints_candidates_and_winner(tmp_path: Path, monkeypatch) -> None:
    _config(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["skills", "resolve", "--session", "implementation"])
    assert result.exit_code == 0
    assert "session:implementation" in result.output
    assert "WINNER" in result.output
    assert "builtin:implementation" in result.output


def test_skills_check_reports_valid_inventory(tmp_path: Path, monkeypatch) -> None:
    _config(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["skills", "check"])
    assert result.exit_code == 0
    assert "VALID_SKILLS" in result.output
    assert "builtins=7" in result.output


def test_session_describe_prints_frozen_bindings(tmp_path: Path) -> None:
    manifest = SessionManifest(
        session=SessionKind.IMPLEMENTATION,
        workflow_revision=1,
        bundle_digest=f"sha256:{'0' * 64}",
        task_id="42",
        ai_command=AICommandKey.IMPLEMENT,
        bindings={
            SkillSlot.WORK: _binding("implementation"),
            SkillSlot.REVIEW: _binding("code-review"),
        },
    )
    session = tmp_path / ".wade" / "session"
    session.mkdir(parents=True)
    (session / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json")), encoding="utf-8"
    )
    with patch("wade.cli.session._root", return_value=tmp_path):
        result = runner.invoke(app, ["session", "describe"])
    assert result.exit_code == 0
    assert "session=implementation" in result.output
    assert "slot=review" in result.output
    assert "builtin:code-review" in result.output


def test_session_describe_uses_plan_dir_fallback_outside_git(tmp_path: Path, monkeypatch) -> None:
    from wade.models.readiness import PLAN_DIR_ENV_VAR

    project = tmp_path / "project"
    project.mkdir()
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    compose_session(
        plan_dir,
        project,
        load_config(project),
        kind=SessionKind.PLAN,
        task_id=None,
        display_root=str(plan_dir / ".wade/session"),
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv(PLAN_DIR_ENV_VAR, str(plan_dir))

    result = runner.invoke(app, ["session", "describe"])

    assert result.exit_code == 0
    assert "session=plan" in result.output


def test_session_refresh_preserves_fallback_project_skills_and_absolute_paths(
    tmp_path: Path, monkeypatch
) -> None:
    from wade.models.readiness import PLAN_DIR_ENV_VAR

    project = tmp_path / "project"
    skill = project / ".agents" / "skills" / "custom-plan"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: custom-plan\ndescription: Custom planning method\n---\n\nPlan carefully.\n",
        encoding="utf-8",
    )
    (project / ".wade.yml").write_text(
        "version: 2\nsessions:\n  plan:\n    skills:\n      work: [project:custom-plan]\n",
        encoding="utf-8",
    )
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    display_root = str(plan_dir / ".wade/session")
    compose_session(
        plan_dir,
        project,
        load_config(project),
        kind=SessionKind.PLAN,
        task_id=None,
        display_root=display_root,
    )
    monkeypatch.chdir(project)
    monkeypatch.setenv(PLAN_DIR_ENV_VAR, str(plan_dir))

    result = runner.invoke(app, ["session", "refresh-skills"])

    assert result.exit_code == 0
    workflow = (plan_dir / ".wade/session/WORKFLOW.md").read_text(encoding="utf-8")
    assert f"{display_root}/skills/project/agents-skills/custom-plan/SKILL.md" in workflow
    assert (plan_dir / ".wade/session/skills/project/agents-skills/custom-plan/SKILL.md").is_file()
