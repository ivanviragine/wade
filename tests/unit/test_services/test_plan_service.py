"""Tests for plan service — prompt rendering, file discovery, orchestration."""

from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from crossby.models.ai import TokenUsage

from wade.git.pr import PRLookup, PRRef
from wade.models.config import AIConfig, PermissionMode, ProjectConfig, ProjectSettings
from wade.models.task import CloseReason, Complexity, PlanFile, Task
from wade.models.worktree import Worktree
from wade.services.ai_resolution import resolve_ai_tool, resolve_model
from wade.services.plan_service import (
    PlanDiagnostic,
    PlanDiagnosticLevel,
    PlanValidationResult,
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
from wade.services.session_composition_service import SessionCompositionError

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


class TestTranscriptWiring:
    def test_codex_prefixes_plan_command(self, tmp_path: Path) -> None:
        """Codex planning sessions should prefix prompt with /plan."""
        with (
            patch("wade.services.plan_service.render_plan_prompt", return_value="Plan this issue"),
            patch("wade.services.plan_service.AbstractAITool.get") as mock_get,
            patch("wade.services.plan_service.run_with_transcript") as mock_rwt,
        ):
            adapter = MagicMock()
            adapter.build_launch_command.return_value = ["codex", "--sandbox", "workspace-write"]
            mock_get.return_value = adapter
            mock_rwt.return_value = 0

            run_ai_planning_session(
                ai_tool="codex",
                plan_dir=str(tmp_path),
                model=None,
                transcript_path=tmp_path / ".transcript",
            )

            kwargs = adapter.build_launch_command.call_args.kwargs
            assert kwargs["initial_message"].startswith("/plan ")
            prompt_file = tmp_path / "prompt.txt"
            assert prompt_file.is_file()
            assert prompt_file.read_text().startswith("/plan ")

    def test_unknown_ai_tool_missing_binary_returns_1(self, tmp_path: Path) -> None:
        """Unknown tool should fail with code 1 when binary is missing."""
        with (
            patch(
                "wade.services.plan_service.AbstractAITool.get", side_effect=ValueError("unknown")
            ),
            patch(
                "wade.services.plan_service.subprocess.run",
                side_effect=FileNotFoundError("not found"),
            ),
        ):
            result = run_ai_planning_session(
                ai_tool="nonexistent-tool",
                plan_dir=str(tmp_path),
            )
            assert result == 1

    def test_unknown_ai_tool_passes_subprocess_exit_code(self, tmp_path: Path) -> None:
        """Unknown tool fallback should propagate subprocess exit code."""
        completed = subprocess.CompletedProcess(args=["x"], returncode=7)
        with (
            patch(
                "wade.services.plan_service.AbstractAITool.get", side_effect=ValueError("unknown")
            ),
            patch("wade.services.plan_service.subprocess.run", return_value=completed),
        ):
            result = run_ai_planning_session(
                ai_tool="some-tool",
                plan_dir=str(tmp_path),
            )
            assert result == 7

    def test_claude_no_output_file_flag(self, tmp_path: Path) -> None:
        """run_ai_planning_session must NOT add --output-file (flag doesn't exist in Claude CLI).

        Transcript capture is handled by run_with_transcript, not a CLI flag.
        """
        transcript = tmp_path / ".transcript"

        with (
            patch("wade.services.plan_service.AbstractAITool.get") as mock_get,
            patch("wade.services.plan_service.run_with_transcript") as mock_rwt,
        ):
            adapter = MagicMock()
            adapter.build_launch_command.return_value = ["claude", "--permission-mode", "plan"]
            adapter.plan_dir_args.return_value = ["--add-dir", str(tmp_path)]
            mock_get.return_value = adapter
            mock_rwt.return_value = 0

            run_ai_planning_session(
                ai_tool="claude",
                plan_dir=str(tmp_path),
                model=None,
                transcript_path=transcript,
            )

            cmd = mock_rwt.call_args[0][0]
            assert "--output-file" not in cmd

    def test_transcript_path_forwarded_to_run_with_transcript(self, tmp_path: Path) -> None:
        """run_ai_planning_session forwards transcript_path to run_with_transcript."""
        transcript = tmp_path / ".transcript"

        with (
            patch("wade.services.plan_service.AbstractAITool.get") as mock_get,
            patch("wade.services.plan_service.run_with_transcript") as mock_rwt,
        ):
            adapter = MagicMock()
            adapter.build_launch_command.return_value = ["claude", "--permission-mode", "plan"]
            adapter.plan_dir_args.return_value = []
            mock_get.return_value = adapter
            mock_rwt.return_value = 0

            run_ai_planning_session(
                ai_tool="claude",
                plan_dir=str(tmp_path),
                model=None,
                transcript_path=transcript,
            )

            assert mock_rwt.call_args[0][1] == transcript

    def test_no_transcript_path_passes_none(self, tmp_path: Path) -> None:
        """When transcript_path is None, run_with_transcript receives None."""
        with (
            patch("wade.services.plan_service.AbstractAITool.get") as mock_get,
            patch("wade.services.plan_service.run_with_transcript") as mock_rwt,
        ):
            adapter = MagicMock()
            adapter.build_launch_command.return_value = ["claude", "--permission-mode", "plan"]
            adapter.plan_dir_args.return_value = ["--add-dir", str(tmp_path)]
            mock_get.return_value = adapter
            mock_rwt.return_value = 0

            run_ai_planning_session(
                ai_tool="claude",
                plan_dir=str(tmp_path),
                model=None,
                transcript_path=None,
            )

            assert mock_rwt.call_args[0][1] is None

    def test_includes_plan_dir_args(self, tmp_path: Path) -> None:
        """run_ai_planning_session should pass plan_dir inside trusted_dirs
        to build_launch_command."""
        with (
            patch("wade.services.plan_service.AbstractAITool.get") as mock_get,
            patch("wade.services.plan_service.run_with_transcript") as mock_rwt,
        ):
            adapter = MagicMock()
            adapter.build_launch_command.return_value = ["copilot", "--model", "test"]
            mock_get.return_value = adapter
            mock_rwt.return_value = 0

            run_ai_planning_session(
                ai_tool="copilot",
                plan_dir=str(tmp_path),
            )

            call_kwargs = adapter.build_launch_command.call_args.kwargs
            assert str(tmp_path) in call_kwargs["trusted_dirs"]

    def test_antigravity_cli_planning_omits_mode_plan_and_receives_plan_context(
        self, tmp_path: Path
    ) -> None:
        """Antigravity CLI planning omits --mode plan while receiving plan prompt & trusted dirs."""
        transcript = tmp_path / ".transcript"
        with patch("wade.services.plan_service.run_with_transcript") as mock_rwt:
            mock_rwt.return_value = 0
            run_ai_planning_session(
                ai_tool="antigravity-cli",
                plan_dir=str(tmp_path),
                model=None,
                transcript_path=transcript,
            )

            cmd = mock_rwt.call_args[0][0]
            # agy must NOT receive --mode plan because native plan mode sandboxes
            # writes to its brain dir rather than the worktree
            assert "--mode" not in cmd
            assert "plan" not in cmd
            assert "--prompt-interactive" in cmd
            prompt_file = tmp_path / "prompt.txt"
            assert prompt_file.is_file()
            assert str(tmp_path) in prompt_file.read_text()

    def test_unaffected_tools_receive_plan_mode_true(self, tmp_path: Path) -> None:
        """Unaffected tools (claude, etc.) receive plan_mode=True with their native args."""
        transcript = tmp_path / ".transcript"
        with patch("wade.services.plan_service.run_with_transcript") as mock_rwt:
            mock_rwt.return_value = 0
            run_ai_planning_session(
                ai_tool="claude",
                plan_dir=str(tmp_path),
                model=None,
                transcript_path=transcript,
            )

            cmd = mock_rwt.call_args[0][0]
            assert "--permission-mode" in cmd
            assert "plan" in cmd

    def test_autonomy_mode_precedence_in_planning_session(self, tmp_path: Path) -> None:
        """PermissionMode.YOLO supersedes native plan mode as expected."""
        transcript = tmp_path / ".transcript"
        with patch("wade.services.plan_service.run_with_transcript") as mock_rwt:
            mock_rwt.return_value = 0
            # Antigravity CLI with YOLO
            run_ai_planning_session(
                ai_tool="antigravity-cli",
                plan_dir=str(tmp_path),
                model=None,
                transcript_path=transcript,
                permission_mode=PermissionMode.YOLO,
            )
            agy_cmd = mock_rwt.call_args[0][0]
            assert "--dangerously-skip-permissions" in agy_cmd
            assert "--mode" not in agy_cmd

            # Claude with YOLO
            run_ai_planning_session(
                ai_tool="claude",
                plan_dir=str(tmp_path),
                model=None,
                transcript_path=transcript,
                permission_mode=PermissionMode.YOLO,
            )
            claude_cmd = mock_rwt.call_args[0][0]
            assert "--dangerously-skip-permissions" in claude_cmd
            assert "plan" not in claude_cmd


# ---------------------------------------------------------------------------
# Model compatibility tests
# ---------------------------------------------------------------------------


class TestModelCompatibility:
    def test_incompatible_model_is_dropped(self, tmp_path: Path) -> None:
        """When the resolved model is incompatible with the tool, it must be dropped."""
        with (
            patch("wade.services.plan_service.AbstractAITool.get") as mock_get,
            patch("wade.services.plan_service.run_with_transcript") as mock_rwt,
        ):
            adapter = MagicMock()
            adapter.is_model_compatible.return_value = False
            adapter.build_launch_command.return_value = ["codex"]
            mock_get.return_value = adapter
            mock_rwt.return_value = 0

            run_ai_planning_session(
                ai_tool="codex",
                plan_dir=str(tmp_path),
                model="claude-haiku-4-5-20251001",
            )

            call_kwargs = adapter.build_launch_command.call_args.kwargs
            assert call_kwargs["model"] is None

    def test_compatible_model_is_kept(self, tmp_path: Path) -> None:
        """When the resolved model is compatible with the tool, it is passed through."""
        with (
            patch("wade.services.plan_service.AbstractAITool.get") as mock_get,
            patch("wade.services.plan_service.run_with_transcript") as mock_rwt,
        ):
            adapter = MagicMock()
            adapter.is_model_compatible.return_value = True
            adapter.build_launch_command.return_value = ["codex", "--model", "codex-mini-latest"]
            mock_get.return_value = adapter
            mock_rwt.return_value = 0

            run_ai_planning_session(
                ai_tool="codex",
                plan_dir=str(tmp_path),
                model="codex-mini-latest",
            )

            call_kwargs = adapter.build_launch_command.call_args.kwargs
            assert call_kwargs["model"] == "codex-mini-latest"

    def test_no_model_skips_compatibility_check(self, tmp_path: Path) -> None:
        """When model is None, is_model_compatible is not called."""
        with (
            patch("wade.services.plan_service.AbstractAITool.get") as mock_get,
            patch("wade.services.plan_service.run_with_transcript") as mock_rwt,
        ):
            adapter = MagicMock()
            adapter.build_launch_command.return_value = ["codex"]
            mock_get.return_value = adapter
            mock_rwt.return_value = 0

            run_ai_planning_session(
                ai_tool="codex",
                plan_dir=str(tmp_path),
                model=None,
            )

            adapter.is_model_compatible.assert_not_called()


# ---------------------------------------------------------------------------
# _finalize_issues — label failure resilience
# ---------------------------------------------------------------------------


class TestFinalizeIssues:
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
            _issue_number: str, *, before_start: Callable[[], bool] | None = None
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
    def test_plan_returns_false_when_no_ai_tool_available(self) -> None:
        """plan() should fail fast with a clear error when no AI tool is resolved."""
        with (
            patch("wade.services.plan_service.load_config", return_value=ProjectConfig()),
            patch("wade.services.plan_service.get_provider", return_value=MagicMock()),
            patch("wade.services.plan_service.resolve_ai_tool", return_value=None),
            patch("wade.services.plan_service.console") as mock_console,
        ):
            assert plan() is False
            mock_console.error.assert_called_once()

    def test_fallback_composition_failure_removes_partial_temp_bundle(self, tmp_path: Path) -> None:
        provider = MagicMock()
        fallback_dir = tmp_path / "wade-plan-fallback"
        fallback_dir.mkdir()

        def fail_composition(*args, **kwargs) -> None:
            partial = fallback_dir / ".wade" / "session"
            partial.mkdir(parents=True)
            (partial / "partial.txt").write_text("partial")
            raise SessionCompositionError("invalid custom skill")

        with (
            patch(
                "wade.services.plan_service.load_config",
                return_value=ProjectConfig(ai=AIConfig(default_tool="claude")),
            ),
            patch("wade.services.plan_service.get_provider", return_value=provider),
            patch("wade.services.plan_service.resolve_ai_tool", return_value="claude"),
            patch("wade.services.plan_service.resolve_model", return_value=None),
            patch(
                "wade.services.plan_service.confirm_ai_selection",
                return_value=("claude", None, None, PermissionMode.DEFAULT),
            ),
            patch("wade.git.repo.get_repo_root", side_effect=RuntimeError("not a repo")),
            patch("wade.services.plan_service.tempfile.mkdtemp", return_value=str(fallback_dir)),
            patch(
                "wade.services.session_composition_service.compose_session",
                side_effect=fail_composition,
            ),
            patch("wade.services.plan_service.ensure_task_label") as ensure_label,
            patch("wade.services.plan_service.run_ai_planning_session") as launch,
            patch("wade.services.plan_service.set_terminal_title"),
            patch("wade.services.plan_service.start_title_keeper"),
            patch("wade.services.plan_service.stop_title_keeper") as stop_keeper,
            patch("wade.services.plan_service.console"),
        ):
            assert plan(project_root=tmp_path) is False

        assert not fallback_dir.exists()
        ensure_label.assert_not_called()
        launch.assert_not_called()
        stop_keeper.assert_called_once()

    def test_provider_setup_failure_removes_fallback_temp_dir(self, tmp_path: Path) -> None:
        provider = MagicMock()
        fallback_dir = tmp_path / "wade-plan-fallback"
        fallback_dir.mkdir()

        with (
            patch(
                "wade.services.plan_service.load_config",
                return_value=ProjectConfig(ai=AIConfig(default_tool="claude")),
            ),
            patch("wade.services.plan_service.get_provider", return_value=provider),
            patch("wade.services.plan_service.resolve_ai_tool", return_value="claude"),
            patch("wade.services.plan_service.resolve_model", return_value=None),
            patch(
                "wade.services.plan_service.confirm_ai_selection",
                return_value=("claude", None, None, PermissionMode.DEFAULT),
            ),
            patch("wade.git.repo.get_repo_root", side_effect=RuntimeError("not a repo")),
            patch("wade.services.plan_service.tempfile.mkdtemp", return_value=str(fallback_dir)),
            patch("wade.services.session_composition_service.compose_session"),
            patch(
                "wade.services.plan_service.ensure_task_label",
                side_effect=RuntimeError("provider unavailable"),
            ),
            patch("wade.services.plan_service.run_ai_planning_session") as launch,
            patch("wade.services.plan_service.set_terminal_title"),
            patch("wade.services.plan_service.start_title_keeper"),
            patch("wade.services.plan_service.stop_title_keeper") as stop_keeper,
            patch("wade.services.plan_service.console"),
            pytest.raises(RuntimeError, match="provider unavailable"),
        ):
            plan(project_root=tmp_path)

        assert not fallback_dir.exists()
        launch.assert_not_called()
        stop_keeper.assert_called_once()

    def test_provider_setup_failure_removes_detached_planning_worktree(
        self, tmp_path: Path
    ) -> None:
        provider = MagicMock()
        worktree = tmp_path / "plan-worktree"

        with (
            patch(
                "wade.services.plan_service.load_config",
                return_value=ProjectConfig(ai=AIConfig(default_tool="claude")),
            ),
            patch("wade.services.plan_service.get_provider", return_value=provider),
            patch("wade.services.plan_service.resolve_ai_tool", return_value="claude"),
            patch("wade.services.plan_service.resolve_model", return_value=None),
            patch(
                "wade.services.plan_service.confirm_ai_selection",
                return_value=("claude", None, None, PermissionMode.DEFAULT),
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.services.plan_service.report_retained_vote_recovery"),
            patch("wade.git.worktree.create_detached_worktree", return_value=worktree),
            patch("wade.services.implementation_service.bootstrap_worktree"),
            patch("wade.services.knowledge_service.mark_throwaway_knowledge_session"),
            patch(
                "wade.services.plan_service.ensure_task_label",
                side_effect=RuntimeError("provider unavailable"),
            ),
            patch("wade.git.worktree.remove_worktree") as remove_worktree,
            patch("wade.services.plan_service.run_ai_planning_session") as launch,
            patch("wade.services.plan_service.set_terminal_title"),
            patch("wade.services.plan_service.start_title_keeper"),
            patch("wade.services.plan_service.stop_title_keeper") as stop_keeper,
            patch("wade.services.plan_service.console"),
            pytest.raises(RuntimeError, match="provider unavailable"),
        ):
            plan(project_root=tmp_path)

        remove_worktree.assert_called_once_with(tmp_path, worktree, force=True)
        launch.assert_not_called()
        stop_keeper.assert_called_once()

    def test_plan_creates_issues_from_plan_files_without_snapshot_fallback(
        self, tmp_path: Path
    ) -> None:
        """Regression: plan() should rely on plan files, not snapshot-based detection."""
        provider = MagicMock()
        provider.snapshot_task_numbers.side_effect = AssertionError(
            "snapshot_task_numbers should not be called"
        )
        adapter = MagicMock()
        adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)
        plan_file = PlanFile(
            path=tmp_path / "plan-1.md",
            title="Add deterministic tests",
            body="## Tasks\n- Add tests\n",
            sections={"tasks": "- Add tests"},
        )

        with (
            patch(
                "wade.services.plan_service.load_config",
                return_value=ProjectConfig(ai=AIConfig(default_tool="claude")),
            ),
            patch("wade.services.plan_service.get_provider", return_value=provider),
            patch("wade.services.plan_service.resolve_ai_tool", return_value="claude"),
            patch("wade.services.plan_service.resolve_model", return_value=None),
            patch(
                "wade.services.plan_service.confirm_ai_selection",
                return_value=("claude", None, None, False),
            ),
            patch("wade.services.plan_service.ensure_task_label"),
            patch("wade.services.plan_service.run_ai_planning_session", return_value=0),
            patch("wade.services.plan_service.AbstractAITool.get", return_value=adapter),
            patch(
                "wade.services.plan_service._extract_token_usage",
                return_value=TokenUsage(total_tokens=123),
            ),
            patch("wade.services.plan_service.validate_plan_files", return_value=[plan_file]),
            # Strict gate sees no error diagnostics → the plan file passes through.
            patch(
                "wade.services.plan_service.validate_plan_dir",
                return_value=PlanValidationResult(),
            ),
            patch(
                "wade.services.plan_service._create_issues_from_plans",
                return_value=(["101"], []),
            ),
            patch(
                "wade.services.plan_service._finalize_issues", return_value=None
            ) as mock_finalize,
            patch("wade.services.plan_service._cleanup_plan_dir"),
            patch("wade.services.plan_service.set_terminal_title"),
            patch("wade.services.plan_service.start_title_keeper"),
            patch("wade.services.plan_service.stop_title_keeper"),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
        ):
            assert plan(project_root=tmp_path) is True

        provider.snapshot_task_numbers.assert_not_called()
        mock_finalize.assert_called_once()

    def test_plan_antigravity_cli_fails_when_no_git_repo(self, tmp_path: Path) -> None:
        """Antigravity CLI plan requires a git planning worktree; fails fast outside git."""
        provider = MagicMock()
        with (
            patch(
                "wade.services.plan_service.load_config",
                return_value=ProjectConfig(ai=AIConfig(default_tool="antigravity-cli")),
            ),
            patch("wade.services.plan_service.get_provider", return_value=provider),
            patch("wade.services.plan_service.resolve_ai_tool", return_value="antigravity-cli"),
            patch("wade.services.plan_service.resolve_model", return_value=None),
            patch(
                "wade.services.plan_service.confirm_ai_selection",
                return_value=("antigravity-cli", None, None, PermissionMode.DEFAULT),
            ),
            patch("wade.services.plan_service.ensure_task_label"),
            patch("wade.services.plan_service.run_ai_planning_session") as mock_launch,
            patch("wade.services.plan_service.set_terminal_title"),
            patch("wade.services.plan_service.start_title_keeper"),
            patch("wade.services.plan_service.stop_title_keeper") as mock_stop_title,
            patch("wade.git.repo.get_repo_root", side_effect=Exception("Not a git repo")),
            patch("wade.services.plan_service.console") as mock_console,
        ):
            assert plan(project_root=tmp_path) is False
            mock_launch.assert_not_called()
            mock_stop_title.assert_called_once()
            mock_console.error.assert_called_once()
            assert "guarded git planning worktree" in mock_console.error.call_args[0][0]

    def test_plan_antigravity_cli_fails_and_cleans_up_when_worktree_bootstrap_fails(
        self, tmp_path: Path
    ) -> None:
        """Antigravity CLI planning fails and cleans up if planning worktree bootstrap fails."""
        provider = MagicMock()
        wt_path = tmp_path / "plan-wt"
        with (
            patch(
                "wade.services.plan_service.load_config",
                return_value=ProjectConfig(ai=AIConfig(default_tool="antigravity-cli")),
            ),
            patch("wade.services.plan_service.get_provider", return_value=provider),
            patch("wade.services.plan_service.resolve_ai_tool", return_value="antigravity-cli"),
            patch("wade.services.plan_service.resolve_model", return_value=None),
            patch(
                "wade.services.plan_service.confirm_ai_selection",
                return_value=("antigravity-cli", None, None, PermissionMode.DEFAULT),
            ),
            patch("wade.services.plan_service.ensure_task_label"),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.git.worktree.create_detached_worktree", return_value=wt_path),
            patch(
                "wade.services.implementation_service.bootstrap_worktree",
                side_effect=RuntimeError("bootstrap failed"),
            ),
            patch("wade.services.plan_service._remove_planning_worktree") as mock_remove_wt,
            patch("wade.services.plan_service.run_ai_planning_session") as mock_launch,
            patch("wade.services.plan_service.set_terminal_title"),
            patch("wade.services.plan_service.start_title_keeper"),
            patch("wade.services.plan_service.stop_title_keeper") as mock_stop_title,
            patch("wade.services.plan_service.console") as mock_console,
        ):
            assert plan(project_root=tmp_path) is False
            mock_launch.assert_not_called()
            mock_remove_wt.assert_called_once_with(tmp_path, wt_path)
            mock_stop_title.assert_called_once()
            mock_console.error.assert_called_once()
            assert "guarded git planning worktree" in mock_console.error.call_args[0][0]

    def test_plan_unaffected_tool_uses_temp_dir_fallback_when_no_git_repo(
        self, tmp_path: Path
    ) -> None:
        """Unaffected tools (e.g. claude) still use temp plan dir when outside a git repo."""
        provider = MagicMock()
        adapter = MagicMock()
        adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)
        plan_file = PlanFile(
            path=tmp_path / "plan-1.md",
            title="feat: add feature",
            body="## Complexity\neasy\n## Tasks\n- task 1\n",
            sections={"complexity": "easy", "tasks": "- task 1"},
        )

        with (
            patch(
                "wade.services.plan_service.load_config",
                return_value=ProjectConfig(ai=AIConfig(default_tool="claude")),
            ),
            patch("wade.services.plan_service.get_provider", return_value=provider),
            patch("wade.services.plan_service.resolve_ai_tool", return_value="claude"),
            patch("wade.services.plan_service.resolve_model", return_value=None),
            patch(
                "wade.services.plan_service.confirm_ai_selection",
                return_value=("claude", None, None, PermissionMode.DEFAULT),
            ),
            patch("wade.services.plan_service.ensure_task_label"),
            patch(
                "wade.services.plan_service.run_ai_planning_session", return_value=0
            ) as mock_launch,
            patch("wade.services.plan_service.AbstractAITool.get", return_value=adapter),
            patch(
                "wade.services.plan_service._extract_token_usage",
                return_value=TokenUsage(total_tokens=50),
            ),
            patch("wade.services.plan_service.validate_plan_files", return_value=[plan_file]),
            patch(
                "wade.services.plan_service.validate_plan_dir",
                return_value=PlanValidationResult(),
            ),
            patch(
                "wade.services.plan_service._create_issues_from_plans",
                return_value=(["101"], []),
            ),
            patch("wade.services.plan_service._finalize_issues", return_value=None),
            patch("wade.services.plan_service._cleanup_plan_dir"),
            patch("wade.services.plan_service.set_terminal_title"),
            patch("wade.services.plan_service.start_title_keeper"),
            patch("wade.services.plan_service.stop_title_keeper"),
            patch("wade.git.repo.get_repo_root", side_effect=Exception("Not a git repo")),
        ):
            assert plan(project_root=tmp_path) is True
            mock_launch.assert_called_once()
            launch_plan_dir = mock_launch.call_args.kwargs["plan_dir"]
            assert "wade-plan-" in launch_plan_dir


# ---------------------------------------------------------------------------
# _offer_to_implement tests
# ---------------------------------------------------------------------------


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
    def _base_patches(
        self,
        tmp_path: Path,
        provider: MagicMock,
        adapter: MagicMock,
        plan_files: list[PlanFile],
    ) -> list[contextlib.AbstractContextManager[MagicMock]]:
        return [
            patch(
                "wade.services.plan_service.load_config",
                return_value=ProjectConfig(ai=AIConfig(default_tool="claude")),
            ),
            patch("wade.services.plan_service.get_provider", return_value=provider),
            patch("wade.services.plan_service.resolve_ai_tool", return_value="claude"),
            patch("wade.services.plan_service.resolve_model", return_value=None),
            patch(
                "wade.services.plan_service.confirm_ai_selection",
                return_value=("claude", None, None, False),
            ),
            patch("wade.services.plan_service.ensure_task_label"),
            patch("wade.services.plan_service.run_ai_planning_session", return_value=0),
            patch("wade.services.plan_service.AbstractAITool.get", return_value=adapter),
            patch(
                "wade.services.plan_service._extract_token_usage",
                return_value=TokenUsage(total_tokens=123),
            ),
            patch("wade.services.plan_service.validate_plan_files", return_value=plan_files),
            # Strict gate sees no error diagnostics → all plan files pass through.
            patch(
                "wade.services.plan_service.validate_plan_dir",
                return_value=PlanValidationResult(),
            ),
            patch("wade.services.plan_service._cleanup_plan_dir_or_worktree"),
            patch("wade.services.plan_service.set_terminal_title"),
            patch("wade.services.plan_service.start_title_keeper"),
            patch("wade.services.plan_service.stop_title_keeper"),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
        ]

    def test_single_plan_file_attaches_and_issue_stays_open(self, tmp_path: Path) -> None:
        """--issue N with exactly one plan file keeps today's attach behavior."""
        provider = MagicMock()
        existing_issue = Task(id="330", title="Some bug", body="Original body")
        provider.read_task.return_value = existing_issue
        adapter = MagicMock()
        adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)

        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("# feat: thing\n\n## Tasks\n- Do it\n")
        plan_file = PlanFile.from_markdown(plan_path)

        with contextlib.ExitStack() as stack:
            for p in self._base_patches(tmp_path, provider, adapter, [plan_file]):
                stack.enter_context(p)
            mock_attach = stack.enter_context(
                patch("wade.services.plan_service._attach_plan_to_existing_issue")
            )
            mock_supersede = stack.enter_context(
                patch("wade.services.plan_service._supersede_issue_with_plans")
            )
            mock_finalize = stack.enter_context(
                patch("wade.services.plan_service._finalize_issues", return_value=None)
            )

            assert plan(project_root=tmp_path, issue_id="330") is True

        mock_attach.assert_called_once()
        assert mock_attach.call_args.kwargs["plan_file"] is plan_file
        assert mock_attach.call_args.kwargs["issue"] is existing_issue
        mock_supersede.assert_not_called()

        mock_finalize.assert_called_once()
        assert mock_finalize.call_args.kwargs["issue_numbers"] == ["330"]

    def test_multi_plan_files_supersede_and_finalize_only_new_issues(self, tmp_path: Path) -> None:
        """--issue N with 2+ plan files supersedes #N; only new issues are finalized."""
        provider = MagicMock()
        existing_issue = Task(id="330", title="Split me", body="Original body")
        provider.read_task.return_value = existing_issue
        adapter = MagicMock()
        adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)

        plan_files = []
        for i in range(2):
            p = tmp_path / f"PLAN-{i}.md"
            p.write_text(f"# feat: part {i}\n\n## Tasks\n- Do {i}\n")
            plan_files.append(PlanFile.from_markdown(p))

        with contextlib.ExitStack() as stack:
            for p in self._base_patches(tmp_path, provider, adapter, plan_files):
                stack.enter_context(p)
            mock_attach = stack.enter_context(
                patch("wade.services.plan_service._attach_plan_to_existing_issue")
            )
            mock_supersede = stack.enter_context(
                patch(
                    "wade.services.plan_service._supersede_issue_with_plans",
                    return_value=["101", "102"],
                )
            )
            mock_finalize = stack.enter_context(
                patch("wade.services.plan_service._finalize_issues", return_value=None)
            )

            assert plan(project_root=tmp_path, issue_id="330") is True

        mock_attach.assert_not_called()
        mock_supersede.assert_called_once()
        assert mock_supersede.call_args.kwargs["issue"] is existing_issue
        assert mock_supersede.call_args.kwargs["plan_files"] == plan_files

        mock_finalize.assert_called_once()
        assert mock_finalize.call_args.kwargs["issue_numbers"] == ["101", "102"]

    def test_handoff_cleanup_failure_makes_plan_fail_for_retry(self, tmp_path: Path) -> None:
        """A retained staged vote must never be hidden behind a successful plan exit."""
        provider = MagicMock()
        existing_issue = Task(id="330", title="Some bug", body="Original body")
        provider.read_task.return_value = existing_issue
        adapter = MagicMock()
        adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)

        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("# feat: thing\n\n## Tasks\n- Do it\n")
        plan_file = PlanFile.from_markdown(plan_path)

        with contextlib.ExitStack() as stack:
            for p in self._base_patches(tmp_path, provider, adapter, [plan_file]):
                stack.enter_context(p)
            stack.enter_context(patch("wade.services.plan_service._attach_plan_to_existing_issue"))
            stack.enter_context(
                patch("wade.services.plan_service._finalize_issues", return_value=None)
            )
            cleanup = stack.enter_context(
                patch(
                    "wade.services.plan_service._cleanup_plan_dir_or_worktree",
                    return_value=False,
                )
            )

            assert plan(project_root=tmp_path, issue_id="330") is False

        cleanup.assert_called_once()

    def test_supersede_with_no_created_issues_skips_finalize(self, tmp_path: Path) -> None:
        """If supersede created nothing at all, plan() must not call _finalize_issues."""
        provider = MagicMock()
        existing_issue = Task(id="330", title="Split me", body="Original body")
        provider.read_task.return_value = existing_issue
        adapter = MagicMock()
        adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)

        plan_files = []
        for i in range(2):
            p = tmp_path / f"PLAN-{i}.md"
            p.write_text(f"# feat: part {i}\n\n## Tasks\n- Do {i}\n")
            plan_files.append(PlanFile.from_markdown(p))

        with contextlib.ExitStack() as stack:
            for p in self._base_patches(tmp_path, provider, adapter, plan_files):
                stack.enter_context(p)
            stack.enter_context(
                patch("wade.services.plan_service._supersede_issue_with_plans", return_value=[])
            )
            mock_finalize = stack.enter_context(
                patch("wade.services.plan_service._finalize_issues")
            )

            assert plan(project_root=tmp_path, issue_id="330") is False

        mock_finalize.assert_not_called()


# ---------------------------------------------------------------------------
# _select_valid_plans — the strict gate before issue creation (E2)
# ---------------------------------------------------------------------------

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

    def test_mixed_non_tty_proceeds_with_valid_subset(self, tmp_path: Path) -> None:
        good = self._plan(tmp_path, "PLAN.md", _GATE_VALID)
        bad = self._plan(tmp_path, "PLAN-2.md", _GATE_NO_COMPLEXITY)
        with (
            patch("wade.services.plan_service.prompts") as mock_prompts,
            patch("wade.services.plan_service.console"),
        ):
            mock_prompts.is_tty.return_value = False
            result = _select_valid_plans(tmp_path, [good, bad], yolo=False)
        assert result == [good]
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
        mock_prompts.confirm.assert_not_called()  # yolo skips the prompt

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
            patch("wade.services.plan_service.shutil.copy2", side_effect=OSError("disk full")),
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


class TestStrictValidationGateWiring:
    """plan() wires the strict gate at both issue-creation call sites (E2)."""

    def _adapter(self) -> MagicMock:
        adapter = MagicMock()
        adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)
        return adapter

    def _valid(self, tmp_path: Path, name: str = "PLAN.md") -> PlanFile:
        return PlanFile(
            path=tmp_path / name,
            title="feat: good",
            complexity=Complexity.COMPLEX,
            body="body",
            sections={},
        )

    def _invalid(self, tmp_path: Path, name: str = "PLAN-2.md") -> PlanFile:
        # Title-parseable (survives validate_plan_files) but strict-invalid.
        return PlanFile(
            path=tmp_path / name,
            title="bad title without prefix",
            complexity=None,
            body="body",
            sections={},
        )

    def _errors_for(self, *names: str) -> PlanValidationResult:
        return PlanValidationResult(
            diagnostics=[
                PlanDiagnostic(
                    file=n,
                    level=PlanDiagnosticLevel.ERROR,
                    message="Missing or invalid '## Complexity' section.",
                )
                for n in names
            ]
        )

    def _base_patches(
        self,
        tmp_path: Path,
        provider: MagicMock,
        adapter: MagicMock,
        plan_files: list[PlanFile],
        validation: PlanValidationResult,
    ) -> list[contextlib.AbstractContextManager[MagicMock]]:
        return [
            patch(
                "wade.services.plan_service.load_config",
                return_value=ProjectConfig(ai=AIConfig(default_tool="claude")),
            ),
            patch("wade.services.plan_service.get_provider", return_value=provider),
            patch("wade.services.plan_service.resolve_ai_tool", return_value="claude"),
            patch("wade.services.plan_service.resolve_model", return_value=None),
            patch(
                "wade.services.plan_service.confirm_ai_selection",
                return_value=("claude", None, None, False),
            ),
            patch("wade.services.plan_service.ensure_task_label"),
            patch("wade.services.plan_service.run_ai_planning_session", return_value=0),
            patch("wade.services.plan_service.AbstractAITool.get", return_value=adapter),
            patch(
                "wade.services.plan_service._extract_token_usage",
                return_value=TokenUsage(total_tokens=123),
            ),
            patch("wade.services.plan_service.validate_plan_files", return_value=plan_files),
            patch("wade.services.plan_service.validate_plan_dir", return_value=validation),
            patch("wade.services.plan_service._cleanup_plan_dir_or_worktree"),
            patch("wade.services.plan_service.set_terminal_title"),
            patch("wade.services.plan_service.start_title_keeper"),
            patch("wade.services.plan_service.stop_title_keeper"),
            patch("wade.services.plan_service.console"),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
        ]

    def test_new_issue_invalid_plan_skipped_only_valid_created(self, tmp_path: Path) -> None:
        provider = MagicMock()
        adapter = self._adapter()
        good = self._valid(tmp_path)
        bad = self._invalid(tmp_path)
        validation = self._errors_for("PLAN-2.md")

        with contextlib.ExitStack() as stack:
            for p in self._base_patches(tmp_path, provider, adapter, [good, bad], validation):
                stack.enter_context(p)
            mock_prompts = stack.enter_context(patch("wade.services.plan_service.prompts"))
            mock_prompts.is_tty.return_value = False
            create = stack.enter_context(
                patch(
                    "wade.services.plan_service._create_issues_from_plans",
                    return_value=(["101"], []),
                )
            )
            stack.enter_context(
                patch("wade.services.plan_service._finalize_issues", return_value=None)
            )

            assert plan(project_root=tmp_path) is True

        create.assert_called_once()
        assert create.call_args.kwargs["plan_files"] == [good]  # invalid dropped

    def test_new_issue_all_invalid_creates_nothing(self, tmp_path: Path) -> None:
        provider = MagicMock()
        adapter = self._adapter()
        bad = self._invalid(tmp_path, "PLAN.md")
        validation = self._errors_for("PLAN.md")

        with contextlib.ExitStack() as stack:
            for p in self._base_patches(tmp_path, provider, adapter, [bad], validation):
                stack.enter_context(p)
            mock_prompts = stack.enter_context(patch("wade.services.plan_service.prompts"))
            mock_prompts.is_tty.return_value = False
            create = stack.enter_context(
                patch("wade.services.plan_service._create_issues_from_plans")
            )
            preserve = stack.enter_context(
                patch("wade.services.plan_service._preserve_generated_plans")
            )

            assert plan(project_root=tmp_path) is False

        create.assert_not_called()
        preserve.assert_called_once()  # generated plans salvaged, not discarded

    def test_new_issue_mixed_abort_creates_nothing(self, tmp_path: Path) -> None:
        provider = MagicMock()
        adapter = self._adapter()
        good = self._valid(tmp_path)
        bad = self._invalid(tmp_path)
        validation = self._errors_for("PLAN-2.md")

        with contextlib.ExitStack() as stack:
            for p in self._base_patches(tmp_path, provider, adapter, [good, bad], validation):
                stack.enter_context(p)
            mock_prompts = stack.enter_context(patch("wade.services.plan_service.prompts"))
            mock_prompts.is_tty.return_value = True
            mock_prompts.confirm.return_value = False  # user declines the partial run
            create = stack.enter_context(
                patch("wade.services.plan_service._create_issues_from_plans")
            )
            preserve = stack.enter_context(
                patch("wade.services.plan_service._preserve_generated_plans")
            )

            assert plan(project_root=tmp_path) is False

        create.assert_not_called()
        preserve.assert_called_once()  # generated plans salvaged, not discarded

    def test_new_issue_all_valid_passes(self, tmp_path: Path) -> None:
        provider = MagicMock()
        adapter = self._adapter()
        good = self._valid(tmp_path)
        clean = PlanValidationResult()

        with contextlib.ExitStack() as stack:
            for p in self._base_patches(tmp_path, provider, adapter, [good], clean):
                stack.enter_context(p)
            mock_prompts = stack.enter_context(patch("wade.services.plan_service.prompts"))
            mock_prompts.is_tty.return_value = True
            create = stack.enter_context(
                patch(
                    "wade.services.plan_service._create_issues_from_plans",
                    return_value=(["101"], []),
                )
            )
            stack.enter_context(
                patch("wade.services.plan_service._finalize_issues", return_value=None)
            )

            assert plan(project_root=tmp_path) is True

        create.assert_called_once()
        assert create.call_args.kwargs["plan_files"] == [good]
        mock_prompts.confirm.assert_not_called()  # nothing invalid → no prompt

    def test_existing_issue_path_filters_invalid(self, tmp_path: Path) -> None:
        provider = MagicMock()
        existing = Task(id="330", title="Some bug", body="Original")
        provider.read_task.return_value = existing
        adapter = self._adapter()
        good = self._valid(tmp_path)
        bad = self._invalid(tmp_path)
        validation = self._errors_for("PLAN-2.md")

        with contextlib.ExitStack() as stack:
            for p in self._base_patches(tmp_path, provider, adapter, [good, bad], validation):
                stack.enter_context(p)
            mock_prompts = stack.enter_context(patch("wade.services.plan_service.prompts"))
            mock_prompts.is_tty.return_value = False
            mock_attach = stack.enter_context(
                patch("wade.services.plan_service._attach_plan_to_existing_issue")
            )
            mock_supersede = stack.enter_context(
                patch("wade.services.plan_service._supersede_issue_with_plans")
            )
            stack.enter_context(
                patch("wade.services.plan_service._finalize_issues", return_value=None)
            )

            assert plan(project_root=tmp_path, issue_id="330") is True

        # One valid file remains → single-plan attach, not supersede.
        mock_attach.assert_called_once()
        assert mock_attach.call_args.kwargs["plan_file"] is good
        mock_supersede.assert_not_called()

    def test_existing_issue_refused_retarget_preserves_plan(self, tmp_path: Path) -> None:
        # The plan is valid but attaching it would retarget an in-flight PR, which the
        # guard refuses. plan() must salvage the freshly generated plan and abort —
        # never finalize the issue against the stale PR and force-remove the worktree,
        # discarding the replacement plan (#376).
        provider = MagicMock()
        existing = Task(id="330", title="Some bug", body="Original")
        provider.read_task.return_value = existing
        adapter = self._adapter()
        good = self._valid(tmp_path)
        validation = PlanValidationResult(diagnostics=[])

        with contextlib.ExitStack() as stack:
            for p in self._base_patches(tmp_path, provider, adapter, [good], validation):
                stack.enter_context(p)
            mock_prompts = stack.enter_context(patch("wade.services.plan_service.prompts"))
            mock_prompts.is_tty.return_value = False
            # Guard refuses the in-flight retarget.
            mock_attach = stack.enter_context(
                patch(
                    "wade.services.plan_service._attach_plan_to_existing_issue",
                    return_value=False,
                )
            )
            mock_finalize = stack.enter_context(
                patch("wade.services.plan_service._finalize_issues", return_value=None)
            )
            preserve = stack.enter_context(
                patch("wade.services.plan_service._preserve_generated_plans")
            )

            assert plan(project_root=tmp_path, issue_id="330") is False

        mock_attach.assert_called_once()
        assert mock_attach.call_args.kwargs["yolo"] is False  # resolved yolo forwarded
        mock_finalize.assert_not_called()  # aborted before finalization
        preserve.assert_called_once()  # replacement plan salvaged, not discarded

    def test_existing_issue_all_invalid_mutates_nothing(self, tmp_path: Path) -> None:
        provider = MagicMock()
        existing = Task(id="330", title="Some bug", body="Original")
        provider.read_task.return_value = existing
        adapter = self._adapter()
        bad = self._invalid(tmp_path, "PLAN.md")
        validation = self._errors_for("PLAN.md")

        with contextlib.ExitStack() as stack:
            for p in self._base_patches(tmp_path, provider, adapter, [bad], validation):
                stack.enter_context(p)
            mock_prompts = stack.enter_context(patch("wade.services.plan_service.prompts"))
            mock_prompts.is_tty.return_value = False
            # Override the base console patch so we can inspect the messages.
            mock_console = stack.enter_context(patch("wade.services.plan_service.console"))
            mock_attach = stack.enter_context(
                patch("wade.services.plan_service._attach_plan_to_existing_issue")
            )
            mock_supersede = stack.enter_context(
                patch("wade.services.plan_service._supersede_issue_with_plans")
            )
            preserve = stack.enter_context(
                patch("wade.services.plan_service._preserve_generated_plans")
            )

            assert plan(project_root=tmp_path, issue_id="330") is False

        mock_attach.assert_not_called()
        mock_supersede.assert_not_called()
        preserve.assert_called_once()  # generated plans salvaged, not discarded
        # Files WERE produced (just invalid), so the misleading "No plan files
        # found" message must not fire — the helper already reported the reason.
        warn_msgs = " ".join(str(c.args[0]) for c in mock_console.warn.call_args_list if c.args)
        assert "No plan files found" not in warn_msgs
