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


def _plan_config(mode: str = "prompt") -> ProjectConfig:
    """Return a ProjectConfig with review_plan set to *mode*."""
    return ProjectConfig(ai=AIConfig(review_plan=AICommandConfig(mode=mode)))


def _impl_config(mode: str = "prompt") -> ProjectConfig:
    """Return a ProjectConfig with review_implementation set to *mode*."""
    return ProjectConfig(ai=AIConfig(review_implementation=AICommandConfig(mode=mode)))


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
        mock_config.return_value = _plan_config("prompt")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="LGTM", mode=DelegationMode.PROMPT
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file)])
        assert result.exit_code == 2
        assert "SELF-REVIEW" in result.output

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
        mock_config.return_value = _plan_config("interactive")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="Nice plan!", mode=DelegationMode.INTERACTIVE
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file)])
        assert result.exit_code == 0
        assert "REVIEW COMPLETE" in result.output

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
        mock_config.return_value = _plan_config("headless")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="All good.", mode=DelegationMode.HEADLESS
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file)])
        assert result.exit_code == 0
        assert "REVIEW COMPLETE" in result.output

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_review_plan_skipped_exits_0(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Skipped review (e.g. reviews disabled) should exit 0 with no status message."""
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Test Plan\n\nContent.")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _plan_config("prompt")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="", mode=DelegationMode.PROMPT, skipped=True
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file)])
        assert result.exit_code == 0
        assert "SELF-REVIEW" not in result.output
        assert "REVIEW COMPLETE" not in result.output

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
        mock_config.return_value = _plan_config("interactive")
        mock_delegate.return_value = DelegationResult(
            success=False, feedback="Error", mode=DelegationMode.INTERACTIVE
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file)])
        assert result.exit_code == 1

    def test_review_plan_missing_file(self) -> None:
        result = runner.invoke(app, ["review", "plan", "/nonexistent/PLAN.md"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Implementation review
# ---------------------------------------------------------------------------


class TestReviewImplementationCli:
    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_no_diff(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 0

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
        mock_config.return_value = _impl_config("prompt")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="Clean code!", mode=DelegationMode.PROMPT
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 2
        assert "SELF-REVIEW" in result.output

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
        mock_config.return_value = _impl_config("interactive")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="Looks good!", mode=DelegationMode.INTERACTIVE
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 0
        assert "REVIEW COMPLETE" in result.output

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
        mock_config.return_value = _impl_config("headless")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="OK", mode=DelegationMode.HEADLESS
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 0
        assert "REVIEW COMPLETE" in result.output

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_skipped_exits_0(
        self,
        mock_run: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """Skipped review should exit 0 with no status message."""
        mock_run.return_value = MagicMock(returncode=0, stdout="diff --git a/f.py\n+line\n")
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _impl_config("prompt")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="", mode=DelegationMode.PROMPT, skipped=True
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 0
        assert "SELF-REVIEW" not in result.output
        assert "REVIEW COMPLETE" not in result.output

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
        mock_config.return_value = _impl_config("interactive")
        mock_delegate.return_value = DelegationResult(
            success=False, feedback="Error", mode=DelegationMode.INTERACTIVE
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 1

    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_staged_flag(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        runner.invoke(app, ["review", "implementation", "--staged"])
        cmd = mock_run.call_args[0][0]
        assert "--staged" in cmd


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
        mock_config.return_value = _plan_config("prompt")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file), "--effort", "low"])
        assert result.exit_code == 2

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
        mock_config.return_value = _impl_config("prompt")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        result = runner.invoke(app, ["review", "implementation", "--effort", "high"])
        assert result.exit_code == 2
