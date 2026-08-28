"""CLI contracts for skill diagnostics and active-session inspection."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from wade.cli.main import app
from wade.models.session_manifest import ResolvedBinding, SessionManifest
from wade.models.skill import ResolvedSkill, SkillSlot
from wade.models.workflow import AICommandKey, SessionKind

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
