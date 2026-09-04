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
    review_batch_mode: str | None = None,
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

        result = runner.invoke(app, ["review", "plan", str(plan_file), "--no-sandbox"])
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
        assert request.sandbox is False

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
    @patch("wade.services.review_delegation_service._collect_review_diffs")
    @patch("wade.git.repo.rev_parse")
    @patch("wade.git.repo.get_repo_root")
    def test_review_implementation_no_diff(
        self,
        mock_repo_root: MagicMock,
        mock_rev_parse: MagicMock,
        mock_collect: MagicMock,
        mock_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_config.return_value = _review_cli_config(review_implementation_enabled=True)
        mock_repo_root.return_value = tmp_path
        mock_rev_parse.return_value = "a" * 40
        mock_collect.return_value = MagicMock(empty=True)
        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 0
        assert "No committed, staged, or unstaged changes to review." in result.output

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service._collect_review_diffs")
    @patch("wade.git.repo.rev_parse")
    @patch("wade.git.repo.get_repo_root")
    def test_review_implementation_prompt_mode_exits_2(
        self,
        mock_repo_root: MagicMock,
        mock_rev_parse: MagicMock,
        mock_collect: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """PROMPT mode should exit 2 with a SELF-REVIEW message."""
        mock_repo_root.return_value = tmp_path
        mock_rev_parse.return_value = "a" * 40
        mock_collect.return_value = MagicMock(
            empty=False,
            review_input=MagicMock(return_value="diff --git a/f.py\n+line\n"),
        )
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _review_cli_config(review_implementation_mode="prompt")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="Clean code!", mode=DelegationMode.PROMPT
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 2
        assert "SELF-REVIEW" in result.output
        assert "--ack-self-review" in result.output
        mock_delegate.assert_called_once()
        request = mock_delegate.call_args[0][0]
        assert "diff --git a/f.py" in request.prompt
        assert request.mode == DelegationMode.PROMPT

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service._collect_review_diffs")
    @patch("wade.git.repo.rev_parse")
    @patch("wade.git.repo.get_repo_root")
    def test_review_implementation_interactive_mode_exits_0(
        self,
        mock_repo_root: MagicMock,
        mock_rev_parse: MagicMock,
        mock_collect: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """INTERACTIVE mode should exit 0 with REVIEW COMPLETE message."""
        mock_repo_root.return_value = tmp_path
        mock_rev_parse.return_value = "a" * 40
        mock_collect.return_value = MagicMock(
            empty=False,
            review_input=MagicMock(return_value="diff --git a/f.py\n+line\n"),
        )
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
    @patch("wade.services.review_delegation_service._collect_review_diffs")
    @patch("wade.git.repo.rev_parse")
    @patch("wade.git.repo.get_repo_root")
    def test_review_implementation_headless_mode_exits_0(
        self,
        mock_repo_root: MagicMock,
        mock_rev_parse: MagicMock,
        mock_collect: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """HEADLESS mode should exit 0 with REVIEW COMPLETE message."""
        mock_repo_root.return_value = tmp_path
        mock_rev_parse.return_value = "a" * 40
        mock_collect.return_value = MagicMock(
            empty=False,
            review_input=MagicMock(return_value="diff --git a/f.py\n+line\n"),
        )
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
    @patch("wade.services.review_delegation_service._collect_review_diffs")
    @patch("wade.git.repo.rev_parse")
    @patch("wade.git.repo.get_repo_root")
    def test_review_implementation_failure_exits_1(
        self,
        mock_repo_root: MagicMock,
        mock_rev_parse: MagicMock,
        mock_collect: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Failed review should exit 1."""
        mock_repo_root.return_value = tmp_path
        mock_rev_parse.return_value = "a" * 40
        mock_collect.return_value = MagicMock(
            empty=False,
            review_input=MagicMock(return_value="diff --git a/f.py\n+line\n"),
        )
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _review_cli_config(review_implementation_mode="interactive")
        mock_delegate.return_value = DelegationResult(
            success=False, feedback="Error", mode=DelegationMode.INTERACTIVE
        )

        result = runner.invoke(app, ["review", "implementation"])
        assert result.exit_code == 1
        mock_delegate.assert_called_once()

    @patch("wade.services.review_delegation_service.review_implementation")
    def test_review_implementation_ack_self_review_exits_zero(
        self,
        mock_review: MagicMock,
    ) -> None:
        mock_review.return_value = DelegationResult(
            success=True,
            feedback="Self-review acknowledged for the current commit and frozen review binding.",
            mode=DelegationMode.PROMPT,
        )

        result = runner.invoke(app, ["review", "implementation", "--ack-self-review"])

        assert result.exit_code == 0
        assert "Self-review acknowledged" in result.output
        assert mock_review.call_args.kwargs["ack_self_review"] is True

    @patch("wade.services.review_delegation_service.review_implementation")
    def test_review_implementation_staged_flag(
        self,
        mock_review: MagicMock,
    ) -> None:
        mock_review.return_value = DelegationResult(
            success=True,
            feedback="No staged changes.",
            mode=DelegationMode.PROMPT,
            skipped=True,
        )
        result = runner.invoke(app, ["review", "implementation", "--staged"])
        assert result.exit_code == 0
        assert mock_review.call_args.kwargs["staged"] is True

    @patch("wade.services.review_delegation_service.review_implementation")
    def test_review_implementation_forwards_explicit_sandbox_opt_out(
        self,
        mock_review: MagicMock,
    ) -> None:
        mock_review.return_value = DelegationResult(
            success=True,
            feedback="No changes.",
            mode=DelegationMode.PROMPT,
            skipped=True,
        )

        result = runner.invoke(app, ["review", "implementation", "--no-sandbox"])

        assert result.exit_code == 0
        assert mock_review.call_args.kwargs["sandbox"] is False

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
    def test_review_batch_prompt_mode_exits_2(self, mock_review_batch: MagicMock) -> None:
        mock_review_batch.return_value = DelegationResult(
            success=True,
            feedback="Review this batch yourself.",
            mode=DelegationMode.PROMPT,
        )

        result = runner.invoke(app, ["review", "batch", "123"])
        assert result.exit_code == 2
        assert "SELF-REVIEW" in result.output

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
                "--no-sandbox",
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
            yolo=None,
            permission_mode=None,
            permission_mode_explicit=False,
            sandbox=False,
            skills=None,
        )

    @patch("wade.services.batch_review_service.review_batch")
    def test_review_batch_failure_exits_1(self, mock_review_batch: MagicMock) -> None:
        mock_review_batch.return_value = DelegationResult(
            success=False,
            feedback="Review failed.",
            mode=DelegationMode.INTERACTIVE,
        )

        result = runner.invoke(app, ["review", "batch", "123", "--mode", "interactive"])
        assert result.exit_code == 1

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

    @patch("wade.services.batch_review_service.review_batch")
    def test_review_batch_effort_flag(
        self,
        mock_review_batch: MagicMock,
    ) -> None:
        mock_review_batch.return_value = DelegationResult(
            success=True,
            feedback="Review this batch yourself.",
            mode=DelegationMode.PROMPT,
        )

        result = runner.invoke(app, ["review", "batch", "123", "--effort", "high"])
        assert result.exit_code == 2
        mock_review_batch.assert_called_once_with(
            "123",
            ai_tool=None,
            model=None,
            mode=None,
            effort="high",
            ai_explicit=False,
            model_explicit=False,
            effort_explicit=True,
            yolo=None,
            permission_mode=None,
            permission_mode_explicit=False,
            sandbox=None,
            skills=None,
        )


# ---------------------------------------------------------------------------
# Effort flag
# ---------------------------------------------------------------------------


class TestReviewCliEffortFlag:
    @patch("wade.services.review_service.start", return_value=True)
    def test_review_pr_comments_effort_flag(self, mock_start: MagicMock) -> None:
        result = runner.invoke(app, ["review", "pr-comments", "42", "--effort", "high"])

        assert result.exit_code == 0
        assert mock_start.call_args.kwargs["effort"] == "high"
        assert mock_start.call_args.kwargs["effort_explicit"] is True

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
        mock_config.return_value = _review_cli_config(review_plan_mode="headless")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        result = runner.invoke(
            app,
            [
                "review",
                "plan",
                str(plan_file),
                "--mode",
                "headless",
                "--ai",
                "claude",
                "--effort",
                "low",
            ],
        )
        assert result.exit_code == 0
        mock_delegate.assert_called_once()
        request = mock_delegate.call_args[0][0]
        assert request.effort == "low"

    @patch("wade.services.review_delegation_service.delegate")
    @patch("wade.services.review_delegation_service.load_config")
    @patch("wade.services.review_delegation_service.load_prompt_template")
    @patch("wade.services.review_delegation_service._collect_review_diffs")
    @patch("wade.git.repo.rev_parse")
    @patch("wade.git.repo.get_repo_root")
    def test_review_implementation_effort_flag(
        self,
        mock_repo_root: MagicMock,
        mock_rev_parse: MagicMock,
        mock_collect: MagicMock,
        mock_template: MagicMock,
        mock_config: MagicMock,
        mock_delegate: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_repo_root.return_value = tmp_path
        mock_rev_parse.return_value = "a" * 40
        mock_collect.return_value = MagicMock(
            empty=False,
            review_input=MagicMock(return_value="diff --git a/f.py\n+line\n"),
        )
        mock_template.return_value = "{diff_content}"
        mock_config.return_value = _review_cli_config(review_implementation_mode="headless")
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="ok", mode=DelegationMode.HEADLESS
        )

        result = runner.invoke(
            app,
            [
                "review",
                "implementation",
                "--mode",
                "headless",
                "--ai",
                "claude",
                "--effort",
                "high",
            ],
        )
        assert result.exit_code == 0
        mock_delegate.assert_called_once()
        request = mock_delegate.call_args[0][0]
        assert request.effort == "high"


# ---------------------------------------------------------------------------
# Trigger (bot review triggers, #431)
# ---------------------------------------------------------------------------


class TestReviewTriggerCli:
    @patch("wade.services.review_service.trigger_bot_reviews")
    def test_dispatches_target_to_service(self, mock_trigger: MagicMock) -> None:
        mock_trigger.return_value = MagicMock(exit_code=0)
        result = runner.invoke(app, ["review", "trigger", "42"])
        assert result.exit_code == 0
        mock_trigger.assert_called_once()
        _, kwargs = mock_trigger.call_args
        assert mock_trigger.call_args.args[0] == "42"
        assert kwargs["selected_bots"] is None
        assert kwargs["dry_run"] is False

    @patch("wade.services.review_service.trigger_bot_reviews")
    def test_forwards_bot_and_dry_run_flags(self, mock_trigger: MagicMock) -> None:
        mock_trigger.return_value = MagicMock(exit_code=0)
        result = runner.invoke(
            app, ["review", "trigger", "42", "--bot", "codex", "--bot", "bugbot", "--dry-run"]
        )
        assert result.exit_code == 0
        _, kwargs = mock_trigger.call_args
        assert kwargs["selected_bots"] == ["codex", "bugbot"]
        assert kwargs["dry_run"] is True

    @patch("wade.services.review_service.trigger_bot_reviews")
    def test_propagates_nonzero_exit_code(self, mock_trigger: MagicMock) -> None:
        mock_trigger.return_value = MagicMock(exit_code=1)
        result = runner.invoke(app, ["review", "trigger", "42"])
        assert result.exit_code == 1
