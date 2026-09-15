"""Crossby public-model consumption; these tests do not claim native CLI success."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from crossby.ai_tools import preflight_plan_session
from crossby.models.ai import (
    AIToolID,
    PlanArtifactSource,
    PlanInteraction,
    PlanInteractionKind,
    PlanInteractionOutcome,
    PlanQuestionOption,
    PlanSessionBinding,
    PlanSessionRequest,
    PlanSessionResult,
)
from crossby.utils.versioning import BinaryVersion
from pydantic import ValidationError

from wade.models.delegation import DelegationMode, DelegationResult
from wade.models.plan_bundle import BUNDLE_MARKER, PlanBundle, PlanMember
from wade.services import native_plan_service as native
from wade.utils.plan_validation import load_plan_file

MARKDOWN = "# feat: collected plan\n\n## Complexity\neasy\n"


def result_for(source: PlanArtifactSource, root: Path) -> PlanSessionResult:
    binding = {
        PlanArtifactSource.REQUESTED_PATH: PlanSessionBinding.ISOLATED_RUN_PATH,
        PlanArtifactSource.STRUCTURED_OUTPUT: PlanSessionBinding.CONVERSATION_ID,
        PlanArtifactSource.SESSION_EXPORT: PlanSessionBinding.SESSION_ID,
        PlanArtifactSource.PROTOCOL_EVENT: PlanSessionBinding.THREAD_TURN_IDS,
    }[source]
    return PlanSessionResult(
        tool=AIToolID.CODEX,
        version="test version 99.0.0",
        plan=MARKDOWN,
        session_id="exact-session",
        native_mode="native plan selector",
        artifact_source=source,
        binding=binding,
        artifact_path=root / ".wade/plans/.native/source.md",
        thread_id="exact-thread",
        turn_id="exact-turn",
        artifact_id="exact-artifact",
        exit_code=0,
    )


@pytest.mark.parametrize("source", list(PlanArtifactSource))
def test_every_native_source_uses_only_returned_markdown(
    tmp_path: Path,
    source: PlanArtifactSource,
) -> None:
    result = result_for(source, tmp_path)
    assert result.artifact_path is not None
    result.artifact_path.parent.mkdir(parents=True)
    result.artifact_path.write_text("unrelated native source; not another WADE task")
    native.save_artifact(tmp_path, result)
    native.materialize(tmp_path, native.parse_artifact(result.plan))
    directory = tmp_path / ".wade/plans"
    assert list(directory.glob("PLAN*.md")) == [directory / "PLAN.md"]
    assert (directory / "PLAN.md").read_text() == MARKDOWN
    assert json.loads((directory / "native-session.json").read_text()) == result.model_dump(
        mode="json"
    )
    assert (directory / "PLAN.md").stat().st_mode & 0o777 == 0o600


def test_materialization_never_overwrites_or_duplicates(tmp_path: Path) -> None:
    result = result_for(PlanArtifactSource.PROTOCOL_EVENT, tmp_path)
    native.save_artifact(tmp_path, result)
    bundle = native.parse_artifact(result.plan)
    native.materialize(tmp_path, bundle)
    with pytest.raises(ValueError, match="conflicts"):
        native.materialize(tmp_path, bundle)
    assert (tmp_path / ".wade/plans/PLAN.md").read_text() == MARKDOWN
    with pytest.raises(ValueError, match="provenance"):
        native.save_artifact(tmp_path, result)


@pytest.mark.parametrize("location", [".wade", ".wade/plans"])
def test_symlinked_output_directories_are_not_followed(tmp_path: Path, location: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = tmp_path / location
    target.parent.mkdir(exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        native.save_artifact(tmp_path, result_for(PlanArtifactSource.PROTOCOL_EVENT, tmp_path))
    assert list(outside.iterdir()) == []


def test_symlinked_member_is_not_read_after_review(tmp_path: Path) -> None:
    directory = tmp_path / ".wade/plans"
    directory.mkdir(parents=True)
    secret = tmp_path / "secret.md"
    secret.write_text(MARKDOWN)
    (directory / "PLAN.md").symlink_to(secret)
    with pytest.raises(ValueError, match="unsafe"):
        load_plan_file(directory / "PLAN.md")


def test_plan_title_ignores_h1_like_text_in_a_leading_fence(tmp_path: Path) -> None:
    plan_path = tmp_path / "PLAN.md"
    plan_path.write_text(
        "```sh\n# install deps\n```\n\n# feat: collected plan\n\n## Complexity\neasy\n"
    )

    plan = load_plan_file(plan_path)

    assert plan.title == "feat: collected plan"


@pytest.mark.parametrize("filename", ["../PLAN.md", "/PLAN.md", "C:\\PLAN.md", "README.md"])
def test_unsafe_names_rejected(filename: str) -> None:
    with pytest.raises(ValidationError):
        PlanMember(filename=filename, markdown=MARKDOWN)


@pytest.mark.parametrize("other", ["PLAN-one.md", "PLAN-ONE.md"])
def test_duplicate_member_names_rejected(other: str) -> None:
    with pytest.raises(ValidationError, match="unique"):
        PlanBundle(
            plans=(
                PlanMember(filename="PLAN-one.md", markdown=MARKDOWN),
                PlanMember(filename=other, markdown=MARKDOWN),
            )
        )


@pytest.mark.parametrize("dependency", ["PLAN-missing.md", "PLAN-one.md"])
def test_missing_or_cyclic_dependency_rejected(dependency: str) -> None:
    with pytest.raises(ValidationError):
        PlanBundle(
            plans=(
                PlanMember(
                    filename="PLAN-one.md",
                    markdown=MARKDOWN,
                    depends_on=(dependency,),
                ),
            )
        )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "  ",
        BUNDLE_MARKER + "\nnot JSON",
        "<!-- wade:plan-bundle:v2 -->\n{}",
        BUNDLE_MARKER + '\n```json\n{"plans": [], "plans": []}\n```',
    ],
)
def test_malformed_envelope_never_collapses_into_a_task(text: str) -> None:
    with pytest.raises(ValueError):
        native.parse_artifact(text)


def test_explicit_bundle_preserves_members_and_relationships() -> None:
    bundle = PlanBundle(
        plans=(
            PlanMember(filename="PLAN-base.md", markdown=MARKDOWN),
            PlanMember(filename="PLAN-client.md", markdown=MARKDOWN, depends_on=("PLAN-base.md",)),
        ),
        knowledge_votes=(),
    )
    artifact = BUNDLE_MARKER + "\n```json\n" + bundle.model_dump_json() + "\n```"
    assert native.parse_artifact(artifact) == bundle


def test_literal_envelope_documentation_inside_one_plan_is_not_a_bundle() -> None:
    markdown = MARKDOWN + "\n## Example\n```text\n" + BUNDLE_MARKER + "\n```\n"
    bundle = native.parse_artifact(markdown)
    assert len(bundle.plans) == 1
    assert bundle.plans[0].markdown == markdown


@pytest.mark.parametrize("tool, sandbox", [("claude", True), ("opencode", True), ("codex", False)])
def test_explicit_no_network_is_not_silently_an_absent_grant(
    tmp_path: Path,
    tool: str,
    sandbox: bool,
) -> None:
    with pytest.raises(ValueError, match="cannot guarantee --no-network-access"):
        native.prepare_request(
            tool,
            PlanSessionRequest(prompt="plan", working_dir=tmp_path, sandbox=sandbox),
            allowed_commands=["wade *"],
            network_restriction_required=True,
        )
    assert not (tmp_path / ".wade").exists()


def test_explicit_no_network_is_preserved_for_supported_sandbox(tmp_path: Path) -> None:
    request = native.prepare_request(
        "codex",
        PlanSessionRequest(prompt="plan", working_dir=tmp_path),
        allowed_commands=["wade *"],
        network_restriction_required=True,
    )
    assert request.network_access is False
    assert request.sandbox is True


@pytest.mark.parametrize(
    "tool, requested_path", [("claude", True), ("codex", False), ("opencode", False)]
)
def test_preflight_uses_public_capabilities_without_creating_paths(
    tmp_path: Path,
    tool: str,
    requested_path: bool,
) -> None:
    root = tmp_path / "future"
    request = native.prepare_request(
        tool,
        PlanSessionRequest(prompt="plan", working_dir=root),
        allowed_commands=["wade *"],
    )
    assert (request.plan_output_dir is not None) is requested_path
    with patch(
        "crossby.utils.versioning.detect_binary_version_info",
        return_value=BinaryVersion(normalized=(9999, 9, 9), text="9999.9.9"),
    ):
        checked = preflight_plan_session(tool, request)
    assert checked.detected_version == "9999.9.9"
    assert {check.value for check in checked.deferred} == {
        "filesystem",
        "authentication",
        "model_availability",
        "protocol_negotiation",
        "artifact_collection",
    }
    assert not root.exists()


def interaction(*, multiple: bool = False, other: bool = False) -> PlanInteraction:
    return PlanInteraction(
        kind=PlanInteractionKind.QUESTION,
        question_id="native-question",
        prompt="Choose",
        session_id="native-session",
        allow_multiple=multiple,
        allow_other=other,
        options=(
            PlanQuestionOption(option_id="native-a", label="First"),
            PlanQuestionOption(option_id="native-b", label="Second"),
        ),
    )


@pytest.mark.parametrize(
    "answer, multiple, other, ids, text",
    [
        ("native-a", False, False, ("native-a",), None),
        ("native-a,native-b", True, False, ("native-a", "native-b"), None),
        ("free text", False, True, (), "free text"),
    ],
)
def test_native_questions_preserve_ids_and_free_text(
    answer: str,
    multiple: bool,
    other: bool,
    ids: tuple[str, ...],
    text: str | None,
) -> None:
    with (
        patch("wade.services.native_plan_service.prompts.is_tty", return_value=True),
        patch("builtins.input", return_value=answer),
    ):
        response = native.interact(interaction(multiple=multiple, other=other))
    selected = response.option_ids or ((response.option_id,) if response.option_id else ())
    assert selected == ids
    assert response.answer == text
    assert response.outcome is PlanInteractionOutcome.ANSWERED


@pytest.mark.parametrize(
    "error, outcome",
    [
        (EOFError, PlanInteractionOutcome.SKIPPED),
        (KeyboardInterrupt, PlanInteractionOutcome.CANCELLED),
    ],
)
def test_unavailable_input_and_cancel_are_not_answers(
    error: type[BaseException],
    outcome: PlanInteractionOutcome,
) -> None:
    with (
        patch("wade.services.native_plan_service.prompts.is_tty", return_value=True),
        patch("builtins.input", side_effect=error),
    ):
        response = native.interact(interaction())
    assert response.outcome is outcome
    assert response.answer is None
    assert response.option_ids == ()


def test_noninteractive_input_is_never_invented() -> None:
    with (
        patch("wade.services.native_plan_service.prompts.is_tty", return_value=False),
        patch("builtins.input") as input_mock,
    ):
        assert native.interact(interaction()).outcome is PlanInteractionOutcome.SKIPPED
    input_mock.assert_not_called()


def test_unperformed_prompt_review_cannot_be_auto_accepted(tmp_path: Path) -> None:
    result = DelegationResult(
        success=True, feedback="Self-review instructions", mode=DelegationMode.PROMPT
    )
    with (
        patch("wade.services.review_delegation_service.review_plan", return_value=result),
        patch("wade.services.native_plan_service.prompts.is_tty", return_value=False),
    ):
        assert not native.review_materialized_plans([tmp_path / "PLAN.md"], tmp_path, yolo=True)


def test_validation_diagnostics_do_not_echo_secret_inputs() -> None:
    with pytest.raises(ValidationError) as failure:
        PlanBundle.model_validate({"plans": "SECRET-INPUT-DO-NOT-PRINT"})
    assert "SECRET-INPUT-DO-NOT-PRINT" not in native.failure_message(failure.value)
