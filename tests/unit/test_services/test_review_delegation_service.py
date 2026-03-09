"""Tests for the review delegation service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
from wade.models.delegation import DelegationMode, DelegationResult
from wade.services.review_delegation_service import (
    _resolve_mode,
    review_code,
    review_plan,
)

# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


class TestResolveMode:
    def test_defaults_to_prompt(self) -> None:
        cfg = AICommandConfig()
        assert _resolve_mode(cfg) == DelegationMode.PROMPT

    def test_reads_mode_from_config(self) -> None:
        cfg = AICommandConfig(mode="headless")
        assert _resolve_mode(cfg) == DelegationMode.HEADLESS

    def test_interactive_mode(self) -> None:
        cfg = AICommandConfig(mode="interactive")
        assert _resolve_mode(cfg) == DelegationMode.INTERACTIVE

    def test_invalid_mode_defaults_to_prompt(self) -> None:
        cfg = AICommandConfig(mode="bad_value")
        assert _resolve_mode(cfg) == DelegationMode.PROMPT


# ---------------------------------------------------------------------------
# review_plan
# ---------------------------------------------------------------------------


class TestReviewPlan:
    def test_missing_plan_file(self) -> None:
        result = review_plan("/nonexistent/PLAN.md")
        assert result.success is False
        assert "not found" in result.feedback

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service._get_template")
    def test_prompt_mode_returns_plan_content(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        # Set up plan file
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# My Plan\n\nDo things.")

        # Template with placeholder
        mock_template.return_value = "Review:\n{plan_content}"

        # Config
        mock_config.return_value = ProjectConfig(
            ai=AIConfig(review_plan=AICommandConfig(mode="prompt"))
        )

        # Delegation returns success
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="Review:\n# My Plan\n\nDo things.",
            mode=DelegationMode.PROMPT,
        )

        result = review_plan(str(plan_file))
        assert result.success is True

        # Verify delegate was called with correct request
        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.PROMPT
        assert "# My Plan" in call_args.prompt

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service._get_template")
    def test_mode_override_from_arg(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Plan")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = ProjectConfig(
            ai=AIConfig(review_plan=AICommandConfig(mode="prompt"))
        )
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        review_plan(str(plan_file), mode="headless")

        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.HEADLESS


# ---------------------------------------------------------------------------
# review_code
# ---------------------------------------------------------------------------


class TestReviewCode:
    @patch("wade.services.review_delegation_service.run")
    def test_no_diff_warns(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="")
        result = review_code()
        assert result.success is True
        assert "No changes" in result.feedback

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service._get_template")
    @patch("wade.services.review_delegation_service.run")
    def test_code_review_with_diff(
        self,
        mock_run: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(stdout="diff --git a/foo.py b/foo.py\n+new line\n")
        mock_template.return_value = "Review:\n{diff_content}"
        mock_config.return_value = ProjectConfig(
            ai=AIConfig(review_code=AICommandConfig(mode="prompt"))
        )
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="LGTM",
            mode=DelegationMode.PROMPT,
        )

        result = review_code()
        assert result.success is True

        call_args = mock_delegate.call_args[0][0]
        assert "diff --git" in call_args.prompt
        assert call_args.mode == DelegationMode.PROMPT

    @patch("wade.services.review_delegation_service.run")
    def test_staged_flag_passed_to_git(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="")
        review_code(staged=True)
        cmd = mock_run.call_args[0][0]
        assert "--staged" in cmd
