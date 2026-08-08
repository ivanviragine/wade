"""Tests for the review delegation service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from crossby.models.ai import EffortLevel

from wade.git.repo import GitError
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

    @patch("wade.services.review_delegation_service.console")
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_headless_launch_notice_announces_timeout(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        mock_console: MagicMock,
        tmp_path: Path,
    ) -> None:
        """The pre-launch notice must tell the orchestrator how long to wait.

        Without the budget in the message, the AI tool driving wade kills the
        headless subprocess at its own (shorter) shell timeout before wade's
        own timeout can fire.
        """
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Plan")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _review_config(
            review_plan_enabled=True,
            review_plan_mode="headless",
            review_plan_timeout=420,
        )
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        review_plan(str(plan_file))

        notices = " ".join(
            str(call.args[0]) for call in mock_console.info.call_args_list if call.args
        )
        assert "420" in notices
        assert "background" in notices.lower()

    @patch("wade.services.review_delegation_service.console")
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_prompt_mode_emits_no_launch_notice(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        mock_console: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Prompt mode returns instantly — no subprocess, so no wait notice."""
        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Plan")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _review_config(
            review_plan_enabled=True, review_plan_mode="prompt"
        )
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        review_plan(str(plan_file))

        notices = " ".join(
            str(call.args[0]) for call in mock_console.info.call_args_list if call.args
        )
        assert "background" not in notices.lower()


# ---------------------------------------------------------------------------
# review_implementation
# ---------------------------------------------------------------------------


class TestReviewCode:
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service._committed_diff_fallback")
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_no_diff_warns(
        self,
        mock_repo_root: MagicMock,
        mock_diff: MagicMock,
        mock_fallback: MagicMock,
        mock_config: MagicMock,
    ) -> None:
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_repo_root.return_value = Path("/repo")
        mock_diff.return_value = ""
        mock_fallback.return_value = ""
        result = review_implementation()
        assert result.success is True
        assert "No changes" in result.feedback
        mock_fallback.assert_called_once_with()

    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_git_diff_failure_returns_error(
        self, mock_repo_root: MagicMock, mock_diff: MagicMock, mock_config: MagicMock
    ) -> None:
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_repo_root.return_value = Path("/repo")
        mock_diff.side_effect = GitError("git diff failed (exit 128): fatal: not a git repository")
        result = review_implementation()
        assert result.success is False
        assert "git diff failed" in result.feedback
        assert result.exit_code == 1

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_code_review_with_diff(
        self,
        mock_repo_root: MagicMock,
        mock_diff: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        mock_repo_root.return_value = Path("/repo")
        mock_diff.return_value = "diff --git a/foo.py b/foo.py\n+new line\n"
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
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_staged_flag_passed_to_git(
        self, mock_repo_root: MagicMock, mock_diff: MagicMock, mock_config: MagicMock
    ) -> None:
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_repo_root.return_value = Path("/repo")
        mock_diff.return_value = ""
        review_implementation(staged=True)
        assert mock_diff.call_args.kwargs["staged"] is True

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
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_invalid_mode_returns_error(
        self,
        mock_repo_root: MagicMock,
        mock_diff: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
    ) -> None:
        mock_repo_root.return_value = Path("/repo")
        mock_diff.return_value = "diff --git a/f.py\n+line\n"
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _review_config(review_implementation_enabled=True)

        result = review_implementation(mode="bad_value")
        assert result.success is False
        assert "Invalid delegation mode" in result.feedback
        assert result.exit_code == 1

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_prompt_mode_works_without_ai_tool_config(
        self,
        mock_repo_root: MagicMock,
        mock_diff: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """Prompt-mode implementation review should not require an AI tool config."""
        mock_repo_root.return_value = Path("/repo")
        mock_diff.return_value = "diff --git a/f.py\n+line\n"
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
# Feedback markup safety (#394)
# ---------------------------------------------------------------------------


class TestFeedbackMarkupSafety:
    """Untrusted delegation feedback with bracketed tokens must not crash Rich.

    Feedback routinely quotes source code containing Rich-markup-like tokens
    (e.g. ``console.print("[success]done[/]")``). Printing it with markup
    enabled made Rich parse ``[/]`` as a closing tag with nothing to close and
    raise ``rich.errors.MarkupError`` — *after* the review had already run,
    losing the feedback and the ``reviewed@<sha>`` marker.
    """

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    def test_success_feedback_with_bracket_markup_does_not_raise(
        self,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Success branch prints bracketed feedback literally instead of crashing.

        The ``[/]`` is intentionally *unbalanced* (a close tag with nothing to
        open) — exactly the quoted-code shape that made old Rich raise
        ``MarkupError: closing tag '[/]' ... has nothing to close``.
        """
        mock_config.return_value = _review_config(review_plan_mode="prompt")
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="Nit: the quoted snippet result[/] should stay literal",
            mode=DelegationMode.PROMPT,
        )

        result = _run_review_delegation("prompt text", "review_plan")

        assert result.success is True
        # The literal token survives to stdout — not swallowed as styling, not crashed.
        assert "[/]" in capsys.readouterr().out

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    def test_failure_feedback_with_bracket_markup_does_not_raise(
        self,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Error branch escapes bracketed feedback so console.error's wrapper still renders.

        The dangling ``[/]`` (nothing to close) is what crashed old Rich; here
        it must render literally while ``console.error``'s own ``[error]…[/]``
        styling still applies.
        """
        mock_config.return_value = _review_config(review_plan_mode="prompt")
        mock_delegate.return_value = DelegationResult(
            success=False,
            feedback="failed: the token result[/] has nothing to close",
            mode=DelegationMode.PROMPT,
            exit_code=1,
        )

        result = _run_review_delegation("prompt text", "review_plan")

        assert result.success is False
        # console.error writes to stderr; the escaped token renders literally.
        assert "[/]" in capsys.readouterr().err


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
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_fallback_used_when_working_tree_empty(
        self,
        mock_repo_root: MagicMock,
        mock_diff: MagicMock,
        mock_fallback: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        mock_repo_root.return_value = Path("/repo")
        mock_diff.return_value = ""
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
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_fallback_not_called_in_staged_mode(
        self,
        mock_repo_root: MagicMock,
        mock_diff: MagicMock,
        mock_fallback: MagicMock,
    ) -> None:
        mock_repo_root.return_value = Path("/repo")
        mock_diff.return_value = ""
        mock_fallback.return_value = "should not be used"

        result = review_implementation(staged=True)

        mock_fallback.assert_not_called()
        assert result.skipped is True

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service._committed_diff_fallback")
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_working_tree_diff_takes_priority(
        self,
        mock_repo_root: MagicMock,
        mock_diff: MagicMock,
        mock_fallback: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        mock_repo_root.return_value = Path("/repo")
        mock_diff.return_value = "diff --git a/f.py b/f.py\n+working tree line\n"
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
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_fallback_returns_empty_skips_review(
        self,
        mock_repo_root: MagicMock,
        mock_diff: MagicMock,
        mock_fallback: MagicMock,
    ) -> None:
        mock_repo_root.return_value = Path("/repo")
        mock_diff.return_value = ""
        mock_fallback.return_value = ""

        result = review_implementation()

        assert result.success is True
        assert result.skipped is True
        assert "No changes" in result.feedback
