"""Tests for the review delegation service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.models.ai import EffortLevel
from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
from wade.models.delegation import DelegationMode, DelegationResult
from wade.services.review_delegation_service import (
    _run_review_delegation,
    review_implementation,
    review_plan,
)


def _review_config(
    *,
    review_plan_mode: str = "prompt",
    review_plan_enabled: bool | None = True,
    review_implementation_mode: str = "prompt",
    review_implementation_enabled: bool | None = True,
) -> ProjectConfig:
    """Build a review-capable project config without relying on repo-local config."""
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
        )
    )


# ---------------------------------------------------------------------------
# review_plan
# ---------------------------------------------------------------------------


class TestReviewPlan:
    @patch("wade.services.review_delegation_service.load_config")
    def test_missing_plan_file(self, mock_config: MagicMock) -> None:
        mock_config.return_value = _review_config(review_plan_enabled=True)
        result = review_plan("/nonexistent/PLAN.md")
        assert result.success is False
        assert "not found" in result.feedback

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
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
        mock_config.return_value = _review_config(review_plan_enabled=True)

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
    @patch("wade.services.review_delegation_service.load_prompt_template")
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
        mock_config.return_value = _review_config(review_plan_enabled=True)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        review_plan(str(plan_file), mode="headless")

        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.HEADLESS

    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_invalid_mode_returns_error(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Plan")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _review_config(review_plan_enabled=True)

        result = review_plan(str(plan_file), mode="bad_value")
        assert result.success is False
        assert "Invalid delegation mode" in result.feedback
        assert result.exit_code == 1

    @patch("wade.services.review_delegation_service.load_config")
    def test_enabled_false_skips_review(
        self,
        mock_config: MagicMock,
    ) -> None:
        """Disabled review skips before preflight — even if plan file is missing."""
        mock_config.return_value = _review_config(review_plan_enabled=False)

        result = review_plan("/nonexistent/PLAN.md")
        assert result.success is True
        assert result.skipped is True
        assert "skipped" in result.feedback.lower()

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_enabled_none_does_not_skip(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Legacy configs without 'enabled' key should NOT skip reviews."""
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Plan")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _review_config(review_plan_enabled=None)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        result = review_plan(str(plan_file))
        assert result.success is True
        mock_delegate.assert_called_once()


# ---------------------------------------------------------------------------
# review_implementation
# ---------------------------------------------------------------------------


class TestReviewCode:
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.run")
    def test_no_diff_warns(self, mock_run: MagicMock, mock_config: MagicMock) -> None:
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = review_implementation()
        assert result.success is True
        assert "No changes" in result.feedback

    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.run")
    def test_git_diff_failure_returns_error(
        self, mock_run: MagicMock, mock_config: MagicMock
    ) -> None:
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_run.return_value = MagicMock(
            returncode=128, stdout="", stderr="fatal: not a git repository"
        )
        result = review_implementation()
        assert result.success is False
        assert "git diff failed" in result.feedback
        assert result.exit_code == 128

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service.run")
    def test_code_review_with_diff(
        self,
        mock_run: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="diff --git a/foo.py b/foo.py\n+new line\n"
        )
        mock_template.return_value = "Review:\n{diff_content}"
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="LGTM",
            mode=DelegationMode.PROMPT,
        )

        result = review_implementation()
        assert result.success is True

        call_args = mock_delegate.call_args[0][0]
        assert "diff --git" in call_args.prompt
        assert call_args.mode == DelegationMode.PROMPT

    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.run")
    def test_staged_flag_passed_to_git(self, mock_run: MagicMock, mock_config: MagicMock) -> None:
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        review_implementation(staged=True)
        cmd = mock_run.call_args[0][0]
        assert "--staged" in cmd

    @patch("wade.services.review_delegation_service.load_config")
    def test_enabled_false_skips_before_git_diff(
        self,
        mock_config: MagicMock,
    ) -> None:
        """Disabled review skips before git diff — no subprocess needed."""
        mock_config.return_value = _review_config(review_implementation_enabled=False)

        result = review_implementation()
        assert result.success is True
        assert result.skipped is True
        assert "skipped" in result.feedback.lower()

    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service.run")
    def test_invalid_mode_returns_error(
        self,
        mock_run: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="diff --git a/f.py\n+line\n")
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _review_config(review_implementation_enabled=True)

        result = review_implementation(mode="bad_value")
        assert result.success is False
        assert "Invalid delegation mode" in result.feedback
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# _run_review_delegation effort + confirm tests
# ---------------------------------------------------------------------------


class TestRunReviewDelegationEffort:
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.confirm_ai_selection")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_effort_passed_to_delegation(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """Effort from resolve_effort should be passed through to delegation request."""
        mock_config.return_value = _review_config(review_plan_mode="headless")
        mock_tool.return_value = "claude"
        mock_model.return_value = None
        mock_effort.return_value = EffortLevel.LOW
        mock_confirm.return_value = ("claude", None, EffortLevel.LOW, False)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        _run_review_delegation("test prompt", "review_plan")

        call_args = mock_delegate.call_args[0][0]
        assert call_args.effort == "low"

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.confirm_ai_selection")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_prompt_mode_skips_confirm(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """In prompt mode, confirm_ai_selection should be skipped."""
        mock_config.return_value = _review_config(review_plan_mode="prompt")
        mock_tool.return_value = "claude"
        mock_model.return_value = None
        mock_effort.return_value = None
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        _run_review_delegation("test prompt", "review_plan")

        mock_confirm.assert_not_called()

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.confirm_ai_selection")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_effort_none_when_not_effort_level(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """When resolve_effort returns None, effort in request should be None."""
        mock_config.return_value = _review_config(review_plan_mode="headless")
        mock_tool.return_value = "claude"
        mock_model.return_value = None
        mock_effort.return_value = None
        mock_confirm.return_value = ("claude", None, None, False)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        _run_review_delegation("test prompt", "review_plan")

        call_args = mock_delegate.call_args[0][0]
        assert call_args.effort is None
