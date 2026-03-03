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
    render_plan_prompt,
    run_ai_planning_session,
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

    def test_config_fallback(self) -> None:
        from wade.models.config import AICommandConfig

        config = ProjectConfig(ai=AIConfig(plan=AICommandConfig(model="claude-sonnet-4-6")))
        result = resolve_model(None, config, "plan")
        assert result == "claude-sonnet-4-6"

    def test_no_model(self) -> None:
        config = ProjectConfig()
        result = resolve_model(None, config)
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
