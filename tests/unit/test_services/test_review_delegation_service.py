"""Tests for the review delegation service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.models.ai import EffortLevel
from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
from wade.models.delegation import DelegationMode, DelegationResult
from wade.services.review_delegation_service import (
    _committed_diff_fallback,
    _run_review_delegation,
    review_implementation,
    review_plan,
)


def _review_config(
    *,
    review_plan_mode: str = "prompt",
    review_plan_enabled: bool | None = True,
    review_plan_timeout: int | None = None,
    review_implementation_mode: str = "prompt",
    review_implementation_enabled: bool | None = True,
    review_implementation_timeout: int | None = None,
    default_tool: str | None = "claude",
) -> ProjectConfig:
    """Build a review-capable project config without relying on repo-local config."""
    return ProjectConfig(
        ai=AIConfig(
            default_tool=default_tool,
            review_plan=AICommandConfig(
                mode=review_plan_mode,
                enabled=review_plan_enabled,
                timeout=review_plan_timeout,
            ),
            review_implementation=AICommandConfig(
                mode=review_implementation_mode,
                enabled=review_implementation_enabled,
                timeout=review_implementation_timeout,
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

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_prompt_mode_works_without_ai_tool_config(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Prompt-mode plan review should not depend on any AI tool being configured."""
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Plan")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _review_config(review_plan_enabled=True, default_tool=None)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        result = review_plan(str(plan_file))
        assert result.success is True

        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.PROMPT
        assert call_args.ai_tool is None

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_headless_timeout_is_forwarded(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Plan")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _review_config(
            review_plan_enabled=True,
            review_plan_mode="headless",
            review_plan_timeout=300,
        )
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        result = review_plan(str(plan_file))
        assert result.success is True

        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.HEADLESS
        assert call_args.timeout == 300


# ---------------------------------------------------------------------------
# review_implementation
# ---------------------------------------------------------------------------


class TestReviewCode:
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service._committed_diff_fallback")
    @patch("wade.services.review_delegation_service.run")
    def test_no_diff_warns(
        self,
        mock_run: MagicMock,
        mock_fallback: MagicMock,
        mock_config: MagicMock,
    ) -> None:
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        mock_fallback.return_value = ""
        result = review_implementation()
        assert result.success is True
        assert "No changes" in result.feedback
        mock_fallback.assert_called_once_with()

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

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service.run")
    def test_prompt_mode_works_without_ai_tool_config(
        self,
        mock_run: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """Prompt-mode implementation review should not require an AI tool config."""
        mock_run.return_value = MagicMock(returncode=0, stdout="diff --git a/f.py\n+line\n")
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _review_config(
            review_implementation_enabled=True,
            default_tool=None,
        )
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        result = review_implementation()
        assert result.success is True

        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.PROMPT
        assert call_args.ai_tool is None


# ---------------------------------------------------------------------------
# Default mode per command
# ---------------------------------------------------------------------------


class TestDefaultModePerCommand:
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.confirm_ai_selection")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_review_batch_defaults_to_interactive(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """review_batch with no mode configured resolves to interactive."""
        mock_config.return_value = ProjectConfig(
            ai=AIConfig(review_batch=AICommandConfig(enabled=True))
        )
        mock_tool.return_value = "claude"
        mock_model.return_value = None
        mock_effort.return_value = None
        mock_confirm.return_value = ("claude", None, None, False)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.INTERACTIVE
        )

        _run_review_delegation("prompt text", "review_batch")

        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.INTERACTIVE

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_review_plan_defaults_to_prompt(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """review_plan with no mode configured still resolves to prompt."""
        mock_config.return_value = ProjectConfig(
            ai=AIConfig(review_plan=AICommandConfig(enabled=True))
        )
        mock_tool.return_value = None
        mock_model.return_value = None
        mock_effort.return_value = None
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        _run_review_delegation("prompt text", "review_plan")

        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.PROMPT


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
        mock_tool.assert_not_called()
        mock_model.assert_not_called()
        mock_effort.assert_not_called()

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


# ---------------------------------------------------------------------------
# _committed_diff_fallback
# ---------------------------------------------------------------------------


class TestCommittedDiffFallback:
    @patch("wade.services.review_delegation_service.git_repo.diff_between")
    @patch("wade.services.review_delegation_service.git_repo.detect_main_branch")
    @patch("wade.services.review_delegation_service.git_repo.get_current_branch")
    @patch("wade.services.review_delegation_service.git_repo.get_repo_root")
    @patch("wade.services.review_delegation_service.load_config")
    def test_returns_branch_diff_when_not_on_main(
        self,
        mock_config: MagicMock,
        mock_root: MagicMock,
        mock_branch: MagicMock,
        mock_detect: MagicMock,
        mock_diff: MagicMock,
    ) -> None:
        mock_config.return_value = ProjectConfig()
        mock_root.return_value = Path("/repo")
        mock_branch.return_value = "feat/42-my-feature"
        mock_detect.return_value = "main"
        mock_diff.return_value = "diff --git a/f.py b/f.py\n+new line\n"

        result = _committed_diff_fallback()

        assert "diff --git" in result
        mock_diff.assert_called_once_with(Path("/repo"), "main", "HEAD")

    @patch("wade.services.review_delegation_service.git_repo.get_current_branch")
    @patch("wade.services.review_delegation_service.git_repo.get_repo_root")
    @patch("wade.services.review_delegation_service.load_config")
    def test_returns_empty_when_on_main_branch(
        self,
        mock_config: MagicMock,
        mock_root: MagicMock,
        mock_branch: MagicMock,
    ) -> None:
        from wade.models.config import ProjectSettings

        mock_config.return_value = ProjectConfig(project=ProjectSettings(main_branch="main"))
        mock_root.return_value = Path("/repo")
        mock_branch.return_value = "main"

        result = _committed_diff_fallback()

        assert result == ""

    @patch("wade.services.review_delegation_service.git_repo.get_repo_root")
    @patch("wade.services.review_delegation_service.load_config")
    def test_returns_empty_on_git_error(
        self,
        mock_config: MagicMock,
        mock_root: MagicMock,
    ) -> None:
        from wade.git.repo import GitError

        mock_config.return_value = ProjectConfig()
        mock_root.side_effect = GitError("not a git repo")

        result = _committed_diff_fallback()

        assert result == ""

    @patch("wade.services.review_delegation_service.git_repo.diff_between")
    @patch("wade.services.review_delegation_service.git_repo.get_current_branch")
    @patch("wade.services.review_delegation_service.git_repo.get_repo_root")
    @patch("wade.services.review_delegation_service.load_config")
    def test_uses_config_main_branch_when_set(
        self,
        mock_config: MagicMock,
        mock_root: MagicMock,
        mock_branch: MagicMock,
        mock_diff: MagicMock,
    ) -> None:
        from wade.models.config import ProjectSettings

        mock_config.return_value = ProjectConfig(project=ProjectSettings(main_branch="develop"))
        mock_root.return_value = Path("/repo")
        mock_branch.return_value = "feat/42-my-feature"
        mock_diff.return_value = "diff content"

        result = _committed_diff_fallback()

        assert result == "diff content"
        mock_diff.assert_called_once_with(Path("/repo"), "develop", "HEAD")


# ---------------------------------------------------------------------------
# review_implementation fallback integration
# ---------------------------------------------------------------------------


class TestReviewImplementationFallback:
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service._committed_diff_fallback")
    @patch("wade.services.review_delegation_service.run")
    def test_fallback_used_when_working_tree_empty(
        self,
        mock_run: MagicMock,
        mock_fallback: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        mock_fallback.return_value = "diff --git a/f.py b/f.py\n+committed line\n"
        mock_template.return_value = "Review:\n{diff_content}"
        mock_config.return_value = ProjectConfig(
            ai=AIConfig(review_implementation=AICommandConfig(mode="prompt", enabled=True))
        )
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="LGTM", mode=DelegationMode.PROMPT
        )

        result = review_implementation()

        assert result.success is True
        assert result.skipped is not True
        call_args = mock_delegate.call_args[0][0]
        assert "committed line" in call_args.prompt

    @patch("wade.services.review_delegation_service._committed_diff_fallback")
    @patch("wade.services.review_delegation_service.run")
    def test_fallback_not_called_in_staged_mode(
        self,
        mock_run: MagicMock,
        mock_fallback: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        mock_fallback.return_value = "should not be used"

        result = review_implementation(staged=True)

        mock_fallback.assert_not_called()
        assert result.skipped is True

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service._committed_diff_fallback")
    @patch("wade.services.review_delegation_service.run")
    def test_working_tree_diff_takes_priority(
        self,
        mock_run: MagicMock,
        mock_fallback: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="diff --git a/f.py b/f.py\n+working tree line\n"
        )
        mock_fallback.return_value = "should not be used"
        mock_template.return_value = "Review:\n{diff_content}"
        mock_config.return_value = ProjectConfig(
            ai=AIConfig(review_implementation=AICommandConfig(mode="prompt", enabled=True))
        )
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        result = review_implementation()

        mock_fallback.assert_not_called()
        assert result.success is True
        call_args = mock_delegate.call_args[0][0]
        assert "working tree line" in call_args.prompt

    @patch("wade.services.review_delegation_service._committed_diff_fallback")
    @patch("wade.services.review_delegation_service.run")
    def test_fallback_returns_empty_skips_review(
        self,
        mock_run: MagicMock,
        mock_fallback: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        mock_fallback.return_value = ""

        result = review_implementation()

        assert result.success is True
        assert result.skipped is True
        assert "No changes" in result.feedback
