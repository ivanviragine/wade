"""Explicit native-terminal handoff, revision, review, and completion contracts."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from wade.cli.main import app
from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
from wade.models.delegation import DelegationMode, DelegationResult
from wade.models.plan_bundle import BUNDLE_MARKER, PlanBundle, PlanMember
from wade.models.workflow import SessionKind
from wade.services import interactive_plan_service as interactive
from wade.services.session_composition_service import compose_session

PLAN = "# fix: preserve native planning\n\n## Complexity\neasy\n\n## Tasks\n- Test it.\n"


@pytest.fixture
def session(tmp_path: Path) -> Path:
    compose_session(tmp_path, tmp_path, ProjectConfig(), kind=SessionKind.PLAN, task_id=None)
    interactive.begin(tmp_path, "claude", review_required=True)
    return tmp_path


def test_explicit_native_artifact_imports_then_requires_real_review(session: Path) -> None:
    native_file = session / "native-chosen-name.md"
    native_file.write_text(PLAN)
    result = CliRunner().invoke(
        app, ["plan-session", "done", str(session / ".wade/plans"), "--from-file", str(native_file)]
    )
    assert result.exit_code == 1
    assert "Review is required" in result.output
    copied = session / ".wade/plans/PLAN.md"
    assert copied.read_text() == PLAN
    assert native_file.read_text() == PLAN
    interactive.record_review(copied, PLAN, self_review=False)
    result = CliRunner().invoke(app, ["plan-session", "done", str(copied.parent)])
    assert result.exit_code == 0, result.output
    assert interactive.collect(session).plans[0].markdown == PLAN


def test_prompt_emission_requires_acknowledgement_for_exact_content(session: Path) -> None:
    interactive.import_artifact(session, PLAN)
    copied = session / ".wade/plans/PLAN.md"
    with pytest.raises(ValueError, match="Run wade review plan first"):
        interactive.acknowledge_self_review(copied)
    interactive.record_review(copied, PLAN, self_review=True)
    with pytest.raises(ValueError, match="Perform the emitted self-review"):
        interactive.complete(session)
    interactive.acknowledge_self_review(copied)
    interactive.complete(session)
    assert interactive.collect(session).plans[0].markdown == PLAN


def test_revisions_invalidate_review_and_post_done_edits_invalidate_handoff(session: Path) -> None:
    interactive.import_artifact(session, PLAN)
    copied = session / ".wade/plans/PLAN.md"
    interactive.record_review(copied, PLAN, self_review=False)
    interactive.import_artifact(session, PLAN + "\nMore detail.\n")
    with pytest.raises(ValueError, match="Review is required"):
        interactive.complete(session)
    interactive.record_review(copied, copied.read_text(), self_review=False)
    interactive.complete(session)
    copied.write_text(PLAN)
    with pytest.raises(ValueError, match="changed after completion"):
        interactive.collect(session)


def test_changed_plan_cannot_acknowledge_an_old_self_review(session: Path) -> None:
    interactive.import_artifact(session, PLAN)
    copied = session / ".wade/plans/PLAN.md"
    interactive.record_review(copied, PLAN, self_review=True)
    copied.write_text(PLAN + "\nUpdated.\n")
    with pytest.raises(ValueError, match="changed"):
        interactive.acknowledge_self_review(copied)


def test_stdin_bundle_preserves_dependencies_and_explicit_knowledge_handoff(session: Path) -> None:
    bundle = PlanBundle(
        plans=(
            PlanMember(filename="PLAN-base.md", markdown=PLAN),
            PlanMember(filename="PLAN-ui.md", markdown=PLAN, depends_on=("PLAN-base.md",)),
        ),
        knowledge_votes=(),
    )
    artifact = BUNDLE_MARKER + "\n```json\n" + bundle.model_dump_json() + "\n```"
    result = CliRunner().invoke(
        app, ["plan-session", "done", str(session / ".wade/plans"), "--from-stdin"], input=artifact
    )
    assert result.exit_code == 1
    for member in bundle.plans:
        interactive.record_review(
            session / ".wade/plans" / member.filename, member.markdown, self_review=False
        )
    interactive.complete(session)
    assert interactive.collect(session) == bundle


def test_missing_completion_never_treats_draft_as_accepted(session: Path) -> None:
    interactive.import_artifact(session, PLAN)
    with pytest.raises(ValueError, match="No completed plan handoff"):
        interactive.collect(session)


def test_import_refuses_unowned_files_and_symlinked_members(session: Path) -> None:
    copied = session / ".wade/plans/PLAN.md"
    copied.write_text("mine")
    with pytest.raises(ValueError, match="conflicts"):
        interactive.import_artifact(session, PLAN)
    assert copied.read_text() == "mine"
    copied.unlink()
    interactive.import_artifact(session, PLAN)
    copied.unlink()
    outside = session / "unrelated.md"
    outside.write_text("mine")
    copied.symlink_to(outside)
    with pytest.raises(ValueError, match="unsafe"):
        interactive.import_artifact(session, PLAN + "\nrevision")
    assert outside.read_text() == "mine"


def test_explicit_review_opt_out_and_direct_plan_files(tmp_path: Path) -> None:
    config = ProjectConfig(ai=AIConfig(review_plan=AICommandConfig(enabled=False)))
    compose_session(tmp_path, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
    interactive.begin(tmp_path, "cursor", review_required=False)
    (tmp_path / ".wade/plans/PLAN.md").write_text(PLAN)
    interactive.complete(tmp_path)
    assert interactive.collect(tmp_path).plans[0].markdown == PLAN


def test_modified_frozen_bundle_cannot_complete_or_import(session: Path) -> None:
    (session / ".wade/session/WORKFLOW.md").write_text("altered")
    with pytest.raises(ValueError, match="integrity"):
        interactive.import_artifact(session, PLAN)


def test_native_source_must_be_a_bounded_regular_file(tmp_path: Path) -> None:
    source = tmp_path / "native.md"
    source.write_text("a" * 2_000_001)
    with pytest.raises(ValueError, match="2 MB"):
        interactive.read_artifact(source)
    link = tmp_path / "link.md"
    link.symlink_to(source)
    with pytest.raises(OSError):
        interactive.read_artifact(link)


@pytest.mark.parametrize("mode", [DelegationMode.PROMPT, DelegationMode.HEADLESS])
def test_review_command_records_result_and_self_review_ack(
    session: Path, mode: DelegationMode, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wade.services.review_delegation_service import review_plan

    monkeypatch.chdir(session)
    interactive.import_artifact(session, PLAN)
    copied = session / ".wade/plans/PLAN.md"
    with (
        patch("wade.services.review_delegation_service.load_config", return_value=ProjectConfig()),
        patch(
            "wade.services.review_delegation_service._run_review_delegation",
            return_value=DelegationResult(success=True, feedback="Reviewed", mode=mode),
        ),
    ):
        assert review_plan(str(copied)).success
        if mode is DelegationMode.PROMPT:
            result = CliRunner().invoke(app, ["review", "plan", str(copied), "--ack-self-review"])
            assert result.exit_code == 0, result.output
    interactive.complete(session)
    assert interactive.collect(session).plans[0].markdown == PLAN


def test_failed_repeat_review_cannot_reuse_previous_success(session: Path) -> None:
    interactive.import_artifact(session, PLAN)
    copied = session / ".wade/plans/PLAN.md"
    interactive.record_review(copied, PLAN, self_review=False)
    interactive.complete(session)
    interactive.invalidate_review(copied)
    with pytest.raises(ValueError, match="Review is required"):
        interactive.complete(session)


def test_knowledge_handoff_is_required_before_native_exit(tmp_path: Path) -> None:
    compose_session(tmp_path, tmp_path, ProjectConfig(), kind=SessionKind.PLAN, task_id=None)
    interactive.begin(tmp_path, "cursor", review_required=False, knowledge_required=True)
    interactive.import_artifact(tmp_path, PLAN)
    with pytest.raises(ValueError, match="knowledge_votes"):
        interactive.complete(tmp_path)
    bundle = PlanBundle(plans=(PlanMember(filename="PLAN.md", markdown=PLAN),), knowledge_votes=())
    interactive.import_artifact(
        tmp_path, BUNDLE_MARKER + "\n```json\n" + bundle.model_dump_json() + "\n```"
    )
    interactive.complete(tmp_path)
    assert interactive.collect(tmp_path).knowledge_votes == ()


def test_managed_review_rejects_symlink_before_delegation(session: Path) -> None:
    from wade.services.review_delegation_service import review_plan

    interactive.import_artifact(session, PLAN)
    plan = session / ".wade/plans/PLAN.md"
    source = session / "outside.md"
    source.write_text(PLAN)
    plan.unlink()
    plan.symlink_to(source)
    with patch("wade.services.review_delegation_service._run_review_delegation") as reviewer:
        result = review_plan(str(plan), project_root=session)
    assert not result.success
    reviewer.assert_not_called()
