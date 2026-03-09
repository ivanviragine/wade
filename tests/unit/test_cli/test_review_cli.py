"""Tests for the review CLI commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from wade.cli.main import app
from wade.models.delegation import DelegationMode, DelegationResult

runner = CliRunner()


class TestReviewPlanCli:
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service._get_template")
    def test_review_plan_success(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: MagicMock,
    ) -> None:
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Test Plan\n\nContent.")
        mock_template.return_value = "{plan_content}"

        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig

        mock_config.return_value = ProjectConfig(
            ai=AIConfig(review_plan=AICommandConfig(mode="prompt"))
        )
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="LGTM", mode=DelegationMode.PROMPT
        )

        result = runner.invoke(app, ["review", "plan", str(plan_file)])
        assert result.exit_code == 0

    def test_review_plan_missing_file(self) -> None:
        result = runner.invoke(app, ["review", "plan", "/nonexistent/PLAN.md"])
        assert result.exit_code == 1


class TestReviewImplementationCli:
    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_no_diff(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="")
        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 0

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service._get_template")
    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_with_diff(
        self,
        mock_run: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(stdout="diff --git a/f.py\n+line\n")
        mock_template.return_value = "{diff_content}"

        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig

        mock_config.return_value = ProjectConfig(
            ai=AIConfig(review_implementation=AICommandConfig(mode="prompt"))
        )
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="Clean code!", mode=DelegationMode.PROMPT
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 0

    @patch("wade.services.review_delegation_service.run")
    def test_review_implementation_staged_flag(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="")
        runner.invoke(app, ["review", "implementation", "--staged"])
        cmd = mock_run.call_args[0][0]
        assert "--staged" in cmd
