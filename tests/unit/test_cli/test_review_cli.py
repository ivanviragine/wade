"""Tests for the review CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from wade.cli.main import app
from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
from wade.models.delegation import DelegationMode, DelegationResult

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _review_cli_config(
    *,
    review_plan_mode: str = "prompt",
    review_plan_enabled: bool | None = True,
    review_implementation_mode: str = "prompt",
    review_implementation_enabled: bool | None = True,
    review_batch_mode: str = "prompt",
    review_batch_enabled: bool | None = True,
) -> ProjectConfig:
    """Build a review CLI config fixture independent from the repo's real config."""
    return ProjectConfig(
        ai=AIConfig(
            default_tool="claude",
            review_plan=AICommandConfig(
                mode=review_plan_mode,
                enabled=review_plan_enabled,
            ),
            review_implementation=AICommandConfig(
                mode=review_implementation_mode,
                enabled=review_implementation_enabled,
            ),
            review_batch=AICommandConfig(
                mode=review_batch_mode,
                enabled=review_batch_enabled,
            ),
        )
    )


# ---------------------------------------------------------------------------
# Plan review
# ---------------------------------------------------------------------------


class TestReviewPlanCli:
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_review_plan_prompt_mode_exits_2(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """PROMPT mode should exit 2 with a SELF-REVIEW message."""
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Test Plan\n\nContent.")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _review_cli_config(review_plan_mode="prompt")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="LGTM", mode=DelegationMode.PROMPT
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file)])
        assert result.exit_code == 2
        assert "SELF-REVIEW" in result.output
        mock_delegate.assert_called_once()
        request = mock_delegate.call_args[0][0]
        assert "# Test Plan" in request.prompt
        assert request.mode == DelegationMode.PROMPT

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_review_plan_interactive_mode_exits_0(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """INTERACTIVE mode should exit 0 with a REVIEW COMPLETE message."""
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Test Plan\n\nContent.")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _review_cli_config(review_plan_mode="interactive")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="Nice plan!", mode=DelegationMode.INTERACTIVE
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file)])
        assert result.exit_code == 0
        assert "REVIEW COMPLETE" in result.output
        mock_delegate.assert_called_once()
        request = mock_delegate.call_args[0][0]
        assert "# Test Plan" in request.prompt
        assert request.mode == DelegationMode.INTERACTIVE

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_review_plan_headless_mode_exits_0(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """HEADLESS mode should exit 0 with a REVIEW COMPLETE message."""
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Test Plan\n\nContent.")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _review_cli_config(review_plan_mode="headless")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="All good.", mode=DelegationMode.HEADLESS
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file)])
        assert result.exit_code == 0
        assert "REVIEW COMPLETE" in result.output
        mock_delegate.assert_called_once()
        request = mock_delegate.call_args[0][0]
        assert "# Test Plan" in request.prompt
        assert request.mode == DelegationMode.HEADLESS

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_review_plan_failure_exits_1(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Failed review should exit 1."""
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Test Plan\n\nContent.")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _review_cli_config(review_plan_mode="interactive")
        mock_delegate.return_value = DelegationResult(
            success=False, feedback="Error", mode=DelegationMode.INTERACTIVE
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file)])
        assert result.exit_code == 1

    @patch("wade.services.review_delegation_service.load_config")
    def test_review_plan_missing_file(self, mock_config: MagicMock) -> None:
        mock_config.return_value = _review_cli_config(review_plan_enabled=True)
        result = runner.invoke(app, ["review", "plan", "/nonexistent/PLAN.md"])
        assert result.exit_code == 1
        assert "Plan file not found" in result.output

    @patch("wade.services.review_delegation_service.review_plan")
    def test_review_plan_skipped_omits_completion_banner(
        self,
        mock_review_plan: MagicMock,
        tmp_path: Path,
    ) -> None:
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Plan\n", encoding="utf-8")
        mock_review_plan.return_value = DelegationResult(
            success=True,
            feedback="Review skipped — not enabled in .wade.yml (ai.review_plan.enabled).",
            mode=DelegationMode.PROMPT,
            skipped=True,
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file)])
        assert result.exit_code == 0
        assert "REVIEW COMPLETE" not in result.output


# ---------------------------------------------------------------------------
# Implementation review
# ---------------------------------------------------------------------------


class TestReviewImplementationCli:
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_no_diff(
        self,
        mock_run: MagicMock,
        mock_config: MagicMock,
    ) -> None:
        mock_config.return_value = _review_cli_config(review_implementation_enabled=True)
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 0
        assert "No changes to review." in result.output

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_prompt_mode_exits_2(
        self,
        mock_run: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """PROMPT mode should exit 2 with a SELF-REVIEW message."""
        mock_run.return_value = MagicMock(returncode=0, stdout="diff --git a/f.py\n+line\n")
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _review_cli_config(review_implementation_mode="prompt")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="Clean code!", mode=DelegationMode.PROMPT
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 2
        assert "SELF-REVIEW" in result.output
        mock_delegate.assert_called_once()
        request = mock_delegate.call_args[0][0]
        assert "diff --git a/f.py" in request.prompt
        assert request.mode == DelegationMode.PROMPT

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_interactive_mode_exits_0(
        self,
        mock_run: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """INTERACTIVE mode should exit 0 with REVIEW COMPLETE message."""
        mock_run.return_value = MagicMock(returncode=0, stdout="diff --git a/f.py\n+line\n")
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _review_cli_config(review_implementation_mode="interactive")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="Looks good!", mode=DelegationMode.INTERACTIVE
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 0
        assert "REVIEW COMPLETE" in result.output
        mock_delegate.assert_called_once()
        request = mock_delegate.call_args[0][0]
        assert "diff --git a/f.py" in request.prompt
        assert request.mode == DelegationMode.INTERACTIVE

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_headless_mode_exits_0(
        self,
        mock_run: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """HEADLESS mode should exit 0 with REVIEW COMPLETE message."""
        mock_run.return_value = MagicMock(returncode=0, stdout="diff --git a/f.py\n+line\n")
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _review_cli_config(review_implementation_mode="headless")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="OK", mode=DelegationMode.HEADLESS
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 0
        assert "REVIEW COMPLETE" in result.output
        mock_delegate.assert_called_once()
        request = mock_delegate.call_args[0][0]
        assert "diff --git a/f.py" in request.prompt
        assert request.mode == DelegationMode.HEADLESS

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_failure_exits_1(
        self,
        mock_run: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """Failed review should exit 1."""
        mock_run.return_value = MagicMock(returncode=0, stdout="diff --git a/f.py\n+line\n")
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _review_cli_config(review_implementation_mode="interactive")
        mock_delegate.return_value = DelegationResult(
            success=False, feedback="Error", mode=DelegationMode.INTERACTIVE
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 1

    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_staged_flag(
        self,
        mock_run: MagicMock,
        mock_config: MagicMock,
    ) -> None:
        mock_config.return_value = _review_cli_config(review_implementation_enabled=True)
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        runner.invoke(app, ["review", "implementation", "--staged"])
        cmd = mock_run.call_args[0][0]
        assert "--staged" in cmd

    @patch("wade.services.review_delegation_service.review_implementation")
    def test_review_implementation_skipped_omits_completion_banner(
        self,
        mock_review_implementation: MagicMock,
    ) -> None:
        mock_review_implementation.return_value = DelegationResult(
            success=True,
            feedback=(
                "Review skipped — not enabled in .wade.yml (ai.review_implementation.enabled)."
            ),
            mode=DelegationMode.PROMPT,
            skipped=True,
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 0
        assert "REVIEW COMPLETE" not in result.output


class TestReviewBatchCli:
    @patch("wade.services.batch_review_service.review_batch")
    def test_review_batch_success(self, mock_review_batch: MagicMock) -> None:
        mock_review_batch.return_value = DelegationResult(
            success=True,
            feedback="Batch looks coherent.",
            mode=DelegationMode.HEADLESS,
        )

        result = runner.invoke(
            app,
            [
                "review",
                "batch",
                "123",
                "--ai",
                "claude",
                "--model",
                "claude-haiku-4.5",
                "--mode",
                "headless",
                "--effort",
                "low",
            ],
        )

        assert result.exit_code == 0
        assert "BATCH REVIEW COMPLETE" in result.output
        mock_review_batch.assert_called_once_with(
            "123",
            ai_tool="claude",
            model="claude-haiku-4.5",
            mode="headless",
            effort="low",
            ai_explicit=True,
            model_explicit=True,
            effort_explicit=True,
        )

    @patch("wade.services.batch_review_service.review_batch")
    def test_review_batch_skipped_omits_completion_banner(
        self, mock_review_batch: MagicMock
    ) -> None:
        mock_review_batch.return_value = DelegationResult(
            success=True,
            feedback="Review skipped — not enabled in .wade.yml (ai.review_batch.enabled).",
            mode=DelegationMode.PROMPT,
            skipped=True,
        )

        result = runner.invoke(app, ["review", "batch", "123"])
        assert result.exit_code == 0
        assert "BATCH REVIEW COMPLETE" not in result.output


# ---------------------------------------------------------------------------
# Effort flag
# ---------------------------------------------------------------------------


class TestReviewCliEffortFlag:
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_review_plan_effort_flag(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Plan")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _review_cli_config(review_plan_mode="prompt")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file), "--effort", "low"])
        assert result.exit_code == 2
        mock_delegate.assert_called_once()
        request = mock_delegate.call_args[0][0]
        assert request.effort == "low"

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_effort_flag(
        self,
        mock_run: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="diff --git a/f.py\n+line\n")
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _review_cli_config(review_implementation_mode="prompt")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        result = runner.invoke(app, ["review", "implementation", "--effort", "high"])
        assert result.exit_code == 2
        mock_delegate.assert_called_once()
        request = mock_delegate.call_args[0][0]
        assert request.effort == "high"
