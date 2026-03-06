"""Tests for plan service — prompt rendering, file discovery, orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.models.config import AIConfig, ProjectConfig
from wade.models.task import PlanFile
from wade.services.ai_resolution import resolve_ai_tool, resolve_model
from wade.services.plan_service import (
    _finalize_issues,
    discover_plan_files,
    get_plan_prompt_template,
    plan_done,
    render_plan_prompt,
    run_ai_planning_session,
    validate_plan_dir,
    validate_plan_files,
)

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
        assert "# Goal" in rendered


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
            from wade.models.ai import AIToolID

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
        result = resolve_model(None, config, "work", tool="claude", complexity="easy")
        assert result == "claude-haiku-4-5"

    def test_complexity_maps_complex(self) -> None:
        from wade.models.config import ComplexityModelMapping

        config = ProjectConfig(
            models={"claude": ComplexityModelMapping(complex="claude-sonnet-4-6")}
        )
        result = resolve_model(None, config, "work", tool="claude", complexity="complex")
        assert result == "claude-sonnet-4-6"

    def test_complexity_no_mapping_falls_to_default(self) -> None:
        config = ProjectConfig(ai=AIConfig(default_model="claude-sonnet-4-6"))
        result = resolve_model(None, config, "work", tool="claude", complexity="easy")
        assert result == "claude-sonnet-4-6"

    def test_complexity_none_falls_to_default(self) -> None:
        config = ProjectConfig(ai=AIConfig(default_model="claude-sonnet-4-6"))
        result = resolve_model(None, config, "work", tool="claude", complexity=None)
        assert result == "claude-sonnet-4-6"

    def test_complexity_beats_default_model(self) -> None:
        """Complexity mapping must take priority over ai.default_model."""
        from wade.models.config import ComplexityModelMapping

        config = ProjectConfig(
            ai=AIConfig(default_model="claude-sonnet-4-6"),
            models={"claude": ComplexityModelMapping(easy="claude-haiku-4-5")},
        )
        result = resolve_model(None, config, "work", tool="claude", complexity="easy")
        assert result == "claude-haiku-4-5"

    def test_command_specific_beats_complexity(self) -> None:
        """Command-specific model must take priority over complexity mapping."""
        from wade.models.config import AICommandConfig, ComplexityModelMapping

        config = ProjectConfig(
            ai=AIConfig(work=AICommandConfig(model="claude-opus-4-6")),
            models={"claude": ComplexityModelMapping(easy="claude-haiku-4-5")},
        )
        result = resolve_model(None, config, "work", tool="claude", complexity="easy")
        assert result == "claude-opus-4-6"

    def test_explicit_beats_everything(self) -> None:
        from wade.models.config import AICommandConfig, ComplexityModelMapping

        config = ProjectConfig(
            ai=AIConfig(
                default_model="default-model",
                work=AICommandConfig(model="cmd-model"),
            ),
            models={"claude": ComplexityModelMapping(easy="complexity-model")},
        )
        # Omit tool= so the compatibility gate doesn't interfere;
        # this test validates fallback *priority*, not compatibility.
        result = resolve_model("explicit-model", config, "work", complexity="easy")
        assert result == "explicit-model"

    def test_incompatible_model_returns_none(self) -> None:
        """When the resolved model is incompatible with the tool, return None."""
        config = ProjectConfig(ai=AIConfig(default_model="claude-sonnet-4-6"))
        # gemini tool won't accept a claude model
        result = resolve_model(None, config, "work", tool="gemini")
        assert result is None


# ---------------------------------------------------------------------------
# Plan file discovery tests
# ---------------------------------------------------------------------------


class TestDiscoverPlanFiles:
    def test_discover_sorts_by_name(self, tmp_path: Path) -> None:
        (tmp_path / "plan-2-feature-b.md").write_text("# Feature B\n")
        (tmp_path / "plan-1-feature-a.md").write_text("# Feature A\n")
        (tmp_path / "plan-3-feature-c.md").write_text("# Feature C\n")

        files = discover_plan_files(tmp_path)
        assert len(files) == 3
        assert files[0].name == "plan-1-feature-a.md"
        assert files[1].name == "plan-2-feature-b.md"
        assert files[2].name == "plan-3-feature-c.md"

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
        (tmp_path / "plan-1.md").write_text("# Feature A\n\n## Tasks\n- Do A\n")
        (tmp_path / "plan-2.md").write_text("# Feature B\n\n## Tasks\n- Do B\n")

        valid = validate_plan_files(tmp_path)
        assert len(valid) == 2
        assert valid[0].title == "Feature A"
        assert valid[1].title == "Feature B"

    def test_validate_skips_invalid(self, tmp_path: Path) -> None:
        (tmp_path / "good.md").write_text("# Valid Plan\n\nContent\n")
        (tmp_path / "bad.md").write_text("No title heading\n")

        valid = validate_plan_files(tmp_path)
        assert len(valid) == 1
        assert valid[0].title == "Valid Plan"

    def test_validate_extracts_complexity(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text(
            "# Complex Feature\n\n## Complexity\nvery_complex\n\n## Tasks\n- Many things\n"
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
            "# My Feature\n\n## Complexity\nmedium\n\n"
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
        (tmp_path / "PLAN.md").write_text("# My Feature\n\n## Tasks\n- [ ] Do it\n")
        result = validate_plan_dir(tmp_path)
        assert result.has_errors
        assert any("Complexity" in d.message for d in result.errors)

    def test_invalid_complexity_produces_error(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text(
            "# My Feature\n\n## Complexity\nbogus_value\n\n## Tasks\n- [ ] Do it\n"
        )
        result = validate_plan_dir(tmp_path)
        assert result.has_errors
        assert any("Complexity" in d.message for d in result.errors)

    def test_missing_tasks_section_produces_warning(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text(
            "# My Feature\n\n## Complexity\nmedium\n\n## Acceptance Criteria\n- [ ] It works\n"
        )
        result = validate_plan_dir(tmp_path)
        assert not result.has_errors
        assert any("Tasks" in d.message for d in result.warnings)

    def test_missing_acceptance_criteria_produces_warning(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text(
            "# My Feature\n\n## Complexity\nmedium\n\n## Tasks\n- [ ] Do it\n"
        )
        result = validate_plan_dir(tmp_path)
        assert not result.has_errors
        assert any("Acceptance Criteria" in d.message for d in result.warnings)

    def test_multiple_files_all_validated(self, tmp_path: Path) -> None:
        content_a = (
            "# Feature A\n\n## Complexity\nmedium\n\n"
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
        (tmp_path / "my-plan.md").write_text("No title\n")
        result = validate_plan_dir(tmp_path)
        assert result.errors[0].file == "my-plan.md"


# ---------------------------------------------------------------------------
# plan_done tests
# ---------------------------------------------------------------------------


class TestPlanDone:
    def test_returns_no_errors_for_valid_plans(self, tmp_path: Path) -> None:
        content = (
            "# Feature\n\n## Complexity\nmedium\n\n"
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
            "# Feature\n\n## Complexity\nmedium\n\nNo tasks or criteria sections.\n"
        )
        result = plan_done(tmp_path)
        assert not result.has_errors
        assert result.warnings
