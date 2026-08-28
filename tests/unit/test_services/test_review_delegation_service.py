"""Tests for the review delegation service."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from crossby.models.ai import EffortLevel

from wade.git.repo import GitError
from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
from wade.models.delegation import DelegationMode, DelegationResult
from wade.models.permission import PermissionMode
from wade.models.session_manifest import ResolvedBinding
from wade.models.skill import ResolvedSkill
from wade.services import review_delegation_service as rds
from wade.services.review_delegation_service import (
    _committed_diff_fallback,
    _ReviewDiffs,
    _run_review_delegation,
    review_implementation,
    review_plan,
)
from wade.services.skill_invocation_service import PreparedDelegationMethod


def _prepared_method() -> PreparedDelegationMethod:
    skill = ResolvedSkill(
        canonical_ref="builtin:code-review",
        source_path="templates/skills/code-review",
        materialized_path=".wade/operations/code-review/test/skills/builtin/code-review",
        content_digest=f"sha256:{'1' * 64}",
        files=("SKILL.md",),
    )
    return PreparedDelegationMethod(
        binding=ResolvedBinding.from_skills((skill,)),
        method_section="<method>Review carefully.</method>",
        host_session=None,
        operation_bundle=None,
    )


@pytest.fixture(autouse=True)
def _stable_review_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests focused on delegation; bundle integration is tested separately."""

    monkeypatch.setattr(
        rds,
        "prepare_delegation_method",
        lambda *args, **kwargs: _prepared_method(),
    )
    monkeypatch.setattr(rds.git_repo, "rev_parse", lambda *args, **kwargs: "a" * 40)
    monkeypatch.setattr(rds.git_repo, "get_current_branch", lambda *args, **kwargs: "main")
    monkeypatch.setattr(rds.git_repo, "detect_main_branch", lambda *args, **kwargs: "main")


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
    def test_plan_dir_only_review_uses_adjacent_frozen_session(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        plan_root = tmp_path / "detached-plan"
        plan_file = plan_root / "output" / "PLAN.md"
        plan_file.parent.mkdir(parents=True)
        plan_file.write_text("# Plan\n", encoding="utf-8")
        (plan_root / ".wade/session").mkdir(parents=True)
        captured: dict[str, Path] = {}

        def prepare(*_args: object, **kwargs: object) -> PreparedDelegationMethod:
            cwd = kwargs["cwd"]
            assert isinstance(cwd, Path)
            captured["cwd"] = cwd
            return _prepared_method()

        monkeypatch.setattr(rds, "prepare_delegation_method", prepare)
        mock_template.return_value = "Review the plan."
        mock_config.return_value = _review_config(review_plan_enabled=True)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        assert review_plan(str(plan_file)).success
        assert captured["cwd"] == plan_root
        assert mock_delegate.call_args.args[0].cwd == plan_root

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
    def test_scaled_advisory_announces_worst_case_total(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        mock_console: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A scaled budget retries on timeout, so the advisory names the worst-case total.

        If the orchestrator only reserves the base budget it kills the call
        before the retry runs.
        """
        from wade.services.delegation_service import extended_timeout

        plan_file = tmp_path / "PLAN.md"
        plan_file.write_text("# Plan")
        mock_template.return_value = "{plan_content}"
        mock_config.return_value = _review_config(
            review_plan_enabled=True,
            review_plan_mode="headless",
            review_plan_timeout=None,  # unset → scaled → retries
        )
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        review_plan(str(plan_file))

        notices = " ".join(
            str(call.args[0]) for call in mock_console.info.call_args_list if call.args
        )
        request = mock_delegate.call_args[0][0]
        worst_case = request.timeout + extended_timeout(request.timeout)
        assert str(worst_case) in notices
        assert "retr" in notices.lower()  # mentions the retry

    @patch("wade.services.review_delegation_service.console")
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    def test_explicit_timeout_advisory_says_no_retry(
        self,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        mock_console: MagicMock,
        tmp_path: Path,
    ) -> None:
        """An explicit budget is verbatim with no retry — the advisory must not promise one."""
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
        assert "no retry" in notices.lower()
        # The scaled worst-case (420 + retry = 1050) must NOT be announced.
        assert "1050" not in notices

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
        assert "No committed, staged, or unstaged changes" in result.feedback

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
        assert any(call.kwargs.get("staged") is True for call in mock_diff.call_args_list)

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
        mock_confirm.return_value = ("claude", None, None, PermissionMode.DEFAULT)
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
# _run_review_delegation permission-mode resolution + forwarding
# ---------------------------------------------------------------------------


def _echo_confirm(
    resolved_tool: str | None,
    resolved_model: str | None,
    *,
    resolved_effort: object = None,
    resolved_permission_mode: PermissionMode = PermissionMode.DEFAULT,
    **_kwargs: object,
) -> tuple[str | None, str | None, object, PermissionMode]:
    """confirm_ai_selection stand-in that echoes the display mode it was handed.

    Lets a test exercise the real resolve_permission_mode + effective-mode logic
    (config → display → request) without driving the interactive UI.
    """
    return resolved_tool, resolved_model, resolved_effort, resolved_permission_mode


class TestRunReviewDelegationPermissionMode:
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.confirm_ai_selection")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_interactive_forwards_config_yolo(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """`.wade.yml` ai.review_batch: {mode: interactive, yolo: true} launches yolo."""
        mock_config.return_value = ProjectConfig(
            ai=AIConfig(review_batch=AICommandConfig(mode="interactive", yolo=True, enabled=True))
        )
        mock_tool.return_value = "claude"
        mock_model.return_value = None
        mock_effort.return_value = None
        mock_confirm.side_effect = _echo_confirm
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.INTERACTIVE
        )

        _run_review_delegation("prompt text", "review_batch")

        request = mock_delegate.call_args[0][0]
        assert request.permission_mode == PermissionMode.YOLO

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.confirm_ai_selection")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_headless_review_forces_default_even_with_config_yolo(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """Headless review stays read-only: no yolo reaches the request or display."""
        mock_config.return_value = ProjectConfig(
            ai=AIConfig(review_plan=AICommandConfig(mode="headless", yolo=True, enabled=True))
        )
        mock_tool.return_value = "claude"
        mock_model.return_value = None
        mock_effort.return_value = None
        mock_confirm.side_effect = _echo_confirm
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        _run_review_delegation("prompt text", "review_plan")

        request = mock_delegate.call_args[0][0]
        assert request.permission_mode == PermissionMode.DEFAULT
        # The display mode handed to confirm was also forced to DEFAULT (shown == applied).
        assert mock_confirm.call_args.kwargs["resolved_permission_mode"] == PermissionMode.DEFAULT

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.confirm_ai_selection")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_explicit_permission_mode_overrides_config(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """An explicit --permission-mode wins over a conflicting config value."""
        mock_config.return_value = ProjectConfig(
            ai=AIConfig(
                review_batch=AICommandConfig(
                    mode="interactive", permission_mode="default", enabled=True
                )
            )
        )
        mock_tool.return_value = "claude"
        mock_model.return_value = None
        mock_effort.return_value = None
        mock_confirm.side_effect = _echo_confirm
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.INTERACTIVE
        )

        _run_review_delegation(
            "prompt text",
            "review_batch",
            permission_mode="auto",
            permission_mode_explicit=True,
        )

        request = mock_delegate.call_args[0][0]
        assert request.permission_mode == PermissionMode.AUTO


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
        mock_confirm.return_value = ("claude", None, EffortLevel.LOW, PermissionMode.DEFAULT)
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
        mock_confirm.return_value = ("claude", None, None, PermissionMode.DEFAULT)
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


class TestReviewImplementationChangeSets:
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service._collect_review_diffs")
    @patch("wade.git.repo.get_repo_root")
    def test_committed_changes_are_reviewed(
        self,
        mock_repo_root: MagicMock,
        mock_diffs: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        mock_repo_root.return_value = Path("/repo")
        mock_diffs.return_value = _ReviewDiffs(
            committed="diff --git a/f.py b/f.py\n+committed line\n",
            staged="",
            unstaged="",
        )
        mock_template.return_value = "Review:\n{diff_content}"
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="LGTM", mode=DelegationMode.PROMPT
        )

        result = review_implementation()

        assert result.success is True
        assert "committed line" in mock_delegate.call_args[0][0].prompt

    @patch("wade.services.review_delegation_service._collect_review_diffs")
    @patch("wade.git.repo.get_repo_root")
    def test_empty_staged_scope_does_not_hide_other_changes(
        self,
        mock_repo_root: MagicMock,
        mock_diffs: MagicMock,
    ) -> None:
        mock_repo_root.return_value = Path("/repo")
        mock_diffs.return_value = _ReviewDiffs(
            committed="diff --git a/f.py b/f.py",
            staged="",
            unstaged="",
        )

        result = review_implementation(staged=True)

        assert result.skipped is True
        assert "Stage the intended changes" in result.feedback

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service._collect_review_diffs")
    @patch("wade.git.repo.get_repo_root")
    def test_default_input_includes_all_nonempty_change_sets(
        self,
        mock_repo_root: MagicMock,
        mock_diffs: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        mock_repo_root.return_value = Path("/repo")
        mock_diffs.return_value = _ReviewDiffs(
            committed="committed line",
            staged="staged line",
            unstaged="working tree line",
        )
        mock_template.return_value = "Review:\n{diff_content}"
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        result = review_implementation()

        assert result.success is True
        prompt = mock_delegate.call_args[0][0].prompt
        assert "committed line" in prompt
        assert "staged line" in prompt
        assert "working tree line" in prompt

    @patch("wade.services.review_delegation_service._collect_review_diffs")
    @patch("wade.git.repo.get_repo_root")
    def test_all_empty_change_sets_skip_review(
        self,
        mock_repo_root: MagicMock,
        mock_diffs: MagicMock,
    ) -> None:
        mock_repo_root.return_value = Path("/repo")
        mock_diffs.return_value = _ReviewDiffs(committed="", staged="", unstaged="")

        result = review_implementation()

        assert result.success is True
        assert result.skipped is True
        assert "No committed, staged, or unstaged changes" in result.feedback


# ---------------------------------------------------------------------------
# Timed-out review display + scaled timeout (#366)
# ---------------------------------------------------------------------------


class TestTimedOutReviewDisplay:
    """A timed-out review shows partial output via warn + plain print, not console.error."""

    @patch("wade.services.review_delegation_service.console")
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    def test_partial_feedback_shown_via_warn_not_error(
        self,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        mock_console: MagicMock,
    ) -> None:
        mock_config.return_value = _review_config(review_plan_mode="prompt")
        mock_delegate.return_value = DelegationResult(
            success=False,
            feedback="partial review findings",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
            timed_out=True,
        )

        result = _run_review_delegation("prompt text", "review_plan")

        assert result.timed_out is True
        mock_console.warn.assert_called()
        mock_console.error.assert_not_called()
        # Partial output printed plainly with markup disabled (untrusted text).
        mock_console.out.print.assert_called_once_with("partial review findings", markup=False)

    @patch("wade.services.review_delegation_service.console")
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    def test_empty_feedback_warns_without_print(
        self,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        mock_console: MagicMock,
    ) -> None:
        mock_config.return_value = _review_config(review_plan_mode="prompt")
        mock_delegate.return_value = DelegationResult(
            success=False,
            feedback="",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
            timed_out=True,
        )

        _run_review_delegation("prompt text", "review_plan")

        mock_console.warn.assert_called()
        mock_console.error.assert_not_called()
        mock_console.out.print.assert_not_called()


class TestReviewScaledTimeout:
    """review_implementation/_run_review_delegation scale the headless budget (#366)."""

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.confirm_ai_selection")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_scaled_timeout_grows_with_prompt_size(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """With ai.<cmd>.timeout unset, a bigger prompt yields a bigger budget."""
        from wade.services.delegation_service import TIMEOUT_FLOOR

        mock_config.return_value = _review_config(
            review_plan_mode="headless", review_plan_timeout=None
        )
        mock_tool.return_value = "claude"
        mock_model.return_value = None
        mock_effort.return_value = None
        mock_confirm.return_value = ("claude", None, None, False)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        _run_review_delegation("x" * 10, "review_plan")
        small_timeout = mock_delegate.call_args[0][0].timeout

        _run_review_delegation("x" * 80_000, "review_plan")
        big_timeout = mock_delegate.call_args[0][0].timeout

        assert small_timeout == TIMEOUT_FLOOR  # tiny payload → floor
        assert big_timeout > small_timeout

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.confirm_ai_selection")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_explicit_config_timeout_bypasses_scaling(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """An explicit ai.<cmd>.timeout is used verbatim even for a huge prompt."""
        mock_config.return_value = _review_config(
            review_plan_mode="headless", review_plan_timeout=333
        )
        mock_tool.return_value = "claude"
        mock_model.return_value = None
        mock_effort.return_value = None
        mock_confirm.return_value = ("claude", None, None, False)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        _run_review_delegation("x" * 80_000, "review_plan")

        assert mock_delegate.call_args[0][0].timeout == 333


class TestReviewPassCountUnaffectedByRetry:
    """One delegate() call → one recorded review pass, even with the internal retry (#366)."""

    @patch("wade.services.review_delegation_service.console")
    @patch("wade.services.review_delegation_service.count_review_passes")
    @patch("wade.services.review_delegation_service.record_review_pass")
    @patch("wade.services.review_delegation_service.write_marker")
    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.git.repo.rev_parse")
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_timed_out_review_records_single_pass(
        self,
        mock_repo_root: MagicMock,
        mock_diff: MagicMock,
        mock_rev_parse: MagicMock,
        mock_config: MagicMock,
        mock_template: MagicMock,
        mock_delegate: MagicMock,
        mock_write_marker: MagicMock,
        mock_record: MagicMock,
        mock_count: MagicMock,
        mock_console: MagicMock,
    ) -> None:
        mock_repo_root.return_value = Path("/repo")
        mock_rev_parse.return_value = "a" * 40
        mock_diff.return_value = "diff --git a/f.py b/f.py\n+line\n"
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_count.return_value = 1
        # _delegate_headless may retry internally, but review_implementation calls
        # delegate() once and gets one result — one review→fix cycle consumed.
        mock_delegate.return_value = DelegationResult(
            success=False,
            feedback="partial",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
            timed_out=True,
        )

        review_implementation()

        mock_record.assert_called_once()


# ---------------------------------------------------------------------------
# {review_budget} prompt substitution (#450)
# ---------------------------------------------------------------------------


class TestReviewBudgetPlaceholder:
    """The reviewer learns its own deadline via {review_budget} (#450)."""

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.confirm_ai_selection")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_headless_scaled_budget_names_per_attempt_timeout_not_worst_case(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """A scaled (retrying) headless budget must inject the per-attempt timeout only.

        The prompt is built once and reused across both attempts
        (``_delegate_headless``) — each attempt is killed at its own budget, so
        naming the worst-case retry sum here would understate how soon the
        *current* attempt is actually cut off.
        """
        from wade.services.delegation_service import extended_timeout

        mock_config.return_value = _review_config(
            review_plan_mode="headless", review_plan_timeout=None
        )
        mock_tool.return_value = "claude"
        mock_model.return_value = None
        mock_effort.return_value = None
        mock_confirm.return_value = ("claude", None, None, PermissionMode.DEFAULT)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        _run_review_delegation("Body.\n\n{review_budget}\n\n---\n", "review_plan")

        request = mock_delegate.call_args[0][0]
        assert "{review_budget}" not in request.prompt
        assert f"{request.timeout}s" in request.prompt
        worst_case = request.timeout + extended_timeout(request.timeout)
        assert worst_case != request.timeout  # sanity: the two numbers differ
        assert str(worst_case) not in request.prompt

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.confirm_ai_selection")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_headless_explicit_timeout_names_the_configured_value(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """An explicit ai.<cmd>.timeout (no retry) is named verbatim in the prompt."""
        mock_config.return_value = _review_config(
            review_implementation_mode="headless", review_implementation_timeout=420
        )
        mock_tool.return_value = "claude"
        mock_model.return_value = None
        mock_effort.return_value = None
        mock_confirm.return_value = ("claude", None, None, PermissionMode.DEFAULT)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        _run_review_delegation("{review_budget}", "review_implementation")

        request = mock_delegate.call_args[0][0]
        assert request.timeout == 420
        assert "420s" in request.prompt
        assert "{review_budget}" not in request.prompt

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.confirm_ai_selection")
    @patch("wade.services.review_delegation_service.resolve_effort")
    @patch("wade.services.review_delegation_service.resolve_model")
    @patch("wade.services.review_delegation_service.resolve_ai_tool")
    @patch("wade.services.review_delegation_service.load_config")
    def test_interactive_mode_gets_no_hard_deadline_wording(
        self,
        mock_config: MagicMock,
        mock_tool: MagicMock,
        mock_model: MagicMock,
        mock_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """review_batch's default (interactive) has no subprocess kill — no fake deadline."""
        mock_config.return_value = ProjectConfig(
            ai=AIConfig(review_batch=AICommandConfig(enabled=True))
        )
        mock_tool.return_value = "claude"
        mock_model.return_value = None
        mock_effort.return_value = None
        mock_confirm.return_value = ("claude", None, None, PermissionMode.DEFAULT)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.INTERACTIVE
        )

        _run_review_delegation("{review_budget}", "review_batch")

        request = mock_delegate.call_args[0][0]
        assert request.mode == DelegationMode.INTERACTIVE
        assert "No hard deadline" in request.prompt
        assert "{review_budget}" not in request.prompt
        # No fabricated numeric deadline anywhere in the substituted line.
        assert not re.search(r"\broughly \*\*\d+s\*\*", request.prompt)

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    def test_prompt_mode_gets_no_hard_deadline_wording(
        self,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """Self-review (PROMPT mode) has no subprocess kill either — same wording as interactive."""
        mock_config.return_value = _review_config(review_plan_mode="prompt")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        _run_review_delegation("{review_budget}", "review_plan")

        request = mock_delegate.call_args[0][0]
        assert "No hard deadline" in request.prompt
        assert not re.search(r"\broughly \*\*\d+s\*\*", request.prompt)

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_review_implementation_prompt_carries_content_and_budget(
        self,
        mock_repo_root: MagicMock,
        mock_diff: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """The final prompt sent to delegate() has both the diff and the budget line."""
        mock_repo_root.return_value = Path("/repo")
        mock_diff.return_value = "diff --git a/f.py b/f.py\n+new line\n"
        mock_template.return_value = "Review:\n{review_budget}\n---\n{diff_content}"
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        review_implementation()

        call_args = mock_delegate.call_args[0][0]
        assert "diff --git" in call_args.prompt
        assert "No hard deadline" in call_args.prompt
        assert "{review_budget}" not in call_args.prompt
        assert "{diff_content}" not in call_args.prompt

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.git.repo.diff_worktree")
    @patch("wade.git.repo.get_repo_root")
    def test_diff_containing_literal_review_budget_token_is_not_corrupted(
        self,
        mock_repo_root: MagicMock,
        mock_diff: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
    ) -> None:
        """A diff that itself touches this prompt template must survive verbatim.

        {review_budget} must be substituted into the bare template *before*
        content is merged in — otherwise reviewing a diff that adds a
        ``{review_budget}`` line (e.g. to review-code.md itself) would have its
        own diff hunk corrupted by the later blind find-and-replace.
        """
        mock_repo_root.return_value = Path("/repo")
        mock_diff.return_value = "diff --git a/review-code.md b/review-code.md\n+{review_budget}\n"
        mock_template.return_value = "Review:\n{review_budget}\n---\n{diff_content}"
        mock_config.return_value = _review_config(review_implementation_enabled=True)
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.PROMPT
        )

        review_implementation()

        call_args = mock_delegate.call_args[0][0]
        # The diff hunk's own literal "{review_budget}" line survives untouched.
        assert "+{review_budget}" in call_args.prompt
        # The real scaffold placeholder was still replaced with real wording.
        assert "No hard deadline" in call_args.prompt
        # Exactly one substituted budget line, not zero and not a corrupted mix.
        assert call_args.prompt.count("No hard deadline") == 1
