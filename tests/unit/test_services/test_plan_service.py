"""Tests for plan service — prompt rendering, file discovery, orchestration."""

from __future__ import annotations

import errno
import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from crossby.ai_tools import AbstractAITool, PlanSessionError
from crossby.ai_tools.plan_mode import (
    PlanArtifactMalformedError,
    PlanSessionUnsupportedError,
    PlanTransportError,
)
from crossby.models.ai import (
    AIToolID,
    EffortLevel,
    PlanApprovalPolicy,
    PlanArtifactSource,
    PlanCommandPolicy,
    PlanSessionBinding,
    PlanSessionRequest,
    PlanSessionResult,
    TokenUsage,
)
from crossby.utils.versioning import BinaryVersion

from wade.git.pr import PRLookup, PRRef
from wade.models.config import (
    AICommandConfig,
    AIConfig,
    KnowledgeConfig,
    PermissionMode,
    ProjectConfig,
    ProjectSettings,
    ProviderConfig,
    ProviderID,
)
from wade.models.interactive_plan import InteractivePlanState
from wade.models.plan_bundle import BUNDLE_MARKER, PlanBundle, PlanKnowledgeVote, PlanMember
from wade.models.task import CloseReason, PlanFile, Task, TaskState
from wade.models.worktree import Worktree
from wade.services.ai_resolution import resolve_ai_tool, resolve_model
from wade.services.plan_service import (
    PLAN_FINALIZATION_FAILED,
    _attach_plan_to_existing_issue,
    _base_retarget_is_safe,
    _branch_work_in_flight,
    _create_issues_from_plans,
    _finalize_issues,
    _offer_to_implement,
    _persist_plan_issue_ref,
    _preserve_generated_plans,
    _reconcile_inflight_worktree_base,
    _select_valid_plans,
    _supersede_issue_with_plans,
    _with_supersede_banner,
    discover_plan_files,
    get_plan_prompt_template,
    plan,
    plan_done,
    render_plan_prompt,
    run_ai_planning_session,
    validate_plan_dir,
    validate_plan_files,
)
from wade.utils import safe_state
from wade.utils.safe_state import StateFileAccessError, StateFileIOError

# ---------------------------------------------------------------------------
# Prompt template tests
# ---------------------------------------------------------------------------


class TestPromptTemplate:
    def test_get_template_exists(self) -> None:
        template = get_plan_prompt_template()
        assert len(template) > 100
        assert "plan" in template.lower()
        assert "{plan_dir}" in template

    def test_render_with_plan_dir(self) -> None:
        rendered = render_plan_prompt("/tmp/wade-plan-abc123")
        assert "/tmp/wade-plan-abc123" in rendered
        assert "{plan_dir}" not in rendered
        assert ".wade/session/WORKFLOW.md" in rendered
        assert "trusted parent process" in rendered


# ---------------------------------------------------------------------------
# AI tool / model resolution tests
# ---------------------------------------------------------------------------


class TestResolveAITool:
    def test_explicit_arg_wins(self) -> None:
        config = ProjectConfig(ai=AIConfig(default_tool="copilot"))
        result = resolve_ai_tool("claude", config)
        assert result == "claude"

    def test_config_fallback(self) -> None:
        config = ProjectConfig(ai=AIConfig(default_tool="copilot"))
        result = resolve_ai_tool(None, config)
        assert result == "copilot"

    def test_detection_fallback(self) -> None:
        config = ProjectConfig()
        with patch("wade.services.plan_service.AbstractAITool.detect_installed") as mock:
            from crossby.models.ai import AIToolID

            mock.return_value = [AIToolID.CLAUDE]
            result = resolve_ai_tool(None, config)
            assert result == "claude"

    def test_no_tool_available(self) -> None:
        config = ProjectConfig()
        with patch("wade.services.plan_service.AbstractAITool.detect_installed") as mock:
            mock.return_value = []
            result = resolve_ai_tool(None, config)
            assert result is None


class TestResolveModel:
    def test_explicit_arg(self) -> None:
        config = ProjectConfig()
        result = resolve_model("claude-opus-4-6", config)
        assert result == "claude-opus-4-6"

    def test_command_specific_fallback(self) -> None:
        from wade.models.config import AICommandConfig

        config = ProjectConfig(ai=AIConfig(plan=AICommandConfig(model="claude-sonnet-4-6")))
        result = resolve_model(None, config, "plan")
        assert result == "claude-sonnet-4-6"

    def test_no_model(self) -> None:
        config = ProjectConfig()
        result = resolve_model(None, config)
        assert result is None

    def test_complexity_maps_easy(self) -> None:
        from wade.models.config import ComplexityModelMapping

        config = ProjectConfig(models={"claude": ComplexityModelMapping(easy="claude-haiku-4-5")})
        result = resolve_model(None, config, "implement", tool="claude", complexity="easy")
        assert result == "claude-haiku-4-5"

    def test_complexity_maps_complex(self) -> None:
        from wade.models.config import ComplexityModelMapping

        config = ProjectConfig(
            models={"claude": ComplexityModelMapping(complex="claude-sonnet-4-6")}
        )
        result = resolve_model(None, config, "implement", tool="claude", complexity="complex")
        assert result == "claude-sonnet-4-6"

    def test_complexity_no_mapping_falls_to_default(self) -> None:
        config = ProjectConfig(ai=AIConfig(default_model="claude-sonnet-4-6"))
        result = resolve_model(None, config, "implement", tool="claude", complexity="easy")
        assert result == "claude-sonnet-4-6"

    def test_complexity_none_falls_to_default(self) -> None:
        config = ProjectConfig(ai=AIConfig(default_model="claude-sonnet-4-6"))
        result = resolve_model(None, config, "implement", tool="claude", complexity=None)
        assert result == "claude-sonnet-4-6"

    def test_complexity_beats_default_model(self) -> None:
        """Complexity mapping must take priority over ai.default_model."""
        from wade.models.config import ComplexityModelMapping

        config = ProjectConfig(
            ai=AIConfig(default_model="claude-sonnet-4-6"),
            models={"claude": ComplexityModelMapping(easy="claude-haiku-4-5")},
        )
        result = resolve_model(None, config, "implement", tool="claude", complexity="easy")
        assert result == "claude-haiku-4-5"

    def test_command_specific_beats_complexity(self) -> None:
        """Command-specific model must take priority over complexity mapping."""
        from wade.models.config import AICommandConfig, ComplexityModelMapping

        config = ProjectConfig(
            ai=AIConfig(implement=AICommandConfig(model="claude-opus-4-6")),
            models={"claude": ComplexityModelMapping(easy="claude-haiku-4-5")},
        )
        result = resolve_model(None, config, "implement", tool="claude", complexity="easy")
        assert result == "claude-opus-4-6"

    def test_explicit_beats_everything(self) -> None:
        from wade.models.config import AICommandConfig, ComplexityModelMapping

        config = ProjectConfig(
            ai=AIConfig(
                default_model="default-model",
                implement=AICommandConfig(model="cmd-model"),
            ),
            models={"claude": ComplexityModelMapping(easy="complexity-model")},
        )
        # Omit tool= so the compatibility gate doesn't interfere;
        # this test validates fallback *priority*, not compatibility.
        result = resolve_model("explicit-model", config, "implement", complexity="easy")
        assert result == "explicit-model"

    def test_incompatible_model_returns_none(self) -> None:
        """When the resolved model is incompatible with the tool, return None."""
        config = ProjectConfig(ai=AIConfig(default_model="claude-sonnet-4-6"))
        # codex won't accept a claude model
        result = resolve_model(None, config, "implement", tool="codex")
        assert result is None


# ---------------------------------------------------------------------------
# Plan file discovery tests
# ---------------------------------------------------------------------------


class TestDiscoverPlanFiles:
    def test_discover_sorts_by_name(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN-2-feature-b.md").write_text("# Feature B\n")
        (tmp_path / "PLAN-1-feature-a.md").write_text("# feat: feature A\n")
        (tmp_path / "PLAN-3-feature-c.md").write_text("# Feature C\n")

        files = discover_plan_files(tmp_path)
        assert len(files) == 3
        assert files[0].name == "PLAN-1-feature-a.md"
        assert files[1].name == "PLAN-2-feature-b.md"
        assert files[2].name == "PLAN-3-feature-c.md"

    def test_discover_ignores_non_md(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text("# Plan\n")
        (tmp_path / "notes.txt").write_text("Some notes\n")
        (tmp_path / ".transcript").write_text("log data\n")

        files = discover_plan_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == "PLAN.md"

    def test_discover_empty_dir(self, tmp_path: Path) -> None:
        files = discover_plan_files(tmp_path)
        assert files == []

    def test_discover_nonexistent_dir(self) -> None:
        files = discover_plan_files(Path("/nonexistent"))
        assert files == []


class TestValidatePlanFiles:
    def test_validate_all_valid(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN-1.md").write_text("# feat: feature A\n\n## Tasks\n- Do A\n")
        (tmp_path / "PLAN-2.md").write_text("# feat: feature B\n\n## Tasks\n- Do B\n")

        valid = validate_plan_files(tmp_path)
        assert len(valid) == 2
        assert valid[0].title == "feat: feature A"
        assert valid[1].title == "feat: feature B"

    def test_validate_skips_invalid(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN-good.md").write_text("# fix: valid plan\n\nContent\n")
        (tmp_path / "PLAN-bad.md").write_text("No title heading\n")

        valid = validate_plan_files(tmp_path)
        assert len(valid) == 1
        assert valid[0].title == "fix: valid plan"

    def test_validate_extracts_complexity(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text(
            "# feat: complex feature\n\n## Complexity\nvery_complex\n\n## Tasks\n- Many things\n"
        )

        valid = validate_plan_files(tmp_path)
        assert len(valid) == 1
        assert valid[0].complexity is not None
        assert valid[0].complexity.value == "very_complex"

    def test_validate_empty_dir(self, tmp_path: Path) -> None:
        valid = validate_plan_files(tmp_path)
        assert valid == []


# ---------------------------------------------------------------------------
# Plan file model tests
# ---------------------------------------------------------------------------


class TestPlanFile:
    def test_from_markdown_basic(self, tmp_path: Path) -> None:
        f = tmp_path / "PLAN.md"
        f.write_text("# Add Auth\n\n## Tasks\n\n- Add login page\n")

        plan = PlanFile.from_markdown(f)
        assert plan.title == "Add Auth"
        assert "Add login page" in plan.body
        assert "tasks" in plan.sections

    def test_from_markdown_complexity(self, tmp_path: Path) -> None:
        f = tmp_path / "PLAN.md"
        f.write_text("# Feature\n\n## Complexity\nmedium\n\n## Tasks\n- Task 1\n")

        plan = PlanFile.from_markdown(f)
        assert plan.complexity is not None
        assert plan.complexity.value == "medium"

    def test_from_markdown_no_title_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text("No heading here\n\nJust text.\n")

        with pytest.raises(ValueError, match="must have a '# Title'"):
            PlanFile.from_markdown(f)

    def test_from_markdown_multiple_sections(self, tmp_path: Path) -> None:
        f = tmp_path / "PLAN.md"
        f.write_text(
            "# Feature\n\n"
            "## Complexity\neasy\n\n"
            "## Tasks\n- Do A\n- Do B\n\n"
            "## Acceptance Criteria\n- Works\n"
        )

        plan = PlanFile.from_markdown(f)
        assert "complexity" in plan.sections
        assert "tasks" in plan.sections
        assert "acceptance criteria" in plan.sections


# ---------------------------------------------------------------------------
# Transcript wiring tests
# ---------------------------------------------------------------------------


# Native collection replaces positional /plan prompts and launch-flag composition.
PLAN_TEXT = (
    "# feat: native test plan\n\n## Complexity\neasy\n\n"
    "## Tasks\n- [ ] Implement and test\n\n## Acceptance Criteria\n- [ ] Tests pass\n"
)


def native_result(markdown: str = PLAN_TEXT) -> PlanSessionResult:
    return PlanSessionResult(
        tool=AIToolID.CODEX,
        version="9999.9.9",
        plan=markdown,
        session_id="native-session",
        native_mode="collaborationMode.mode=plan",
        artifact_source=PlanArtifactSource.PROTOCOL_EVENT,
        binding=PlanSessionBinding.THREAD_TURN_IDS,
        thread_id="native-thread",
        turn_id="native-turn",
        artifact_id="native-plan",
        exit_code=0,
    )


def bundle_text(*members: tuple[str, str]) -> str:
    payload = {"plans": [{"filename": name, "markdown": body} for name, body in members]}
    return BUNDLE_MARKER + "\n\x60\x60\x60json\n" + json.dumps(payload) + "\n\x60\x60\x60\n"


@pytest.fixture
def collected_harness(
    tmp_path: Path,
) -> Iterator[tuple[ProjectConfig, MagicMock, MagicMock, Path]]:
    config = ProjectConfig(
        ai=AIConfig(
            default_tool="codex",
            review_plan=AICommandConfig(enabled=False),
        )
    )
    root = tmp_path / "planning-worktree"
    root.mkdir()
    provider = MagicMock()
    provider.create_task.side_effect = [
        Task(id=str(number), title="feat: native test plan") for number in range(1, 10)
    ]
    provider.read_task.return_value = Task(id="330", title="feat: existing", body="Original body")
    # Exercise the retained collector path independently of Codex's new TUI capability.
    from crossby.ai_tools.codex import CodexAdapter
    from crossby.models.ai import PlanModeActivation

    collected_caps = CodexAdapter().capabilities()
    collected_caps = collected_caps.model_copy(
        update={
            "plan_mode": collected_caps.plan_mode.model_copy(
                update={"activation": PlanModeActivation.UNSUPPORTED}
            )
        }
    )
    with (
        patch.object(CodexAdapter, "capabilities", return_value=collected_caps),
        patch("wade.services.plan_service.load_config", return_value=config),
        patch("wade.services.review_delegation_service.load_config", return_value=config),
        patch("wade.services.plan_service.get_provider", return_value=provider),
        patch(
            "crossby.utils.versioning.detect_binary_version_info",
            return_value=BinaryVersion(normalized=(9999, 9, 9), text="9999.9.9"),
        ),
        patch.object(
            AbstractAITool, "run_plan_session", autospec=True, return_value=native_result()
        ) as collect,
        patch("wade.services.plan_service.prompts.is_tty", return_value=False),
        patch("wade.git.repo.get_repo_root", return_value=tmp_path),
        patch("wade.git.worktree.create_detached_worktree", return_value=root),
        patch("wade.services.implementation_service.bootstrap_worktree"),
        patch(
            "wade.services.plan_service.bootstrap_draft_pr",
            return_value={"number": "12", "url": "https://example.test/pull/12"},
        ),
        patch("wade.services.plan_service._cleanup_plan_dir_or_worktree", return_value=True),
        patch("wade.services.plan_service.set_terminal_title"),
        patch("wade.services.plan_service.start_title_keeper"),
        patch("wade.services.plan_service.stop_title_keeper"),
    ):
        yield config, provider, collect, root


class TestCollectedSession:
    @pytest.mark.parametrize("existing", [False, True])
    def test_partial_persistence_retains_all_output_without_implementation_offer(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        existing: bool,
    ) -> None:
        _, provider, collect, _ = collected_harness
        collect.return_value = native_result(
            bundle_text(("PLAN-a.md", PLAN_TEXT), ("PLAN-b.md", PLAN_TEXT))
        )
        with (
            patch(
                "wade.services.plan_service._create_issues_from_plans",
                return_value=(["1"], ["PLAN-b.md"]),
            ),
            patch("wade.services.plan_service._offer_to_implement") as offer,
            patch("wade.services.plan_service._preserve_generated_plans") as preserve,
        ):
            assert not plan(project_root=tmp_path, issue_id="330" if existing else None)
        preserve.assert_called_once()
        offer.assert_not_called()
        provider.close_task.assert_not_called()

    @pytest.mark.parametrize("issue_id", [None, "330"])
    def test_failed_implementation_handoff_does_not_preserve_persisted_plans(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        issue_id: str | None,
    ) -> None:
        with (
            patch("wade.services.plan_service._attach_plan_to_existing_issue", return_value=True),
            patch("wade.services.plan_service._finalize_issues", return_value=False),
            patch("wade.services.plan_service._preserve_generated_plans") as preserve,
            patch(
                "wade.services.plan_service._cleanup_plan_dir_or_worktree", return_value=True
            ) as cleanup,
        ):
            assert not plan(project_root=tmp_path, issue_id=issue_id)
        preserve.assert_not_called()
        cleanup.assert_called_once()

    def test_finalization_failure_retains_registered_handoff(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        """Recovery state survives a failure after the issue and draft PR persist."""
        _, provider, _, root = collected_harness

        with (
            patch(
                "wade.services.plan_service._finalize_issues",
                return_value=PLAN_FINALIZATION_FAILED,
            ),
            patch("wade.services.plan_service._preserve_generated_plans") as preserve,
            patch("wade.services.plan_service._retain_inaccessible_handoff") as retain,
        ):
            assert not plan(project_root=tmp_path)

        assert root.is_dir()
        progress = json.loads((root / ".wade/plans/handoff-progress.json").read_text())
        assert progress["persisted_issues"] == {"PLAN.md": "1"}
        retain.assert_called_once_with(str(root / ".wade/plans"), root, access_denied=False)
        preserve.assert_not_called()
        provider.create_task.assert_called_once()

    def test_partial_finalization_with_pending_marker_retains_registered_handoff(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        """A marker can still reconcile an externally created task after a partial batch."""
        _, _, collect, root = collected_harness
        collect.return_value = native_result(
            bundle_text(("PLAN-a.md", PLAN_TEXT), ("PLAN-b.md", PLAN_TEXT))
        )

        def partially_persist(
            **kwargs: object,
        ) -> tuple[list[str], list[str]]:
            pending = kwargs["pending_issue_markers"]
            assert isinstance(pending, dict)
            pending["PLAN-b.md"] = "<!-- wade:plan-handoff:pending -->"
            return ["1"], ["PLAN-b.md"]

        with (
            patch(
                "wade.services.plan_service._create_issues_from_plans",
                side_effect=partially_persist,
            ),
            patch(
                "wade.services.plan_service._finalize_issues",
                return_value=PLAN_FINALIZATION_FAILED,
            ),
            patch("wade.services.plan_service._preserve_generated_plans") as preserve,
            patch("wade.services.plan_service._retain_inaccessible_handoff") as retain,
        ):
            assert not plan(project_root=tmp_path)

        assert root.is_dir()
        retain.assert_called_once()
        assert retain.call_args.kwargs["access_denied"] is False
        preserve.assert_not_called()

    def test_terminal_collector_receives_public_identity_bearing_consent(
        self, tmp_path: Path
    ) -> None:
        from crossby.ai_tools import terminal_interaction_handler

        with (
            patch.object(
                AbstractAITool, "run_plan_session", return_value=native_result()
            ) as collect,
            patch("wade.services.plan_service.prompts.is_tty", return_value=True),
        ):
            run_ai_planning_session(
                "claude",
                str(tmp_path / ".wade/plans"),
                request=PlanSessionRequest(prompt="plan", working_dir=tmp_path),
            )
        assert collect.call_args.kwargs["interaction_handler"] is terminal_interaction_handler

    def test_returned_plan_after_callback_cancellation_is_not_success(self, tmp_path: Path) -> None:
        from crossby.models.ai import (
            PlanInteraction,
            PlanInteractionKind,
            PlanInteractionOutcome,
            PlanInteractionResponse,
        )

        def collect(
            request: PlanSessionRequest, *, interaction_handler: object
        ) -> PlanSessionResult:
            assert callable(interaction_handler)
            interaction_handler(
                PlanInteraction(
                    kind=PlanInteractionKind.PLAN_APPROVAL,
                    question_id="final",
                    prompt="Keep plan?",
                    session_id="native-session",
                )
            )
            return native_result()

        with (
            patch.object(AbstractAITool, "run_plan_session", side_effect=collect),
            patch("wade.services.plan_service.prompts.is_tty", return_value=True),
            patch(
                "wade.services.native_plan_service.interact",
                return_value=PlanInteractionResponse(outcome=PlanInteractionOutcome.CANCELLED),
            ),
            pytest.raises(KeyboardInterrupt),
        ):
            run_ai_planning_session(
                "codex",
                str(tmp_path / ".wade/plans"),
                request=PlanSessionRequest(prompt="plan", working_dir=tmp_path),
            )
        assert (tmp_path / ".wade/plans/native-session.json").is_file()
        assert not (tmp_path / ".wade/plans/PLAN.md").exists()

    @pytest.mark.parametrize("vote_id", ["known-entry", "unknown-entry"])
    def test_knowledge_votes_are_validated_and_staged_in_parent(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        vote_id: str,
    ) -> None:
        config, provider, collect, root = collected_harness
        config.knowledge.enabled = True
        (root / "KNOWLEDGE.md").write_text("## known-entry | 2026-09-15 | plan\nA useful fact.\n")
        payload = {
            "plans": [{"filename": "PLAN.md", "markdown": PLAN_TEXT}],
            "knowledge_votes": [{"entry_id": vote_id, "direction": "up"}],
        }
        collect.return_value = native_result(
            BUNDLE_MARKER + "\n```json\n" + json.dumps(payload) + "\n```"
        )
        with patch("wade.services.knowledge_service.record_handoff_rating_for_session") as rate:
            assert plan(project_root=tmp_path) is (vote_id == "known-entry")
        if vote_id == "known-entry":
            rate.assert_called_once_with(root, config.knowledge, vote_id, "up", "native-session")
        else:
            rate.assert_not_called()
            provider.create_task.assert_not_called()

    def test_raw_prompt_and_exact_request(self, tmp_path: Path) -> None:
        request = PlanSessionRequest(
            prompt="preflight",
            working_dir=tmp_path,
            model="gpt-5.6",
            effort=EffortLevel.HIGH,
            sandbox=True,
            network_access=False,
            approval_policy=PlanApprovalPolicy.NEVER,
        )
        with (
            patch.object(
                AbstractAITool, "run_plan_session", autospec=True, return_value=native_result()
            ) as collect,
            patch("wade.services.plan_service.prompts.is_tty", return_value=False),
        ):
            result = run_ai_planning_session(
                "codex", str(tmp_path / ".wade/plans"), request=request
            )
        actual = collect.call_args.args[1]
        assert isinstance(actual, PlanSessionRequest)
        assert actual.prompt.startswith("# Managed planning session")
        assert not actual.prompt.startswith("/plan")
        assert actual.model == "gpt-5.6"
        assert actual.effort is EffortLevel.HIGH
        assert actual.approval_policy is PlanApprovalPolicy.NEVER
        assert actual.network_access is False
        assert actual.sandbox is True
        assert collect.call_args.kwargs == {"interaction_handler": None}
        assert result == native_result()
        assert not (tmp_path / ".wade/plans/.transcript").exists()

    def test_unknown_tool_is_not_launched_directly(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            run_ai_planning_session(
                "not-a-tool",
                str(tmp_path / ".wade/plans"),
                request=PlanSessionRequest(prompt="plan", working_dir=tmp_path),
            )

    @pytest.mark.parametrize("mode", [PermissionMode.DEFAULT, PermissionMode.YOLO])
    def test_parent_autonomy_never_changes_native_request(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        mode: PermissionMode,
    ) -> None:
        config, provider, collect, root = collected_harness
        config.ai.permission_mode = mode.value
        assert plan(project_root=tmp_path)
        request = collect.call_args.args[1]
        assert isinstance(request, PlanSessionRequest)
        assert request.working_dir == root
        assert request.command_policy == PlanCommandPolicy(allowed_commands=("wade *",))
        assert request.trusted_dirs == ()
        assert request.network_access is False
        assert request.approval_policy is PlanApprovalPolicy.ON_REQUEST
        assert request.plan_output_dir is None
        assert provider.create_task.call_count == 1
        assert all(key not in request.model_dump() for key in ("yolo", "auto", "accept_edits"))
        bodies = [call.kwargs.get("body", "") for call in provider.update_task.call_args_list]
        assert any("native-session" in body and "protocol_event" in body for body in bodies)
        assert not any("Total tokens" in body for body in bodies)


class TestFinalizeIssues:
    def test_native_provenance_failure_requires_plan_preservation(self) -> None:
        provider = MagicMock()
        provider.read_task.side_effect = RuntimeError("API error")

        with (
            patch("wade.services.plan_service.add_planned_by_labels"),
            patch("wade.services.plan_service.console"),
        ):
            result = _finalize_issues(
                provider=provider,
                config=ProjectConfig(),
                issue_numbers=["1"],
                native_result=native_result(),
            )

        assert result is PLAN_FINALIZATION_FAILED

    def test_label_failure_does_not_abort(self) -> None:
        """A failing add_planned_by_labels must not prevent finalization."""
        provider = MagicMock()
        config = ProjectConfig()

        with (
            patch(
                "wade.services.plan_service.add_planned_by_labels",
                side_effect=RuntimeError("API error"),
            ),
            patch("wade.services.plan_service.apply_plan_token_usage"),
            patch("wade.services.plan_service.console") as mock_console,
        ):
            # Must not raise
            _finalize_issues(
                provider=provider,
                config=config,
                issue_numbers=["1", "2"],
                ai_tool="claude",
                model="opus",
                usage=None,
            )

        # Warnings emitted for both issues
        assert mock_console.warn.call_count == 2

    def test_auto_deps_explicit_flags_are_false(self) -> None:
        """Auto-deps call must use ai_explicit=False and model_explicit=False."""
        provider = MagicMock()
        config = ProjectConfig()

        with (
            patch("wade.services.plan_service.add_planned_by_labels"),
            patch("wade.services.plan_service.apply_plan_token_usage"),
            patch("wade.services.deps_service.analyze_deps") as mock_analyze_deps,
            patch("wade.services.plan_service.console"),
        ):
            _finalize_issues(
                provider=provider,
                config=config,
                issue_numbers=["1", "2"],
                ai_tool="claude",
                model="opus",
                usage=None,
            )

        # Verify analyze_deps was called with ai_explicit=False, model_explicit=False
        mock_analyze_deps.assert_called_once()
        call_kwargs = mock_analyze_deps.call_args.kwargs
        assert call_kwargs["ai_tool"] == "claude"
        assert call_kwargs["model"] == "opus"
        assert call_kwargs["ai_explicit"] is False
        assert call_kwargs["model_explicit"] is False

    def test_vote_handoff_failure_waits_until_other_finalization_completes(
        self, tmp_path: Path
    ) -> None:
        """A transient main-write failure must not lose completed plan bookkeeping."""
        from wade.models.config import KnowledgeConfig
        from wade.services.knowledge_service import StagedRatingsFlushResult

        provider = MagicMock()
        task = MagicMock(id="1", title="Issue", body="")
        provider.read_task.return_value = task
        config = ProjectConfig(knowledge=KnowledgeConfig(enabled=True))
        usage = TokenUsage(total_tokens=42)

        def offer_and_run_handoff(
            _issue_number: str,
            *,
            before_start: Callable[[], bool] | None = None,
        ) -> bool:
            assert before_start is not None
            return before_start()

        with (
            patch("wade.services.plan_service.apply_plan_token_usage") as apply_usage,
            patch("wade.services.plan_service.add_planned_by_labels") as add_labels,
            patch(
                "wade.services.knowledge_service.flush_staged_ratings",
                return_value=StagedRatingsFlushResult(
                    success=False, message="main checkout is read-only"
                ),
            ),
            patch(
                "wade.services.plan_service._offer_to_implement",
                side_effect=offer_and_run_handoff,
            ),
            patch("wade.services.plan_service.console") as mock_console,
        ):
            result = _finalize_issues(
                provider=provider,
                config=config,
                issue_numbers=["1"],
                ai_tool="claude",
                usage=usage,
                repo_root=tmp_path,
                planning_worktree=tmp_path / "planning-worktree",
            )

        assert result is False
        apply_usage.assert_called_once()
        add_labels.assert_called_once_with(provider, "1", "claude", None)
        # The plan() caller immediately retries this handoff during cleanup;
        # it emits the sole recovery message if that retry also fails.
        mock_console.error.assert_not_called()
        mock_console.hint.assert_not_called()


# ---------------------------------------------------------------------------
# validate_plan_dir tests
# ---------------------------------------------------------------------------


class TestValidatePlanDir:
    def test_empty_dir_returns_error(self, tmp_path: Path) -> None:
        result = validate_plan_dir(tmp_path)
        assert result.has_errors
        assert any("No plan files" in d.message for d in result.errors)

    def test_nonexistent_dir_returns_error(self, tmp_path: Path) -> None:
        result = validate_plan_dir(tmp_path / "does-not-exist")
        assert result.has_errors

    def test_valid_plan_passes(self, tmp_path: Path) -> None:
        content = (
            "# feat: my feature\n\n## Complexity\nmedium\n\n"
            "## Tasks\n- [ ] Do it\n\n## Acceptance Criteria\n- [ ] It works\n"
        )
        (tmp_path / "PLAN.md").write_text(content)
        result = validate_plan_dir(tmp_path)
        assert not result.has_errors
        assert not result.warnings

    def test_missing_title_produces_error(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text("No heading here\n\nJust text.\n")
        result = validate_plan_dir(tmp_path)
        assert result.has_errors
        assert any("# Title" in d.message for d in result.errors)

    def test_missing_complexity_produces_error(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text("# feat: my feature\n\n## Tasks\n- [ ] Do it\n")
        result = validate_plan_dir(tmp_path)
        assert result.has_errors
        assert any("Complexity" in d.message for d in result.errors)

    def test_invalid_complexity_produces_error(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text(
            "# feat: my feature\n\n## Complexity\nbogus_value\n\n## Tasks\n- [ ] Do it\n"
        )
        result = validate_plan_dir(tmp_path)
        assert result.has_errors
        assert any("Complexity" in d.message for d in result.errors)

    def test_missing_tasks_section_produces_warning(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text(
            "# feat: my feature\n\n## Complexity\nmedium\n\n"
            "## Acceptance Criteria\n- [ ] It works\n"
        )
        result = validate_plan_dir(tmp_path)
        assert not result.has_errors
        assert any("Tasks" in d.message for d in result.warnings)

    def test_missing_acceptance_criteria_produces_warning(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text(
            "# feat: my feature\n\n## Complexity\nmedium\n\n## Tasks\n- [ ] Do it\n"
        )
        result = validate_plan_dir(tmp_path)
        assert not result.has_errors
        assert any("Acceptance Criteria" in d.message for d in result.warnings)

    def test_multiple_files_all_validated(self, tmp_path: Path) -> None:
        content_a = (
            "# feat: feature A\n\n## Complexity\nmedium\n\n"
            "## Tasks\n- [ ] A\n\n## Acceptance Criteria\n- [ ] AC\n"
        )
        (tmp_path / "PLAN-1-a.md").write_text(content_a)
        (tmp_path / "PLAN-2-b.md").write_text("No title here\n")
        result = validate_plan_dir(tmp_path)
        assert result.has_errors
        assert any(d.file == "PLAN-2-b.md" for d in result.errors)

    def test_errors_collected_across_files(self, tmp_path: Path) -> None:
        """All files are validated — errors are not fail-fast."""
        (tmp_path / "PLAN-1-a.md").write_text("No title\n")
        (tmp_path / "PLAN-2-b.md").write_text("Also no title\n")
        result = validate_plan_dir(tmp_path)
        assert len(result.errors) == 2

    def test_diagnostic_includes_filename(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN-my-plan.md").write_text("No title\n")
        result = validate_plan_dir(tmp_path)
        assert result.errors[0].file == "PLAN-my-plan.md"


# ---------------------------------------------------------------------------
# plan_done tests
# ---------------------------------------------------------------------------


class TestPlanDone:
    def test_returns_no_errors_for_valid_plans(self, tmp_path: Path) -> None:
        content = (
            "# feat: add retry logic\n\n## Complexity\nmedium\n\n"
            "## Tasks\n- [ ] Do it\n\n## Acceptance Criteria\n- [ ] Works\n"
        )
        (tmp_path / "PLAN.md").write_text(content)
        assert not plan_done(tmp_path).has_errors

    def test_returns_errors_for_invalid_plans(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text("No title heading\n")
        assert plan_done(tmp_path).has_errors

    def test_no_errors_with_warnings_only(self, tmp_path: Path) -> None:
        """Warnings (missing recommended sections) must not produce errors."""
        (tmp_path / "PLAN.md").write_text(
            "# fix: correct timeout handling\n\n## Complexity\nmedium\n\n"
            "No tasks or criteria sections.\n"
        )
        result = plan_done(tmp_path)
        assert not result.has_errors
        assert result.warnings

    def test_error_when_title_missing_conventional_prefix(self, tmp_path: Path) -> None:
        """Title without a conventional commit prefix must produce an error."""
        (tmp_path / "PLAN.md").write_text(
            "# Add retry logic\n\n## Complexity\nmedium\n\n"
            "## Tasks\n- [ ] Do it\n\n## Acceptance Criteria\n- [ ] Works\n"
        )
        result = plan_done(tmp_path)
        assert result.has_errors
        assert any("conventional commit" in d.message for d in result.errors)


class TestPlanOrchestrator:
    @pytest.mark.parametrize("tool", ["claude", "cursor", "copilot", "opencode", "antigravity-cli"])
    def test_native_terminal_launch_hands_off_through_done(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        tool: str,
    ) -> None:
        from wade.models.workflow import SessionKind
        from wade.services import interactive_plan_service as interactive
        from wade.services.session_composition_service import compose_session

        config, provider, collect, root = collected_harness
        config.ai.default_tool = tool
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)

        def run(argv: list[str], *_args: object, **kwargs: object) -> int:
            assert kwargs["cwd"] == root
            assert "plan" in argv or "--plan" in argv
            assert "app-server" not in argv and "--print" not in argv
            if tool == "claude":
                import json

                settings = json.loads(argv[argv.index("--settings") + 1])
                assert settings["plansDirectory"] == "./.wade/plans/native"
            interactive.import_artifact(root, "# fix: native terminal\n\n## Complexity\neasy\n")
            interactive.complete(root)
            return 0

        with (
            patch("crossby.utils.versioning.detect_binary_version", return_value=(9999, 0, 0)),
            patch("crossby.utils.process.run_with_transcript", side_effect=run) as launch,
        ):
            assert plan(project_root=tmp_path, permission_mode="yolo")
        launch.assert_called_once()
        collect.assert_not_called()
        provider.create_task.assert_called_once()

    def test_parent_permission_denial_retains_completed_native_handoff(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        from wade.models.workflow import SessionKind
        from wade.services import interactive_plan_service as interactive
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        config.ai.default_tool = "claude"
        config.ai.review_plan.enabled = True
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        deny_parent = False
        original_open = os.open

        def run(*_args: object, **_kwargs: object) -> int:
            nonlocal deny_parent
            interactive.import_artifact(root, PLAN_TEXT)
            interactive.record_review(root / ".wade/plans/PLAN.md", PLAN_TEXT, self_review=False)
            interactive.complete(root)
            deny_parent = True
            return 0

        def denied_open(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if deny_parent and Path(path).name == interactive.STATE:
                raise PermissionError(13, "denied", os.fspath(path))
            return original_open(path, flags, mode, dir_fd=dir_fd)

        with (
            patch("crossby.utils.versioning.detect_binary_version", return_value=(9999, 0, 0)),
            patch("crossby.utils.process.run_with_transcript", side_effect=run),
            patch.object(safe_state.os, "open", side_effect=denied_open),
            patch("wade.services.plan_service._cleanup_plan_dir_or_worktree") as cleanup,
            patch("wade.services.plan_service.console") as mock_console,
        ):
            assert not plan(project_root=tmp_path, permission_mode="yolo")

        provider.create_task.assert_not_called()
        provider.create_label.assert_not_called()
        cleanup.assert_not_called()
        assert root.is_dir()
        error = " ".join(str(call) for call in mock_console.error.call_args_list)
        hints = " ".join(str(call) for call in mock_console.hint.call_args_list)
        assert "permission denied" in error.lower()
        assert "--recover" in hints

    def test_interactive_handoff_progress_precedes_preparation(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        """A failed preparation still leaves the completed interactive handoff retryable."""
        from wade.models.workflow import SessionKind
        from wade.services import interactive_plan_service as interactive
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        config.ai.default_tool = "claude"
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)

        def run(*_args: object, **_kwargs: object) -> int:
            interactive.import_artifact(root, PLAN_TEXT)
            interactive.complete(root)
            return 0

        with (
            patch("crossby.utils.versioning.detect_binary_version", return_value=(9999, 0, 0)),
            patch("crossby.utils.process.run_with_transcript", side_effect=run),
            patch(
                "wade.services.plan_service._prepare_plan_handoff",
                side_effect=PermissionError(errno.EACCES, "denied", str(root)),
            ) as prepare,
        ):
            assert not plan(project_root=tmp_path, model="claude-sonnet-4-6")

        progress = json.loads((root / ".wade/plans/handoff-progress.json").read_text())
        assert progress["session_id"] == prepare.call_args.kwargs["handoff_id"]
        assert progress["model"] == "claude-sonnet-4-6"
        provider.create_label.assert_not_called()

    def test_collector_network_default_does_not_become_a_native_requirement(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        """The short alias policy must not reject native tools without a network toggle."""
        from wade.models.workflow import SessionKind
        from wade.services import interactive_plan_service as interactive
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        config.ai.default_tool = "claude"
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)

        def run(*_args: object, **_kwargs: object) -> int:
            interactive.import_artifact(root, PLAN_TEXT)
            interactive.complete(root)
            return 0

        with (
            patch("crossby.utils.versioning.detect_binary_version", return_value=(9999, 0, 0)),
            patch("crossby.utils.process.run_with_transcript", side_effect=run),
        ):
            assert plan(project_root=tmp_path, collector_network_access=True)

        provider.create_task.assert_called_once()

    def test_collector_network_override_reaches_collector_launch(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        """The alias-only override still pins collector network access on."""
        _, _, collect, _ = collected_harness

        assert plan(project_root=tmp_path, collector_network_access=True)

        assert collect.call_args.args[1].network_access is True

    def test_progress_write_failure_retains_a_materialized_completed_handoff(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        """Recovery can consume a handoff after its initial progress write is retried."""
        from wade.models.workflow import SessionKind
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)

        with (
            patch("wade.services.plan_service._save_handoff_progress", return_value=False),
            patch("wade.services.plan_service._cleanup_plan_dir_or_worktree") as cleanup,
        ):
            assert not plan(project_root=tmp_path)

        cleanup.assert_not_called()
        assert (root / ".wade/plans/native-session.json").is_file()
        assert (root / ".wade/plans/PLAN.md").is_file()

        with patch(
            "wade.git.worktree.list_worktrees",
            return_value=[Worktree(path=str(root), branch="(detached)")],
        ):
            assert plan(project_root=tmp_path, recover=root)

        provider.create_task.assert_called_once()

    def test_recovery_reconciles_task_when_post_creation_progress_write_fails(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        """A retained task marker prevents a retry from creating the task twice."""
        from wade.models.workflow import SessionKind
        from wade.services import plan_service
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        original_settings = ProjectSettings(
            issue_label="original-plan",
            branch_prefix="original",
            main_branch="original-main",
        )
        config.project = original_settings
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        original_save = plan_service._save_handoff_progress
        writes = 0

        def fail_post_creation_write(path: Path, progress: object) -> bool:
            nonlocal writes
            writes += 1
            if writes == 3:
                return False
            return original_save(path, progress)  # type: ignore[arg-type]

        with patch(
            "wade.services.plan_service._save_handoff_progress",
            side_effect=fail_post_creation_write,
        ):
            assert not plan(project_root=tmp_path)

        assert provider.create_task.call_count == 1
        progress = json.loads((root / ".wade/plans/handoff-progress.json").read_text())
        marker = progress["pending_issue_markers"]["PLAN.md"]
        assert marker in provider.create_task.call_args.kwargs["body"]
        provider.list_tasks.return_value = [
            Task(id="1", title="feat: native test plan", body=marker)
        ]
        provider.find_tasks_by_body_marker.return_value = [
            Task(id="1", title="feat: native test plan", body=marker)
        ]
        config.project = ProjectSettings(
            issue_label="changed-plan",
            branch_prefix="changed",
            main_branch="changed-main",
        )

        with patch(
            "wade.git.worktree.list_worktrees",
            return_value=[Worktree(path=str(root), branch="(detached)")],
        ):
            assert plan(project_root=tmp_path, recover=root)

        assert provider.create_task.call_count == 1
        provider.find_tasks_by_body_marker.assert_called_once_with(
            marker,
            label="original-plan",
            state=TaskState.OPEN,
        )
        recovered_config = plan_service.bootstrap_draft_pr.call_args.kwargs["config"]
        assert recovered_config.project == original_settings

    def test_recovery_reruns_strict_plan_validation_before_provider_mutation(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        from wade.models.workflow import SessionKind
        from wade.services import interactive_plan_service as interactive
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        interactive.begin(root, "claude", review_required=False)
        interactive.import_artifact(root, PLAN_TEXT)
        interactive.complete(root)

        invalid_plan = PLAN_TEXT.replace("## Complexity\neasy\n\n", "")
        plan_path = root / ".wade/plans/PLAN.md"
        plan_path.write_text(invalid_plan)
        state_path = root / ".wade/plans" / interactive.STATE
        state = InteractivePlanState.model_validate_json(state_path.read_text())
        state.bundle = PlanBundle(plans=(PlanMember(filename="PLAN.md", markdown=invalid_plan),))
        state_path.write_text(state.model_dump_json())

        with patch(
            "wade.git.worktree.list_worktrees",
            return_value=[Worktree(path=str(root), branch="(detached)")],
        ):
            assert not plan(project_root=tmp_path, recover=root)
        provider.create_task.assert_not_called()
        provider.create_label.assert_not_called()
        provider.ensure_label.assert_not_called()

    def test_recovery_revalidates_retained_handoff_then_consumes_it(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        from wade.models.workflow import SessionKind
        from wade.services import interactive_plan_service as interactive
        from wade.services.session_composition_service import compose_session

        config, provider, collect, root = collected_harness
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        interactive.begin(root, "claude", review_required=False)
        interactive.import_artifact(root, PLAN_TEXT)
        interactive.complete(root)
        linked = [Worktree(path=str(root), branch="(detached)")]
        original_open = os.open

        def denied_state_open(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if Path(path).name == interactive.STATE:
                raise PermissionError(13, "denied", os.fspath(path))
            return original_open(path, flags, mode, dir_fd=dir_fd)

        with (
            patch("wade.git.worktree.list_worktrees", return_value=linked),
            patch.object(safe_state.os, "open", side_effect=denied_state_open),
        ):
            assert not plan(project_root=tmp_path, recover=root)
        provider.create_task.assert_not_called()
        provider.create_label.assert_not_called()
        assert root.is_dir()

        with patch("wade.git.worktree.list_worktrees", return_value=linked):
            assert plan(project_root=tmp_path, recover=root)
        collect.assert_not_called()
        provider.create_task.assert_called_once()

    def test_recovery_deduplicates_staged_handoff_votes_after_provider_retry(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        from wade.models.workflow import SessionKind
        from wade.services import interactive_plan_service as interactive
        from wade.services.knowledge_service import staged_ratings_path
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        config.knowledge.enabled = True
        (root / "KNOWLEDGE.md").write_text("## known-entry | 2026-09-15 | plan\nA useful fact.\n")
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        interactive.begin(root, "claude", review_required=False, knowledge_required=True)
        bundle = PlanBundle(
            plans=(PlanMember(filename="PLAN.md", markdown=PLAN_TEXT),),
            knowledge_votes=({"entry_id": "known-entry", "direction": "up"},),
        )
        interactive.import_artifact(
            root,
            BUNDLE_MARKER + "\n```json\n" + bundle.model_dump_json() + "\n```",
        )
        interactive.complete(root)
        linked = [Worktree(path=str(root), branch="(detached)")]

        with (
            patch("wade.git.worktree.list_worktrees", return_value=linked),
            patch(
                "wade.services.knowledge_service.is_throwaway_knowledge_session",
                return_value=True,
            ),
            patch(
                "wade.services.plan_service.ensure_task_label",
                side_effect=[RuntimeError("provider unavailable"), None],
            ),
        ):
            assert not plan(project_root=tmp_path, recover=root)
            first_attempt = [
                json.loads(line)
                for line in staged_ratings_path(root).read_text(encoding="utf-8").splitlines()
            ]
            assert len(first_attempt) == 1

            assert plan(project_root=tmp_path, recover=root)

        retries = [
            json.loads(line)
            for line in staged_ratings_path(root).read_text(encoding="utf-8").splitlines()
        ]
        assert retries == first_attempt
        provider.create_task.assert_called_once()

    def test_recovery_rejects_changed_handoff_votes_before_provider_mutation(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        """A revised retained bundle cannot reuse an already staged vote identity."""
        from wade.models.workflow import SessionKind
        from wade.services import interactive_plan_service as interactive
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        config.knowledge = KnowledgeConfig(enabled=True)
        (root / "KNOWLEDGE.md").write_text("## known-entry | 2026-09-15 | plan\nA useful fact.\n")
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        interactive.begin(root, "claude", review_required=False, knowledge_required=True)
        bundle = PlanBundle(
            plans=(PlanMember(filename="PLAN.md", markdown=PLAN_TEXT),),
            knowledge_votes=({"entry_id": "known-entry", "direction": "up"},),
        )
        interactive.import_artifact(
            root,
            BUNDLE_MARKER + "\n```json\n" + bundle.model_dump_json() + "\n```",
        )
        interactive.complete(root)
        linked = [Worktree(path=str(root), branch="(detached)")]

        with (
            patch("wade.git.worktree.list_worktrees", return_value=linked),
            patch(
                "wade.services.knowledge_service.is_throwaway_knowledge_session",
                return_value=True,
            ),
            patch(
                "wade.services.plan_service.ensure_task_label",
                side_effect=RuntimeError("provider unavailable"),
            ),
        ):
            assert not plan(project_root=tmp_path, recover=root)

        progress = json.loads((root / ".wade/plans/handoff-progress.json").read_text())
        assert progress["knowledge_votes"] == [{"entry_id": "known-entry", "direction": "up"}]
        state_path = root / ".wade/plans" / interactive.STATE
        state = InteractivePlanState.model_validate_json(state_path.read_text())
        assert state.bundle is not None
        state.bundle = state.bundle.model_copy(
            update={
                "knowledge_votes": (PlanKnowledgeVote(entry_id="known-entry", direction="down"),)
            }
        )
        state_path.write_text(state.model_dump_json())

        with patch("wade.git.worktree.list_worktrees", return_value=linked):
            assert not plan(project_root=tmp_path, recover=root)

        provider.create_task.assert_not_called()

    def test_plain_permission_error_during_parent_handoff_retains_worktree(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        config, provider, collect, root = collected_harness
        config.knowledge.enabled = True
        (root / "KNOWLEDGE.md").write_text("## known-entry | 2026-09-15 | plan\nA useful fact.\n")
        collect.return_value = native_result(
            BUNDLE_MARKER
            + "\n```json\n"
            + json.dumps(
                {
                    "plans": [{"filename": "PLAN.md", "markdown": PLAN_TEXT}],
                    "knowledge_votes": [{"entry_id": "known-entry", "direction": "up"}],
                }
            )
            + "\n```"
        )

        with (
            patch(
                "wade.services.knowledge_service.record_handoff_rating_for_session",
                side_effect=PermissionError(errno.EACCES, "denied", str(root)),
            ),
            patch("wade.services.plan_service._cleanup_plan_dir_or_worktree") as cleanup,
        ):
            assert not plan(project_root=tmp_path)

        cleanup.assert_not_called()
        assert root.is_dir()
        provider.create_task.assert_not_called()

    def test_recovery_consumes_retained_collector_handoff(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        config, provider, collect, root = collected_harness
        config.knowledge.enabled = True
        (root / "KNOWLEDGE.md").write_text("## known-entry | 2026-09-15 | plan\nA useful fact.\n")
        from wade.models.workflow import SessionKind
        from wade.services.session_composition_service import compose_session

        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        collect.return_value = native_result(
            BUNDLE_MARKER
            + "\n```json\n"
            + json.dumps(
                {
                    "plans": [{"filename": "PLAN.md", "markdown": PLAN_TEXT}],
                    "knowledge_votes": [{"entry_id": "known-entry", "direction": "up"}],
                }
            )
            + "\n```"
        )

        with patch(
            "wade.services.knowledge_service.record_handoff_rating_for_session",
            side_effect=PermissionError(errno.EACCES, "denied", str(root)),
        ):
            assert not plan(project_root=tmp_path)

        assert (root / ".wade/plans/native-session.json").is_file()
        assert root.is_dir()

        with patch(
            "wade.git.worktree.list_worktrees",
            return_value=[Worktree(path=str(root), branch="(detached)")],
        ):
            assert plan(project_root=tmp_path, recover=root)

        assert collect.call_count == 1
        provider.create_task.assert_called_once()

    def test_recovery_reuses_tasks_persisted_before_cleanup_failure(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        """A retained completed handoff must resume finalization, not recreate tasks."""
        from wade.models.workflow import SessionKind
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)

        with patch("wade.services.plan_service._cleanup_plan_dir_or_worktree", return_value=False):
            assert not plan(project_root=tmp_path)

        progress = json.loads((root / ".wade/plans/handoff-progress.json").read_text())
        assert progress["persisted_issues"] == {"PLAN.md": "1"}
        assert progress["persisted_plan_digests"]
        provider.create_task.assert_called_once()

        with patch(
            "wade.git.worktree.list_worktrees",
            return_value=[Worktree(path=str(root), branch="(detached)")],
        ):
            assert plan(project_root=tmp_path, recover=root)

        provider.create_task.assert_called_once()

    @pytest.mark.parametrize(
        "reviewed_plan",
        [
            PLAN_TEXT + "\n## Notes\n- Edited during review\n",
            PLAN_TEXT.replace("# feat: native test plan", "# fix: native test plan"),
        ],
        ids=["body", "title"],
    )
    def test_recovery_rejects_reviewed_plan_changed_after_task_pr_persistence(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        reviewed_plan: str,
    ) -> None:
        """A retained task cannot be reused when review changes its draft-PR plan."""
        from wade.models.workflow import SessionKind
        from wade.services import plan_service
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        with patch("wade.services.plan_service._cleanup_plan_dir_or_worktree", return_value=False):
            assert not plan(project_root=tmp_path)

        progress = json.loads((root / ".wade/plans/handoff-progress.json").read_text())
        assert progress["persisted_issues"] == {"PLAN.md": "1"}
        assert progress["persisted_plan_digests"]
        provider.reset_mock()
        plan_service.bootstrap_draft_pr.reset_mock()

        def review_and_edit(paths: list[Path], *_args: object, **_kwargs: object) -> bool:
            paths[0].write_text(reviewed_plan)
            return True

        with (
            patch(
                "wade.git.worktree.list_worktrees",
                return_value=[Worktree(path=str(root), branch="(detached)")],
            ),
            patch(
                "wade.services.plan_service.native_plan.review_materialized_plans",
                side_effect=review_and_edit,
            ),
        ):
            assert not plan(project_root=tmp_path, recover=root)

        assert provider.mock_calls == []
        plan_service.bootstrap_draft_pr.assert_not_called()

    def test_recovery_uses_collector_model_recorded_with_retained_handoff(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        """Collector recovery keeps the original model for planned-by provenance."""
        from wade.models.workflow import SessionKind
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)

        with patch(
            "wade.services.plan_service._prepare_plan_handoff",
            side_effect=PermissionError(errno.EACCES, "denied", str(root)),
        ):
            assert not plan(
                project_root=tmp_path,
                model="gpt-5.2-codex",
                model_explicit=True,
            )

        config.ai.default_model = "gpt-5.3-codex"
        with (
            patch(
                "wade.git.worktree.list_worktrees",
                return_value=[Worktree(path=str(root), branch="(detached)")],
            ),
            patch("wade.services.plan_service.add_planned_by_labels") as add_labels,
        ):
            assert plan(project_root=tmp_path, recover=root)

        add_labels.assert_called_once_with(provider, "1", "codex", "gpt-5.2-codex")

    def test_recovery_rejects_changed_provider_before_provider_instantiation(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        """Retained task IDs must never be replayed through a different backend."""
        from wade.models.workflow import SessionKind
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        with patch(
            "wade.services.plan_service._prepare_plan_handoff",
            side_effect=PermissionError(errno.EACCES, "denied", str(root)),
        ):
            assert not plan(project_root=tmp_path)

        progress = json.loads((root / ".wade/plans/handoff-progress.json").read_text())
        assert progress["provider"]["name"] == ProviderID.GITHUB

        config.provider = ProviderConfig(name=ProviderID.MARKDOWN)
        with (
            patch(
                "wade.git.worktree.list_worktrees",
                return_value=[Worktree(path=str(root), branch="(detached)")],
            ),
            patch("wade.services.plan_service.get_provider") as get_provider,
        ):
            assert not plan(project_root=tmp_path, recover=root)

        get_provider.assert_not_called()
        provider.create_task.assert_not_called()
        assert root.is_dir()

    @pytest.mark.parametrize(
        ("initial_enabled", "recovery_enabled", "votes"),
        [
            (False, True, None),
            (True, False, [{"entry_id": "known-entry", "direction": "up"}]),
        ],
    )
    def test_recovery_uses_the_handoff_knowledge_requirement(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        initial_enabled: bool,
        recovery_enabled: bool,
        votes: list[dict[str, str]] | None,
    ) -> None:
        """A setting toggle must not invalidate an otherwise completed handoff."""
        from wade.models.workflow import SessionKind
        from wade.services.session_composition_service import compose_session

        config, provider, collect, root = collected_harness
        config.knowledge = KnowledgeConfig(enabled=initial_enabled)
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        if votes is not None:
            (root / "KNOWLEDGE.md").write_text(
                "## known-entry | 2026-09-15 | plan\nA useful fact.\n"
            )
            collect.return_value = native_result(
                BUNDLE_MARKER
                + "\n```json\n"
                + json.dumps(
                    {
                        "plans": [{"filename": "PLAN.md", "markdown": PLAN_TEXT}],
                        "knowledge_votes": votes,
                    }
                )
                + "\n```"
            )

        with patch(
            "wade.services.plan_service._prepare_plan_handoff",
            side_effect=PermissionError(errno.EACCES, "denied", str(root)),
        ):
            assert not plan(project_root=tmp_path)

        progress = json.loads((root / ".wade/plans/handoff-progress.json").read_text())
        assert progress["knowledge_required"] is initial_enabled
        assert progress["knowledge"] == {"enabled": initial_enabled, "path": "KNOWLEDGE.md"}
        config.knowledge = KnowledgeConfig(enabled=recovery_enabled, path="RECOVERY-KNOWLEDGE.md")

        with patch(
            "wade.git.worktree.list_worktrees",
            return_value=[Worktree(path=str(root), branch="(detached)")],
        ):
            assert plan(project_root=tmp_path, recover=root)

        provider.create_task.assert_called_once()

    @pytest.mark.parametrize(
        ("initial_enabled", "recovery_enabled", "votes"),
        [
            (False, True, None),
            (True, False, [{"entry_id": "known-entry", "direction": "up"}]),
        ],
    )
    def test_recovery_uses_frozen_knowledge_requirement_when_state_denial_precedes_progress(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        initial_enabled: bool,
        recovery_enabled: bool,
        votes: list[dict[str, str]] | None,
    ) -> None:
        """The interactive state, rather than changed settings, binds first recovery."""
        from wade.models.workflow import SessionKind
        from wade.services import interactive_plan_service as interactive
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        config.knowledge = KnowledgeConfig(enabled=initial_enabled)
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        if votes is not None:
            (root / "KNOWLEDGE.md").write_text(
                "## known-entry | 2026-09-15 | plan\nA useful fact.\n"
            )
        bundle = PlanBundle(
            plans=(PlanMember(filename="PLAN.md", markdown=PLAN_TEXT),),
            knowledge_votes=tuple(PlanKnowledgeVote(**vote) for vote in votes) if votes else None,
        )
        interactive.begin(
            root,
            "claude",
            review_required=False,
            knowledge_required=initial_enabled,
        )
        interactive.import_artifact(
            root,
            BUNDLE_MARKER + "\n```json\n" + bundle.model_dump_json() + "\n```",
        )
        interactive.complete(root)
        linked = [Worktree(path=str(root), branch="(detached)")]
        original_open = os.open

        def deny_state_open(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if Path(path).name == interactive.STATE:
                raise PermissionError(errno.EACCES, "denied", os.fspath(path))
            return original_open(path, flags, mode, dir_fd=dir_fd)

        with (
            patch("wade.git.worktree.list_worktrees", return_value=linked),
            patch.object(safe_state.os, "open", side_effect=deny_state_open),
        ):
            assert not plan(project_root=tmp_path, recover=root)

        assert not (root / ".wade/plans/handoff-progress.json").exists()
        config.knowledge.enabled = recovery_enabled

        with patch("wade.git.worktree.list_worktrees", return_value=linked):
            assert plan(project_root=tmp_path, recover=root)

        progress = json.loads((root / ".wade/plans/handoff-progress.json").read_text())
        assert progress["knowledge_required"] is initial_enabled
        assert progress["knowledge_votes"] == votes
        provider.create_task.assert_called_once()

    def test_recovery_state_io_failure_does_not_report_access_denial(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        from wade.models.workflow import SessionKind
        from wade.services import interactive_plan_service as interactive
        from wade.services.session_composition_service import compose_session

        config, _, _, root = collected_harness
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        interactive.begin(root, "claude", review_required=False)

        with (
            patch(
                "wade.git.worktree.list_worktrees",
                return_value=[Worktree(path=str(root), branch="(detached)")],
            ),
            patch(
                "wade.services.plan_service.interactive_plan.collect_with_state",
                side_effect=StateFileIOError(root, OSError(errno.EIO, "I/O failed")),
            ),
            patch("wade.services.plan_service.console") as mock_console,
        ):
            assert not plan(project_root=tmp_path, recover=root)

        hints = " ".join(str(call) for call in mock_console.hint.call_args_list)
        assert "Recover the retained handoff" in hints
        assert "After restoring filesystem access" not in hints

    @pytest.mark.parametrize("changed_gate", ["content", "review", "binding"])
    def test_recovery_rejects_changed_completed_handoff_before_provider_mutation(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        changed_gate: str,
    ) -> None:
        from wade.models.workflow import SessionKind
        from wade.services import interactive_plan_service as interactive
        from wade.services.session_composition_service import compose_session

        config, provider, _, root = collected_harness
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        interactive.begin(root, "claude", review_required=True)
        interactive.import_artifact(root, PLAN_TEXT)
        plan_path = root / ".wade/plans/PLAN.md"
        interactive.record_review(plan_path, PLAN_TEXT, self_review=False)
        interactive.complete(root)
        if changed_gate == "content":
            plan_path.write_text(PLAN_TEXT + "\nchanged\n")
        elif changed_gate == "review":
            (root / ".wade/plans" / interactive.REVIEWS).unlink()
        else:
            (root / ".wade/session/WORKFLOW.md").write_text("changed")

        with patch(
            "wade.git.worktree.list_worktrees",
            return_value=[Worktree(path=str(root), branch="(detached)")],
        ):
            assert not plan(project_root=tmp_path, recover=root)
        provider.create_task.assert_not_called()
        provider.create_label.assert_not_called()

    @pytest.mark.parametrize(
        ("tool", "kwargs"),
        [
            ("claude", {"permission_mode": "accept-edits"}),
            ("copilot", {"permission_mode": "auto"}),
            ("opencode", {"sandbox": True}),
            ("cursor", {"network_access": False}),
            ("opencode", {"trusted_dirs": [Path("/tmp")]}),
            ("antigravity-cli", {"timeout": 600}),
            ("claude", {"approval_policy": "never"}),
        ],
    )
    def test_native_unsupported_policy_fails_before_side_effects(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        tool: str,
        kwargs: dict[str, object],
    ) -> None:
        config, provider, collect, _ = collected_harness
        config.ai.default_tool = tool
        with (
            patch("wade.git.worktree.create_detached_worktree") as create,
            patch("crossby.utils.process.run_with_transcript") as launch,
        ):
            assert not plan(project_root=tmp_path, **kwargs)
        create.assert_not_called()
        launch.assert_not_called()
        collect.assert_not_called()
        provider.create_task.assert_not_called()

    @pytest.mark.parametrize("exit_code", [0, 1])
    def test_native_exit_without_completion_cannot_create_tasks(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        exit_code: int,
    ) -> None:
        from wade.models.workflow import SessionKind
        from wade.services.session_composition_service import compose_session

        config, provider, collect, root = collected_harness
        config.ai.default_tool = "claude"
        compose_session(root, tmp_path, config, kind=SessionKind.PLAN, task_id=None)
        with (
            patch("crossby.utils.versioning.detect_binary_version", return_value=(9999, 0, 0)),
            patch("crossby.utils.process.run_with_transcript", return_value=exit_code),
            patch("wade.services.plan_service._preserve_generated_plans") as preserve,
        ):
            assert not plan(project_root=tmp_path)
        collect.assert_not_called()
        provider.create_task.assert_not_called()
        preserve.assert_called_once()

    def test_no_ai_tool(self) -> None:
        with (
            patch("wade.services.plan_service.load_config", return_value=ProjectConfig()),
            patch("wade.services.plan_service.get_provider"),
            patch("wade.services.plan_service.resolve_ai_tool", return_value=None),
        ):
            assert not plan()

    @pytest.mark.parametrize("selected", ["not-a-tool", "vscode", "antigravity"])
    def test_final_selection_preflight_has_no_side_effects(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        selected: str,
    ) -> None:
        _, provider, collect, _ = collected_harness
        with (
            patch(
                "wade.services.plan_service.confirm_ai_selection",
                return_value=(selected, None, None, PermissionMode.DEFAULT),
            ),
            patch("wade.git.worktree.create_detached_worktree") as create,
        ):
            assert not plan(project_root=tmp_path)
        create.assert_not_called()
        collect.assert_not_called()
        provider.create_task.assert_not_called()
        provider.create_label.assert_not_called()

    @pytest.mark.parametrize(
        "detected",
        [
            None,
            BinaryVersion(normalized=(0, 1, 0), text="codex-cli 0.1.0"),
        ],
    )
    def test_unknown_or_old_version_fails_before_worktree(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        detected: BinaryVersion | None,
    ) -> None:
        _, provider, collect, _ = collected_harness
        with (
            patch("crossby.utils.versioning.detect_binary_version_info", return_value=detected),
            patch("wade.git.worktree.create_detached_worktree") as create,
        ):
            assert not plan(project_root=tmp_path)
        create.assert_not_called()
        collect.assert_not_called()
        provider.create_task.assert_not_called()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"permission_mode": "auto"},
            {"permission_mode": "accept-edits"},
            {"effort": "invalid"},
            {"model": "claude-opus-4-6"},
            {"approval_policy": "invalid"},
            {"timeout": 3601},
            {"timeout": 0},
        ],
    )
    def test_unsupported_explicit_requirement_is_not_dropped(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        kwargs: dict[str, object],
    ) -> None:
        _, provider, collect, _ = collected_harness
        with patch("wade.git.worktree.create_detached_worktree") as create:
            assert not plan(project_root=tmp_path, **kwargs)
        create.assert_not_called()
        collect.assert_not_called()
        provider.create_task.assert_not_called()

    def test_explicit_policies_are_preserved(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        config, _, collect, _ = collected_harness
        config.permissions.allowed_commands = ["wade *", "git status"]
        assert plan(
            project_root=tmp_path,
            model="gpt-5.6",
            effort="high",
            sandbox=False,
            network_access=True,
            approval_policy="never",
            trusted_dirs=[tmp_path],
            timeout=123,
        )
        request = collect.call_args.args[1]
        assert request.model == "gpt-5.6"
        assert request.effort is EffortLevel.HIGH
        assert request.sandbox is False
        assert request.network_access is True
        assert request.approval_policy is PlanApprovalPolicy.NEVER
        assert request.trusted_dirs == (tmp_path,)
        assert request.timeout_seconds == 123
        assert request.command_policy.allowed_commands == ("wade *", "git status")

    def test_success_imports_once_and_preserves_exact_provenance(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        _, provider, collect, root = collected_harness
        assert plan(project_root=tmp_path)
        collect.assert_called_once()
        assert provider.create_task.call_count == 1
        assert (root / ".wade/plans/PLAN.md").read_text() == PLAN_TEXT
        raw = json.loads((root / ".wade/plans/native-session.json").read_text())
        assert raw == native_result().model_dump(mode="json")

    @pytest.mark.parametrize(
        "error_type",
        [
            PlanSessionUnsupportedError,
            PlanTransportError,
            PlanArtifactMalformedError,
        ],
    )
    def test_typed_runtime_failures_create_no_tasks(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        error_type: type[PlanSessionError],
    ) -> None:
        _, provider, collect, _ = collected_harness
        collect.side_effect = error_type(
            "native failure",
            tool_id=AIToolID.CODEX,
            capability=AbstractAITool.get("codex").capabilities().plan_mode,
        )
        assert not plan(project_root=tmp_path)
        provider.create_task.assert_not_called()
        provider.create_label.assert_not_called()

    def test_interruption_cleans_without_creating_a_task(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        _, provider, collect, _ = collected_harness
        collect.side_effect = KeyboardInterrupt()
        with patch("wade.services.plan_service._cleanup_plan_dir_or_worktree") as cleanup:
            assert not plan(project_root=tmp_path)
        cleanup.assert_called_once()
        provider.create_task.assert_not_called()

    def test_no_worktree_fallback_is_isolated(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        _, _, collect, _ = collected_harness
        with (
            patch("wade.git.repo.get_repo_root", side_effect=RuntimeError("no repository")),
            patch("wade.services.session_composition_service.compose_session"),
        ):
            assert plan(project_root=tmp_path)
        cwd = collect.call_args.args[1].working_dir
        assert cwd != tmp_path
        assert cwd.name.startswith("wade-plan-")
        assert (cwd / ".wade/plans/PLAN.md").is_file()

    def test_required_review_failure_preserves_output(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        _, provider, _, _ = collected_harness
        with (
            patch(
                "wade.services.native_plan_service.review_materialized_plans", return_value=False
            ),
            patch("wade.services.plan_service._preserve_generated_plans") as preserve,
        ):
            assert not plan(project_root=tmp_path)
        provider.create_task.assert_not_called()
        preserve.assert_called_once()


class TestOfferToImplement:
    """Tests for _offer_to_implement helper."""

    def test_user_accepts_starts_implementation_session(self) -> None:
        """Accepting the prompt calls start_implementation_session and returns its result."""
        with (
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.start_implementation_session") as mock_start,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.is_tty.return_value = True
            mock_prompts.confirm.return_value = True
            from wade.services.implementation_service import ImplementResult

            mock_start.return_value = ImplementResult(success=True)

            result = _offer_to_implement("42")

            assert result is True
            mock_start.assert_called_once_with(target="42", plan_handoff=True)

    def test_user_declines_returns_none(self) -> None:
        """Declining the prompt returns None without flushing or starting."""
        before_start = MagicMock(return_value=True)
        with (
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.start_implementation_session") as mock_start,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.is_tty.return_value = True
            mock_prompts.confirm.return_value = False

            result = _offer_to_implement("42", before_start=before_start)

            assert result is None
            before_start.assert_not_called()
            mock_start.assert_not_called()

    def test_non_tty_prints_static_hint(self) -> None:
        """Non-TTY environments skip the prompt and show a static hint."""
        with (
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.start_implementation_session") as mock_start,
            patch("wade.services.plan_service.console") as mock_console,
        ):
            mock_prompts.is_tty.return_value = False

            result = _offer_to_implement("42")

            assert result is None
            mock_prompts.confirm.assert_not_called()
            mock_start.assert_not_called()
            mock_console.detail.assert_called_once_with("wade implement 42")

    def test_implementation_session_failure_returns_false(self) -> None:
        """If start_implementation_session fails, the failure is propagated."""
        with (
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.start_implementation_session") as mock_start,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.is_tty.return_value = True
            mock_prompts.confirm.return_value = True
            from wade.services.implementation_service import ImplementResult

            mock_start.return_value = ImplementResult(success=False)

            result = _offer_to_implement("42")

            assert result is False

    def test_implementation_session_exception_returns_false(self) -> None:
        """If start_implementation_session raises, the exception is caught and False returned."""
        with (
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.start_implementation_session") as mock_start,
            patch("wade.services.plan_service.console"),
            patch("wade.services.plan_service.logger"),
        ):
            mock_prompts.is_tty.return_value = True
            mock_prompts.confirm.return_value = True
            mock_start.side_effect = RuntimeError("boom")

            result = _offer_to_implement("42")

            assert result is False


# ---------------------------------------------------------------------------
# _finalize_issues hint tests
# ---------------------------------------------------------------------------


class TestFinalizeIssuesHints:
    """Tests for the next-steps hint logic in _finalize_issues."""

    def _make_provider(self) -> MagicMock:
        provider = MagicMock()
        task = MagicMock()
        task.id = "1"
        task.title = "Test issue"
        task.body = ""
        provider.read_task.return_value = task
        return provider

    def _make_config(self) -> MagicMock:
        return MagicMock()

    def test_single_issue_calls_offer(self) -> None:
        """Single issue triggers _offer_to_implement."""
        with (
            patch("wade.services.plan_service._offer_to_implement") as mock_offer,
            patch("wade.services.plan_service.apply_plan_token_usage"),
            patch("wade.services.plan_service.add_planned_by_labels"),
            patch("wade.services.plan_service.console"),
        ):
            mock_offer.return_value = True

            result = _finalize_issues(
                provider=self._make_provider(),
                config=self._make_config(),
                issue_numbers=["1"],
            )

            mock_offer.assert_called_once_with("1")
            assert result is True

    def test_planner_profile_does_not_reach_the_offer(self) -> None:
        """The child planner profile is not the enclosing handoff runtime."""
        with (
            patch("wade.services.plan_service._offer_to_implement") as mock_offer,
            patch("wade.services.plan_service.apply_plan_token_usage"),
            patch("wade.services.plan_service.add_planned_by_labels"),
            patch("wade.services.plan_service.console"),
        ):
            mock_offer.return_value = True

            _finalize_issues(
                provider=self._make_provider(),
                config=self._make_config(),
                issue_numbers=["1"],
                sandbox=True,
            )

            mock_offer.assert_called_once_with("1")

    def test_multiple_issues_shows_batch_hint(self) -> None:
        """Multiple issues show wade implement-batch hint, not offer prompt."""
        with (
            patch("wade.services.plan_service._offer_to_implement") as mock_offer,
            patch("wade.services.plan_service.apply_plan_token_usage"),
            patch("wade.services.plan_service.add_planned_by_labels"),
            patch("wade.services.plan_service.console") as mock_console,
            patch("wade.services.deps_service.analyze_deps", return_value=None),
        ):
            result = _finalize_issues(
                provider=self._make_provider(),
                config=self._make_config(),
                issue_numbers=["1", "2", "3"],
            )

            mock_offer.assert_not_called()
            mock_console.detail.assert_called_with("wade implement-batch 1 2 3")
            assert all(
                call.args != ("No dependencies found between issues.",)
                for call in mock_console.info.call_args_list
            )
            assert result is None


# ---------------------------------------------------------------------------
# _attach_plan_to_existing_issue — single PlanFile
# ---------------------------------------------------------------------------


class TestAttachPlanToExistingIssue:
    def test_attaches_single_plan_file_preserving_original_body(self, tmp_path: Path) -> None:
        provider = MagicMock()
        issue = Task(id="42", title="Some issue", body="Original body")
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("# feat: thing\n\n## Tasks\n- Do it\n")
        plan_file = PlanFile.from_markdown(plan_path)

        with (
            patch(
                "wade.services.plan_service.bootstrap_draft_pr",
                return_value={"number": 99, "url": "https://example.com/pr/99"},
            ),
            patch("wade.services.plan_service.add_complexity_label"),
            patch("wade.services.plan_service.console"),
            # No open PR yet → the in-flight retarget guard is a no-op (never hits gh).
            patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup(found=False)),
        ):
            attached = _attach_plan_to_existing_issue(
                provider=provider,
                config=ProjectConfig(),
                issue=issue,
                plan_file=plan_file,
                repo_root=tmp_path,
            )

        assert attached is True
        provider.update_task.assert_called_once()
        updated_body = provider.update_task.call_args.kwargs["body"]
        assert "Original body" in updated_body
        assert "PR #99" in updated_body

    def test_replayed_attachment_does_not_duplicate_the_plan_link(self, tmp_path: Path) -> None:
        # Recovery replays the attachment after a failed finalization or cleanup.
        # bootstrap_draft_pr reuses the same open PR, so the link the body already
        # carries must not be appended a second time (#516).
        provider = MagicMock()
        issue = Task(
            id="42",
            title="Some issue",
            body="Original body\n\n**Full plan**: PR #99",
        )
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("# feat: thing\n\n## Tasks\n- Do it\n")
        plan_file = PlanFile.from_markdown(plan_path)

        with (
            patch(
                "wade.services.plan_service.bootstrap_draft_pr",
                return_value={"number": 99, "url": "https://example.com/pr/99"},
            ),
            patch("wade.services.plan_service.add_complexity_label"),
            patch("wade.services.plan_service.console"),
            patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup(found=False)),
        ):
            attached = _attach_plan_to_existing_issue(
                provider=provider,
                config=ProjectConfig(),
                issue=issue,
                plan_file=plan_file,
                repo_root=tmp_path,
            )

        assert attached is True
        provider.update_task.assert_not_called()

    def test_returns_false_when_retarget_guard_refuses(self, tmp_path: Path) -> None:
        # When the in-flight retarget guard refuses, the plan must NOT be attached
        # and the caller is told so (False) — the bootstrap is never reached (#376).
        provider = MagicMock()
        issue = Task(id="42", title="Some issue", body="Original body")
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("# feat: thing\n\n## Tasks\n- Do it\n")
        plan_file = PlanFile.from_markdown(plan_path)

        with (
            patch("wade.services.plan_service._base_retarget_is_safe", return_value=False),
            patch("wade.services.plan_service.bootstrap_draft_pr") as mock_bootstrap,
            patch("wade.services.plan_service.add_complexity_label"),
            patch("wade.services.plan_service.console"),
        ):
            attached = _attach_plan_to_existing_issue(
                provider=provider,
                config=ProjectConfig(),
                issue=issue,
                plan_file=plan_file,
                repo_root=tmp_path,
            )

        assert attached is False
        mock_bootstrap.assert_not_called()
        provider.update_task.assert_not_called()

    def test_returns_false_when_bootstrap_fails(self, tmp_path: Path) -> None:
        # bootstrap_draft_pr returning None (missing base, failed retarget, gh
        # error) must NOT finalize the issue — the plan lives only in the worktree,
        # so the caller has to preserve-and-abort rather than discard it (#376).
        provider = MagicMock()
        issue = Task(id="42", title="Some issue", body="Original body")
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("# feat: thing\n\n## Tasks\n- Do it\n")
        plan_file = PlanFile.from_markdown(plan_path)

        with (
            patch("wade.services.plan_service._base_retarget_is_safe", return_value=True),
            patch("wade.services.plan_service.bootstrap_draft_pr", return_value=None),
            patch("wade.services.plan_service.add_complexity_label"),
            patch("wade.services.plan_service.console"),
        ):
            attached = _attach_plan_to_existing_issue(
                provider=provider,
                config=ProjectConfig(),
                issue=issue,
                plan_file=plan_file,
                repo_root=tmp_path,
            )

        assert attached is False
        provider.update_task.assert_not_called()

    def test_returns_false_when_reconcile_fails(self, tmp_path: Path) -> None:
        # The PR was retargeted, but the in-flight worktree's pin could not be
        # updated — a resumed session would merge into the old base. Abort so the
        # plan is preserved instead of finalizing on a divergent target (#376).
        provider = MagicMock()
        issue = Task(id="42", title="Some issue", body="Original body")
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("# feat: thing\n\n## Tasks\n- Do it\n")
        plan_file = PlanFile.from_markdown(plan_path)

        with (
            patch("wade.services.plan_service._base_retarget_is_safe", return_value=True),
            patch(
                "wade.services.plan_service.bootstrap_draft_pr",
                return_value={"number": 99, "url": "http://x/99"},
            ),
            patch(
                "wade.services.plan_service._reconcile_inflight_worktree_base",
                return_value=False,
            ),
            patch("wade.services.plan_service.add_complexity_label"),
            patch("wade.services.plan_service.console"),
        ):
            attached = _attach_plan_to_existing_issue(
                provider=provider,
                config=ProjectConfig(),
                issue=issue,
                plan_file=plan_file,
                repo_root=tmp_path,
            )

        assert attached is False
        provider.update_task.assert_not_called()


# ---------------------------------------------------------------------------
# Base branch (#376) — plan pipeline threading + in-flight retarget guard
# ---------------------------------------------------------------------------


def _open_pr_lookup(number: int = 99, base: str = "main") -> PRLookup:
    return PRLookup(
        found=True,
        pr=PRRef(number=number, url="http://x", state="OPEN", baseRefName=base),
    )


def _cfg_main() -> ProjectConfig:
    """ProjectConfig with main_branch set to avoid detect_main_branch subprocess."""
    return ProjectConfig(project=ProjectSettings(main_branch="main"))


class TestCreateIssuesFromPlansBaseBranch:
    def _make_plan(self, tmp_path: Path, *, base_section: str = "") -> PlanFile:
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(
            "# feat: thing\n\n## Complexity\nmedium\n" + base_section + "\n## Tasks\n- Do it\n"
        )
        return PlanFile.from_markdown(plan_path)

    def test_threads_declared_base_into_bootstrap(self, tmp_path: Path) -> None:
        plan_file = self._make_plan(tmp_path, base_section="\n## Base Branch\ndevelop\n")
        assert plan_file.base_branch == "develop"
        provider = MagicMock()
        provider.create_task.return_value = Task(id="7", title="feat: thing")

        with (
            patch(
                "wade.services.plan_service.bootstrap_draft_pr",
                return_value={"number": 5, "url": "http://x/5"},
            ) as mock_bootstrap,
            patch("wade.services.plan_service.add_complexity_label"),
            patch("wade.services.plan_service.console"),
        ):
            created, failed = _create_issues_from_plans(
                provider=provider,
                config=_cfg_main(),
                plan_files=[plan_file],
                repo_root=tmp_path,
            )

        assert created == ["7"]
        assert failed == []
        assert mock_bootstrap.call_args.kwargs["base_branch"] == "develop"

    def test_absent_base_section_passes_none(self, tmp_path: Path) -> None:
        plan_file = self._make_plan(tmp_path)
        assert plan_file.base_branch is None
        provider = MagicMock()
        provider.create_task.return_value = Task(id="8", title="feat: thing")

        with (
            patch(
                "wade.services.plan_service.bootstrap_draft_pr",
                return_value={"number": 5, "url": "http://x/5"},
            ) as mock_bootstrap,
            patch("wade.services.plan_service.add_complexity_label"),
            patch("wade.services.plan_service.console"),
        ):
            _create_issues_from_plans(
                provider=provider,
                config=_cfg_main(),
                plan_files=[plan_file],
                repo_root=tmp_path,
            )

        assert mock_bootstrap.call_args.kwargs["base_branch"] is None

    def test_reconciled_issue_finishes_draft_pr_persistence(self, tmp_path: Path) -> None:
        """A recovered marker task follows the ordinary label and PR path."""
        plan_file = self._make_plan(tmp_path)
        marker = "<!-- wade:plan-handoff:reconciled -->"
        provider = MagicMock()
        provider.find_tasks_by_body_marker.return_value = [
            Task(id="7", title="feat: thing", body=f"Existing lightweight body\n\n{marker}")
        ]
        persisted: dict[str, str] = {}
        pending = {"PLAN.md": marker}
        save_progress = MagicMock(return_value=True)

        with (
            patch(
                "wade.services.plan_service.bootstrap_draft_pr",
                return_value={"number": 5, "url": "http://x/5"},
            ) as bootstrap,
            patch("wade.services.plan_service.add_complexity_label") as add_complexity,
            patch("wade.services.plan_service.console"),
        ):
            created, failed = _create_issues_from_plans(
                provider=provider,
                config=_cfg_main(),
                plan_files=[plan_file],
                repo_root=tmp_path,
                persisted_issues=persisted,
                pending_issue_markers=pending,
                handoff_id="completed-handoff",
                save_progress=save_progress,
            )

        assert created == ["7"]
        assert failed == []
        provider.create_task.assert_not_called()
        add_complexity.assert_called_once_with(provider, "7", plan_file.complexity)
        bootstrap.assert_called_once()
        assert bootstrap.call_args.kwargs["issue_number"] == "7"
        assert bootstrap.call_args.kwargs["refresh_existing_plan"] is True
        assert persisted == {"PLAN.md": "7"}
        assert pending == {}
        save_progress.assert_called_once()
        assert "**Full plan**: PR #5" in provider.update_task.call_args.kwargs["body"]

    def test_reconciled_issue_does_not_duplicate_an_existing_plan_link(
        self, tmp_path: Path
    ) -> None:
        """Replaying after a failed progress write reuses, rather than repeats, the link."""
        plan_file = self._make_plan(tmp_path)
        marker = "<!-- wade:plan-handoff:reconciled -->"
        provider = MagicMock()
        provider.find_tasks_by_body_marker.return_value = [
            Task(
                id="7",
                title="feat: thing",
                body=f"Existing lightweight body\n\n{marker}\n\n**Full plan**: PR #5",
            )
        ]
        persisted: dict[str, str] = {}
        pending = {"PLAN.md": marker}

        with (
            patch(
                "wade.services.plan_service.bootstrap_draft_pr",
                return_value={"number": 5, "url": "http://x/5"},
            ),
            patch("wade.services.plan_service.add_complexity_label"),
            patch("wade.services.plan_service.console"),
        ):
            created, failed = _create_issues_from_plans(
                provider=provider,
                config=_cfg_main(),
                plan_files=[plan_file],
                repo_root=tmp_path,
                persisted_issues=persisted,
                pending_issue_markers=pending,
                handoff_id="completed-handoff",
                save_progress=MagicMock(return_value=True),
            )

        assert created == ["7"]
        assert failed == []
        provider.update_task.assert_not_called()

    def test_bootstrap_failure_records_plan_as_failed(self, tmp_path: Path) -> None:
        # An unresolvable declared base makes bootstrap_draft_pr return None. The plan
        # was never persisted to a PR, so it must be recorded as failed (not created) —
        # else the caller finalizes the issue and force-removes the planning worktree,
        # discarding the plan (#376 review). The already-created lightweight issue is
        # closed so it does not orphan and a re-run does not accumulate a duplicate.
        plan_file = self._make_plan(tmp_path, base_section="\n## Base Branch\ndevelop\n")
        provider = MagicMock()
        provider.create_task.return_value = Task(id="9", title="feat: thing")

        with (
            patch("wade.services.plan_service.bootstrap_draft_pr", return_value=None),
            patch("wade.services.plan_service.add_complexity_label"),
            patch("wade.services.plan_service.console"),
        ):
            created, failed = _create_issues_from_plans(
                provider=provider,
                config=_cfg_main(),
                plan_files=[plan_file],
                repo_root=tmp_path,
            )

        assert created == []
        assert failed == [plan_file.path.name]
        provider.close_task.assert_called_once_with("9", reason=CloseReason.NOT_PLANNED)

    def test_bootstrap_failure_swallows_orphan_close_error(self, tmp_path: Path) -> None:
        # Closing the orphaned issue is best-effort: a close failure must not mask the
        # underlying bootstrap failure or crash the batch — the plan is still recorded as
        # failed so the caller preserves the planning output (#376 review).
        plan_file = self._make_plan(tmp_path, base_section="\n## Base Branch\ndevelop\n")
        provider = MagicMock()
        provider.create_task.return_value = Task(id="9", title="feat: thing")
        provider.close_task.side_effect = RuntimeError("gh down")

        with (
            patch("wade.services.plan_service.bootstrap_draft_pr", return_value=None),
            patch("wade.services.plan_service.add_complexity_label"),
            patch("wade.services.plan_service.console"),
        ):
            created, failed = _create_issues_from_plans(
                provider=provider,
                config=_cfg_main(),
                plan_files=[plan_file],
                repo_root=tmp_path,
            )

        assert created == []
        assert failed == [plan_file.path.name]


class TestBranchWorkInFlight:
    def test_active_worktree_is_in_flight(self, tmp_path: Path) -> None:
        with patch(
            "wade.git.worktree.list_worktrees",
            return_value=[Worktree(path="/wt", branch="feat/1-x")],
        ):
            assert _branch_work_in_flight(tmp_path, "feat/1-x", "main") is True

    def test_commits_past_scaffold_is_in_flight(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.worktree.list_worktrees", return_value=[]),
            patch("wade.git.branch.resolve_start_point", return_value="main"),
            patch("wade.git.branch.commits_ahead", return_value=3),
        ):
            assert _branch_work_in_flight(tmp_path, "feat/1-x", "main") is True

    def test_bare_scaffold_is_not_in_flight(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.worktree.list_worktrees", return_value=[]),
            patch("wade.git.branch.resolve_start_point", return_value="main"),
            patch("wade.git.branch.commits_ahead", return_value=1),
            patch("wade.git.branch.tip_commit_is_empty", return_value=True),
        ):
            assert _branch_work_in_flight(tmp_path, "feat/1-x", "main") is False

    def test_single_non_empty_commit_is_in_flight(self, tmp_path: Path) -> None:
        # Exactly one commit ahead but the tip touched the tree (amended scaffold / squash
        # / a PR opened outside WADE) → real work. The guard must require confirmation, in
        # lock-step with the reroot's _branch_has_real_work, or a silent retarget would
        # pollute the PR's diff with the old base's commits (#376 review).
        with (
            patch("wade.git.worktree.list_worktrees", return_value=[]),
            patch("wade.git.branch.resolve_start_point", return_value="main"),
            patch("wade.git.branch.commits_ahead", return_value=1),
            patch("wade.git.branch.tip_commit_is_empty", return_value=False),
        ):
            assert _branch_work_in_flight(tmp_path, "feat/1-x", "main") is True

    def test_unresolvable_base_fails_closed_as_in_flight(self, tmp_path: Path) -> None:
        # commits_ahead raises (base deleted upstream / narrow clone) → we cannot
        # tell whether work is in flight, so err on the safe side and treat it as
        # in-flight so the retarget requires confirmation (#376 review).
        from wade.git.repo import GitError

        with (
            patch("wade.git.worktree.list_worktrees", return_value=[]),
            patch("wade.git.branch.resolve_start_point", return_value="develop"),
            patch("wade.git.branch.commits_ahead", side_effect=GitError("bad revision")),
        ):
            assert _branch_work_in_flight(tmp_path, "feat/1-x", "develop") is True


class TestBaseRetargetGuard:
    def _issue(self) -> Task:
        return Task(id="42", title="feat: thing")

    def _plan(self, tmp_path: Path, base: str | None) -> PlanFile:
        section = f"\n## Base Branch\n{base}\n" if base else "\n"
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(f"# feat: thing\n\n## Complexity\nmedium\n{section}\n## Tasks\n- Do\n")
        return PlanFile.from_markdown(plan_path)

    def _run(self, tmp_path: Path, base: str | None, *, yolo: bool = False) -> bool:
        return _base_retarget_is_safe(
            _cfg_main(), self._issue(), self._plan(tmp_path, base), tmp_path, yolo=yolo
        )

    def test_no_open_pr_is_safe(self, tmp_path: Path) -> None:
        with patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup(found=False)):
            assert self._run(tmp_path, "develop") is True

    def test_lookup_failure_is_refused(self, tmp_path: Path) -> None:
        # A transient gh error is not "no PR" — abort rather than risk a silent retarget.
        with (
            patch(
                "wade.git.pr.get_pr_for_branch",
                return_value=PRLookup(found=False, lookup_failed=True),
            ),
            patch("wade.services.plan_service.console") as mock_console,
        ):
            assert self._run(tmp_path, "develop") is False
            mock_console.error.assert_called_once()

    def test_unchanged_base_is_safe(self, tmp_path: Path) -> None:
        with patch("wade.git.pr.get_pr_for_branch", return_value=_open_pr_lookup(base="develop")):
            assert self._run(tmp_path, "develop") is True

    def test_base_change_not_in_flight_is_safe(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.pr.get_pr_for_branch", return_value=_open_pr_lookup(base="main")),
            patch("wade.services.plan_service._branch_work_in_flight", return_value=False),
        ):
            assert self._run(tmp_path, "develop") is True

    def test_base_change_in_flight_non_tty_is_refused(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.pr.get_pr_for_branch", return_value=_open_pr_lookup(base="main")),
            patch("wade.services.plan_service._branch_work_in_flight", return_value=True),
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.is_tty.return_value = False
            assert self._run(tmp_path, "develop") is False

    def test_base_change_in_flight_yolo_is_refused(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.pr.get_pr_for_branch", return_value=_open_pr_lookup(base="main")),
            patch("wade.services.plan_service._branch_work_in_flight", return_value=True),
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.is_tty.return_value = True
            assert self._run(tmp_path, "develop", yolo=True) is False
            mock_prompts.confirm.assert_not_called()

    def test_base_change_in_flight_tty_confirm_proceeds(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.pr.get_pr_for_branch", return_value=_open_pr_lookup(base="main")),
            patch("wade.services.plan_service._branch_work_in_flight", return_value=True),
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.is_tty.return_value = True
            mock_prompts.confirm.return_value = True
            assert self._run(tmp_path, "develop") is True

    def test_base_removal_keeps_pr_base_and_proceeds(self, tmp_path: Path) -> None:
        # Plan drops the Base Branch section while the PR targets a non-main base:
        # wade never auto-reverts, so this proceeds (bootstrap won't retarget) after a warning.
        with (
            patch("wade.git.pr.get_pr_for_branch", return_value=_open_pr_lookup(base="develop")),
            patch("wade.services.plan_service._branch_work_in_flight", return_value=True),
            patch("wade.services.plan_service.console") as mock_console,
        ):
            assert self._run(tmp_path, None) is True
            mock_console.warn.assert_called_once()


class TestReconcileInflightWorktreeBase:
    """After a confirmed retarget, the in-flight worktree's pin must follow (#376 review)."""

    def _issue(self) -> Task:
        return Task(id="42", title="feat: thing")

    def _wt_entry(self, wt: Path) -> list[Worktree]:
        return [Worktree(path=str(wt), branch="feat/42-thing")]

    def test_writes_pin_for_inflight_worktree(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        with (
            patch("wade.git.branch.make_branch_name", return_value="feat/42-thing"),
            patch("wade.git.worktree.list_worktrees", return_value=self._wt_entry(wt)),
        ):
            ok = _reconcile_inflight_worktree_base(_cfg_main(), self._issue(), tmp_path, "develop")
        assert ok is True
        assert (wt / ".wade" / "base_branch").read_text().strip() == "develop"

    def test_no_worktree_is_noop(self, tmp_path: Path) -> None:
        with (
            patch("wade.git.branch.make_branch_name", return_value="feat/42-thing"),
            patch("wade.git.worktree.list_worktrees", return_value=[]),
        ):
            ok = _reconcile_inflight_worktree_base(_cfg_main(), self._issue(), tmp_path, "develop")
        assert ok is True
        assert not (tmp_path / ".wade").exists()

    def test_worktree_discovery_failure_fails_closed(self, tmp_path: Path) -> None:
        # list_worktrees raising *after* the PR is retargeted means we cannot confirm an
        # in-flight worktree's pin matches the new base — fail CLOSED (False) so the
        # caller preserves-and-aborts rather than reporting success on a possibly-stale
        # pin that would merge a resumed session into the old base (#376 review).
        with (
            patch("wade.git.branch.make_branch_name", return_value="feat/42-thing"),
            patch("wade.git.worktree.list_worktrees", side_effect=OSError("git failed")),
            patch("wade.services.plan_service.console") as mock_console,
        ):
            ok = _reconcile_inflight_worktree_base(_cfg_main(), self._issue(), tmp_path, "develop")
        assert ok is False
        mock_console.error.assert_called_once()

    def test_write_failure_returns_false(self, tmp_path: Path) -> None:
        # A read-only worktree (write raises OSError) must not be swallowed: the PR
        # is already retargeted, so the stale pin would merge into the old base.
        # Surface it (False) so the caller aborts rather than reports success (#376).
        wt = tmp_path / "wt"
        wt.mkdir()
        with (
            patch("wade.git.branch.make_branch_name", return_value="feat/42-thing"),
            patch("wade.git.worktree.list_worktrees", return_value=self._wt_entry(wt)),
            patch("wade.services.plan_service.Path.write_text", side_effect=OSError("read-only")),
            patch("wade.services.plan_service.console") as mock_console,
        ):
            ok = _reconcile_inflight_worktree_base(_cfg_main(), self._issue(), tmp_path, "develop")
        assert ok is False
        mock_console.error.assert_called_once()

    def test_retarget_to_main_clears_stale_pin(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        (wt / ".wade").mkdir(parents=True)
        (wt / ".wade" / "base_branch").write_text("develop\n")
        with (
            patch("wade.git.branch.make_branch_name", return_value="feat/42-thing"),
            patch("wade.git.worktree.list_worktrees", return_value=self._wt_entry(wt)),
        ):
            _reconcile_inflight_worktree_base(_cfg_main(), self._issue(), tmp_path, "main")
        assert not (wt / ".wade" / "base_branch").exists()

    def test_base_removal_leaves_existing_pin(self, tmp_path: Path) -> None:
        # declared_base is None (section removed) — a documented no-op; the pin stays.
        wt = tmp_path / "wt"
        (wt / ".wade").mkdir(parents=True)
        (wt / ".wade" / "base_branch").write_text("develop\n")
        with (
            patch("wade.git.branch.make_branch_name", return_value="feat/42-thing"),
            patch("wade.git.worktree.list_worktrees", return_value=self._wt_entry(wt)),
        ):
            _reconcile_inflight_worktree_base(_cfg_main(), self._issue(), tmp_path, None)
        assert (wt / ".wade" / "base_branch").read_text().strip() == "develop"


# ---------------------------------------------------------------------------
# _persist_plan_issue_ref — issue context for resumed plan sessions (#351/#391)
# ---------------------------------------------------------------------------


class TestPersistPlanIssueRef:
    def test_writes_issue_heading_at_expected_path(self, tmp_path: Path) -> None:
        from wade.models.hooks import PLAN_ISSUE_REF_FILE

        _persist_plan_issue_ref(tmp_path, Task(id="351", title="E3: session start", body=""))
        ref = tmp_path / PLAN_ISSUE_REF_FILE
        assert ref.read_text(encoding="utf-8") == "# Issue #351: E3: session start\n"

    def test_persisted_ref_round_trips_through_session_start_hook(self, tmp_path: Path) -> None:
        # The whole point: what the plan session writes is exactly what the PLAN
        # SessionStart hook parses back after a resume/compaction. Persist here,
        # read via the hook policy, and assert the issue is re-injected.
        from wade.hooks.policies import session_start_context
        from wade.models.hooks import SessionPhase

        _persist_plan_issue_ref(tmp_path, Task(id="330", title="Split me", body=""))
        payload = session_start_context(tmp_path, SessionPhase.PLAN)
        assert payload is not None
        assert "Issue #330 — Split me" in payload

    def test_write_failure_is_swallowed(self, tmp_path: Path) -> None:
        # Best-effort: a persist failure must never abort the plan session.
        with patch("wade.services.plan_service.Path.write_text", side_effect=OSError("nope")):
            _persist_plan_issue_ref(tmp_path, Task(id="1", title="x", body=""))

    def test_symlinked_wade_dir_is_rejected(self, tmp_path: Path) -> None:
        # A symlinked `.wade` must not be followed — the write would otherwise land
        # at `<link-target>/plan-issue.md`, outside the ephemeral planning worktree.
        outside = tmp_path / "outside"
        outside.mkdir()
        worktree = tmp_path / "wt"
        worktree.mkdir()
        (worktree / ".wade").symlink_to(outside, target_is_directory=True)

        _persist_plan_issue_ref(worktree, Task(id="7", title="x", body=""))

        assert not (outside / "plan-issue.md").exists()


# ---------------------------------------------------------------------------
# _with_supersede_banner — banner idempotency
# ---------------------------------------------------------------------------


class TestWithSupersedeBanner:
    def test_prepends_banner_to_body(self) -> None:
        result = _with_supersede_banner("Original content", "#1, #2")
        assert result == "> **Superseded by #1, #2**\n\nOriginal content"

    def test_replaces_existing_banner_instead_of_stacking(self) -> None:
        body = "> **Superseded by #1, #2**\n\nOriginal content"
        result = _with_supersede_banner(body, "#1, #2, #3")
        assert result.count("Superseded by") == 1
        assert "#1, #2, #3" in result
        assert "Original content" in result

    def test_empty_body_returns_banner_only(self) -> None:
        result = _with_supersede_banner("", "#1, #2")
        assert result == "> **Superseded by #1, #2**"

    def test_replaces_existing_banner_with_leading_whitespace(self) -> None:
        body = "\n> **Superseded by #1, #2**\n\nOriginal content"
        result = _with_supersede_banner(body, "#1, #2, #3")
        assert result.count("Superseded by") == 1
        assert "#1, #2, #3" in result
        assert "Original content" in result


# ---------------------------------------------------------------------------
# _supersede_issue_with_plans
# ---------------------------------------------------------------------------


class TestSupersedeIssueWithPlans:
    def _make_plan_files(self, tmp_path: Path, n: int) -> list[PlanFile]:
        files = []
        for i in range(n):
            p = tmp_path / f"PLAN-{i}.md"
            p.write_text(f"# feat: part {i}\n\n## Tasks\n- Do {i}\n")
            files.append(PlanFile.from_markdown(p))
        return files

    def test_full_success_closes_original_as_not_planned(self, tmp_path: Path) -> None:
        provider = MagicMock()
        issue = Task(id="330", title="Big feature", body="Original body")
        plan_files = self._make_plan_files(tmp_path, 3)

        with (
            patch(
                "wade.services.plan_service._create_issues_from_plans",
                return_value=(["101", "102", "103"], []),
            ),
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.confirm.return_value = True

            result = _supersede_issue_with_plans(
                provider=provider,
                config=ProjectConfig(),
                issue=issue,
                plan_files=plan_files,
                repo_root=None,
                yolo=False,
            )

        assert result == ["101", "102", "103"]

        provider.comment_on_task.assert_called_once()
        comment_body = provider.comment_on_task.call_args.args[1]
        assert "#101, #102, #103" in comment_body

        provider.update_task.assert_called_once()
        updated_body = provider.update_task.call_args.kwargs["body"]
        assert "Superseded by #101, #102, #103" in updated_body
        assert "Original body" in updated_body

        provider.close_task.assert_called_once_with("330", reason=CloseReason.NOT_PLANNED)

    def test_partial_failure_leaves_issue_open(self, tmp_path: Path) -> None:
        provider = MagicMock()
        issue = Task(id="330", title="Big feature", body="Original body")
        plan_files = self._make_plan_files(tmp_path, 3)

        with (
            patch(
                "wade.services.plan_service._create_issues_from_plans",
                return_value=(["101", "102"], ["PLAN-2.md"]),
            ),
            patch("wade.services.plan_service.console") as mock_console,
        ):
            result = _supersede_issue_with_plans(
                provider=provider,
                config=ProjectConfig(),
                issue=issue,
                plan_files=plan_files,
                repo_root=None,
                yolo=True,
            )

        assert result == ["101", "102"]
        provider.comment_on_task.assert_not_called()
        provider.update_task.assert_not_called()
        provider.close_task.assert_not_called()
        mock_console.warn.assert_called_once()
        assert "PLAN-2.md" in mock_console.warn.call_args.args[0]

    def test_yolo_skips_confirmation_prompt(self, tmp_path: Path) -> None:
        provider = MagicMock()
        issue = Task(id="330", title="Big feature", body="")
        plan_files = self._make_plan_files(tmp_path, 2)

        with (
            patch(
                "wade.services.plan_service._create_issues_from_plans",
                return_value=(["101", "102"], []),
            ),
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console"),
        ):
            _supersede_issue_with_plans(
                provider=provider,
                config=ProjectConfig(),
                issue=issue,
                plan_files=plan_files,
                repo_root=None,
                yolo=True,
            )

        mock_prompts.confirm.assert_not_called()
        provider.close_task.assert_called_once_with("330", reason=CloseReason.NOT_PLANNED)

    def test_user_declines_close_leaves_issue_open(self, tmp_path: Path) -> None:
        provider = MagicMock()
        issue = Task(id="330", title="Big feature", body="")
        plan_files = self._make_plan_files(tmp_path, 2)

        with (
            patch(
                "wade.services.plan_service._create_issues_from_plans",
                return_value=(["101", "102"], []),
            ),
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.confirm.return_value = False

            result = _supersede_issue_with_plans(
                provider=provider,
                config=ProjectConfig(),
                issue=issue,
                plan_files=plan_files,
                repo_root=None,
                yolo=False,
            )

        assert result == ["101", "102"]
        provider.close_task.assert_not_called()
        # Comment and banner are applied regardless of the close decision.
        provider.comment_on_task.assert_called_once()
        provider.update_task.assert_called_once()


# ---------------------------------------------------------------------------
# plan() — existing-issue branch: attach vs supersede
# ---------------------------------------------------------------------------


class TestPlanExistingIssueBranch:
    def test_single_plan_attaches_without_creating_an_issue(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        _, provider, _, _ = collected_harness
        with patch(
            "wade.services.plan_service._attach_plan_to_existing_issue", return_value=True
        ) as attach:
            assert plan(project_root=tmp_path, issue_id="330")
        attach.assert_called_once()
        provider.create_task.assert_not_called()

    def test_multiple_plans_supersede_only_after_validation(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        _, provider, collect, _ = collected_harness
        collect.return_value = native_result(
            bundle_text(
                ("PLAN-one.md", PLAN_TEXT),
                ("PLAN-two.md", PLAN_TEXT.replace("test plan", "second plan")),
            )
        )
        with (
            patch(
                "wade.services.plan_service._supersede_issue_with_plans", return_value=["1", "2"]
            ) as supersede,
            patch("wade.services.plan_service._finalize_issues", return_value=None) as finalize,
        ):
            assert plan(project_root=tmp_path, issue_id="330")
        supersede.assert_called_once()
        assert len(supersede.call_args.kwargs["plan_files"]) == 2
        assert finalize.call_args.kwargs["issue_numbers"] == ["1", "2"]
        provider.create_task.assert_not_called()

    def test_refused_retarget_preserves_plan(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        _, provider, _, _ = collected_harness
        with (
            patch("wade.services.plan_service._attach_plan_to_existing_issue", return_value=False),
            patch("wade.services.plan_service._preserve_generated_plans") as preserve,
        ):
            assert not plan(project_root=tmp_path, issue_id="330")
        preserve.assert_called_once()
        provider.create_task.assert_not_called()

    def test_handoff_cleanup_failure_requires_recovery(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        with patch(
            "wade.services.plan_service._cleanup_plan_dir_or_worktree", return_value=False
        ) as cleanup:
            assert not plan(project_root=tmp_path)
        cleanup.assert_called_once()


_GATE_VALID = "# feat: add retry logic\n\n## Complexity\ncomplex\n\n## Tasks\n- a\n"
_GATE_NO_COMPLEXITY = "# feat: add retry logic\n\n## Tasks\n- a\n"
_GATE_BAD_TITLE = "# add retry logic\n\n## Complexity\ncomplex\n\n## Tasks\n- a\n"


class TestSelectValidPlans:
    """Unit tests for the strict validation gate that runs on real plan dirs."""

    def _plan(self, plan_dir: Path, name: str, content: str) -> PlanFile:
        p = plan_dir / name
        p.write_text(content)
        return PlanFile.from_markdown(p)

    def test_all_valid_returns_all_without_prompt(self, tmp_path: Path) -> None:
        a = self._plan(tmp_path, "PLAN.md", _GATE_VALID)
        b = self._plan(tmp_path, "PLAN-2.md", _GATE_VALID)
        with (
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console"),
        ):
            result = _select_valid_plans(tmp_path, [a, b], yolo=False)
        assert result == [a, b]
        mock_prompts.confirm.assert_not_called()  # nothing invalid → no prompt

    def test_all_invalid_returns_empty_and_surfaces_errors(self, tmp_path: Path) -> None:
        a = self._plan(tmp_path, "PLAN.md", _GATE_NO_COMPLEXITY)
        b = self._plan(tmp_path, "PLAN-2.md", _GATE_BAD_TITLE)
        with (
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console") as mock_console,
        ):
            result = _select_valid_plans(tmp_path, [a, b], yolo=False)
        assert result == []
        mock_prompts.confirm.assert_not_called()
        assert mock_console.error.called  # errors are surfaced loudly

    def test_mixed_non_tty_requires_a_decision(self, tmp_path: Path) -> None:
        good = self._plan(tmp_path, "PLAN.md", _GATE_VALID)
        bad = self._plan(tmp_path, "PLAN-2.md", _GATE_NO_COMPLEXITY)
        with (
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.is_tty.return_value = False
            result = _select_valid_plans(tmp_path, [good, bad], yolo=False)
        assert result is None
        mock_prompts.confirm.assert_not_called()  # never hang headless

    def test_mixed_yolo_proceeds_with_valid_subset(self, tmp_path: Path) -> None:
        good = self._plan(tmp_path, "PLAN.md", _GATE_VALID)
        bad = self._plan(tmp_path, "PLAN-2.md", _GATE_BAD_TITLE)
        with (
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.is_tty.return_value = True
            result = _select_valid_plans(tmp_path, [good, bad], yolo=True)
        assert result == [good]
        mock_prompts.confirm.assert_called_once()  # subset decisions never inherit YOLO

    def test_mixed_tty_confirm_proceeds(self, tmp_path: Path) -> None:
        good = self._plan(tmp_path, "PLAN.md", _GATE_VALID)
        bad = self._plan(tmp_path, "PLAN-2.md", _GATE_NO_COMPLEXITY)
        with (
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.is_tty.return_value = True
            mock_prompts.confirm.return_value = True
            result = _select_valid_plans(tmp_path, [good, bad], yolo=False)
        assert result == [good]
        mock_prompts.confirm.assert_called_once()

    def test_mixed_tty_decline_aborts_with_none(self, tmp_path: Path) -> None:
        good = self._plan(tmp_path, "PLAN.md", _GATE_VALID)
        bad = self._plan(tmp_path, "PLAN-2.md", _GATE_NO_COMPLEXITY)
        with (
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.is_tty.return_value = True
            mock_prompts.confirm.return_value = False
            result = _select_valid_plans(tmp_path, [good, bad], yolo=False)
        assert result is None  # abort → caller creates nothing


class TestPreserveGeneratedPlans:
    """The strict-gate reject path salvages generated plans before cleanup (E2)."""

    def test_copies_plan_files_to_stable_dir_then_cleans(self, tmp_path: Path) -> None:
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()
        (plan_dir / "PLAN.md").write_text(_GATE_VALID)
        (plan_dir / "PLAN-2.md").write_text(_GATE_NO_COMPLEXITY)
        preserved_dir = tmp_path / "preserved"
        preserved_dir.mkdir()

        with (
            patch("wade.services.plan_service.tempfile.mkdtemp", return_value=str(preserved_dir)),
            patch("wade.services.plan_service._cleanup_plan_dir_or_worktree") as mock_cleanup,
            patch("wade.services.plan_service.console"),
        ):
            _preserve_generated_plans(str(plan_dir), None, None)

        # Files are salvaged to the stable dir, and the normal cleanup still runs
        # afterwards so no worktree/temp dir lingers.
        assert (preserved_dir / "PLAN.md").is_file()
        assert (preserved_dir / "PLAN-2.md").is_file()
        mock_cleanup.assert_called_once_with(str(plan_dir), None, None, None)

    def test_incomplete_native_handoff_keeps_transcript_and_native_file(
        self, tmp_path: Path
    ) -> None:
        plan_dir = tmp_path / "plans"
        (plan_dir / "native").mkdir(parents=True)
        (plan_dir / "interactive-session.json").write_text('{"completed":false}')
        (plan_dir / "terminal.log").write_text("Native session stopped before import")
        (plan_dir / "native/draft.md").write_text(_GATE_VALID)
        preserved_dir = tmp_path / "preserved"
        with (
            patch("wade.services.plan_service.tempfile.mkdtemp", return_value=str(preserved_dir)),
            patch("wade.services.plan_service._cleanup_plan_dir_or_worktree") as cleanup,
            patch("wade.services.plan_service.console"),
        ):
            _preserve_generated_plans(str(plan_dir), None, None)
        assert (
            preserved_dir / "terminal.log"
        ).read_text() == "Native session stopped before import"
        assert (preserved_dir / "native/draft.md").read_text() == _GATE_VALID
        cleanup.assert_called_once()

    def test_no_files_skips_copy_but_still_cleans(self, tmp_path: Path) -> None:
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()  # no PLAN*.md written

        with (
            patch("wade.services.plan_service.tempfile.mkdtemp") as mock_mkdtemp,
            patch("wade.services.plan_service._cleanup_plan_dir_or_worktree") as mock_cleanup,
            patch("wade.services.plan_service.console"),
        ):
            _preserve_generated_plans(str(plan_dir), None, None)

        mock_mkdtemp.assert_not_called()  # nothing to preserve
        mock_cleanup.assert_called_once_with(str(plan_dir), None, None, None)

    def test_propagates_cleanup_failure_after_preserving_files(self, tmp_path: Path) -> None:
        """A copied plan is recoverable, but an undelivered vote is still a failure."""
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()
        (plan_dir / "PLAN.md").write_text(_GATE_VALID)
        preserved_dir = tmp_path / "preserved"
        preserved_dir.mkdir()

        with (
            patch("wade.services.plan_service.tempfile.mkdtemp", return_value=str(preserved_dir)),
            patch("wade.services.plan_service._cleanup_plan_dir_or_worktree", return_value=False),
            patch("wade.services.plan_service.console"),
        ):
            result = _preserve_generated_plans(str(plan_dir), tmp_path, tmp_path)

        assert result is False
        assert (preserved_dir / "PLAN.md").is_file()

    def test_copy_failure_retains_source_and_skips_cleanup(self, tmp_path: Path) -> None:
        # A mid-copy failure must never cost the user their generated plans: the
        # temp dir may hold only a partial batch, so the original plan
        # dir/worktree is retained (cleanup skipped) and its path reported —
        # never deleted after a partial salvage.
        plan_dir = tmp_path / "plans"
        plan_dir.mkdir()
        (plan_dir / "PLAN.md").write_text(_GATE_VALID)
        (plan_dir / "PLAN-2.md").write_text(_GATE_NO_COMPLEXITY)
        preserved_dir = tmp_path / "preserved"
        preserved_dir.mkdir()

        with (
            patch("wade.services.plan_service.tempfile.mkdtemp", return_value=str(preserved_dir)),
            patch("wade.services.plan_service.shutil.copytree", side_effect=OSError("disk full")),
            patch("wade.services.plan_service._cleanup_plan_dir_or_worktree") as mock_cleanup,
            patch("wade.services.plan_service.console") as mock_console,
        ):
            _preserve_generated_plans(str(plan_dir), None, None)

        # Cleanup is skipped, so the intact originals survive in place...
        mock_cleanup.assert_not_called()
        assert (plan_dir / "PLAN.md").is_file()
        assert (plan_dir / "PLAN-2.md").is_file()
        # ...and the user is pointed at where they still live, not the temp dir.
        mock_console.hint.assert_called_once_with(f"Plan files: {plan_dir}")

    def test_inaccessible_directory_reports_unknown_count_and_retains_worktree(
        self, tmp_path: Path
    ) -> None:
        worktree = tmp_path / "plan-worktree"
        plan_dir = worktree / ".wade/plans"
        plan_dir.mkdir(parents=True)
        denied = PermissionError(13, "denied", str(plan_dir))

        with (
            patch(
                "wade.services.plan_service.list_state_files_strict",
                side_effect=StateFileAccessError(plan_dir, denied),
            ),
            patch("wade.services.plan_service._cleanup_plan_dir_or_worktree") as cleanup,
            patch("wade.services.plan_service.console") as mock_console,
        ):
            assert not _preserve_generated_plans(str(plan_dir), tmp_path, worktree, ProjectConfig())

        cleanup.assert_not_called()
        output = " ".join(
            str(call)
            for call in [
                *mock_console.info.call_args_list,
                *mock_console.warn.call_args_list,
            ]
        )
        assert "count is unknown" in output
        assert "Preserved 0" not in output
        assert worktree.is_dir()
        assert any("--recover" in str(call) for call in mock_console.hint.call_args_list)


class TestStrictValidationGateWiring:
    @pytest.mark.parametrize("issue_id", [None, "330"])
    @pytest.mark.parametrize("accepted", [False, True])
    def test_invalid_subset_requires_an_explicit_decision(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        issue_id: str | None,
        accepted: bool,
    ) -> None:
        _, provider, collect, _ = collected_harness
        collect.return_value = native_result(
            bundle_text(
                ("PLAN-good.md", PLAN_TEXT),
                ("PLAN-bad.md", "# feat: missing complexity\n"),
            )
        )
        with (
            patch("wade.services.plan_service.prompts.is_tty", return_value=True),
            patch(
                "wade.services.plan_service.confirm_ai_selection",
                return_value=("codex", None, None, PermissionMode.YOLO),
            ),
            patch("wade.services.plan_service.prompts.confirm", return_value=accepted) as confirm,
            patch(
                "wade.services.plan_service._attach_plan_to_existing_issue", return_value=True
            ) as attach,
            patch("wade.services.plan_service._finalize_issues", return_value=None),
            patch("wade.services.plan_service._preserve_generated_plans") as preserve,
        ):
            assert plan(project_root=tmp_path, issue_id=issue_id) is accepted
        confirm.assert_called_once()
        if accepted:
            if issue_id:
                attach.assert_called_once()
            else:
                assert provider.create_task.call_count == 1
            preserve.assert_not_called()
        else:
            provider.create_task.assert_not_called()
            attach.assert_not_called()
            preserve.assert_called_once()

    @pytest.mark.parametrize(
        "markdown",
        [
            "# feat: missing complexity\n",
            "not a plan",
            PLAN_TEXT + "\n# feat: another task\n",
            BUNDLE_MARKER + "\nmalformed envelope",
        ],
    )
    def test_invalid_output_never_creates_tasks(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
        markdown: str,
    ) -> None:
        _, provider, collect, _ = collected_harness
        collect.return_value = native_result(markdown)
        with patch("wade.services.plan_service._preserve_generated_plans") as preserve:
            assert not plan(project_root=tmp_path)
        provider.create_task.assert_not_called()
        preserve.assert_called_once()

    def test_noninteractive_invalid_subset_is_not_implicitly_accepted(
        self,
        collected_harness: tuple[ProjectConfig, MagicMock, MagicMock, Path],
        tmp_path: Path,
    ) -> None:
        _, provider, collect, _ = collected_harness
        collect.return_value = native_result(
            bundle_text(
                ("PLAN-good.md", PLAN_TEXT),
                ("PLAN-bad.md", "# bad plan\n"),
            )
        )
        with patch("wade.services.plan_service._preserve_generated_plans"):
            assert not plan(project_root=tmp_path, yolo=True)
        provider.create_task.assert_not_called()
