"""Review-command orchestration for binding-aware receipts and pass budgets."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

from wade.models.delegation import DelegationMode, DelegationResult
from wade.models.session_manifest import ResolvedBinding, ReviewOutcome
from wade.models.skill import ResolvedSkill
from wade.services.skill_invocation_service import PreparedDelegationMethod

rds = importlib.import_module("wade.services.review_delegation_service")


def _cap(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return " ".join((captured.out + "\n" + captured.err).split())


@pytest.fixture
def review_preflight(monkeypatch: pytest.MonkeyPatch) -> PreparedDelegationMethod:
    """Provide a stable foreign binding for command-orchestration unit tests."""

    skill = ResolvedSkill(
        canonical_ref="builtin:code-review",
        source_path="templates/skills/code-review",
        materialized_path=".wade/operations/code-review/test/skills/builtin/code-review",
        content_digest=f"sha256:{'1' * 64}",
        files=("SKILL.md",),
    )
    prepared = PreparedDelegationMethod(
        binding=ResolvedBinding.from_skills((skill,)),
        method_section="<method>Review carefully.</method>",
        host_session=None,
        operation_bundle=None,
    )
    monkeypatch.setattr(rds, "prepare_delegation_method", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(rds.git_repo, "rev_parse", lambda *args, **kwargs: "a" * 40)
    monkeypatch.setattr(rds.git_repo, "get_current_branch", lambda *args, **kwargs: "main")
    return prepared


class TestReviewImplementationReceipts:
    def test_no_diff_records_satisfying_no_diff_outcome(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value=""),
            patch.object(rds, "_committed_diff_fallback", return_value=""),
            patch.object(rds, "_record_binding_outcome", return_value=0) as record,
        ):
            result = rds.review_implementation()

        assert result.skipped is True
        assert record.call_args.args[2] is review_preflight
        assert record.call_args.args[3] is ReviewOutcome.NO_DIFF

    @pytest.mark.parametrize(
        ("result", "outcome"),
        (
            (
                DelegationResult(success=True, feedback="ok", mode=DelegationMode.HEADLESS),
                ReviewOutcome.REVIEWED,
            ),
            (
                DelegationResult(
                    success=False,
                    feedback="partial",
                    mode=DelegationMode.HEADLESS,
                    exit_code=1,
                    timed_out=True,
                ),
                ReviewOutcome.TIMED_OUT,
            ),
        ),
    )
    def test_completed_attempt_records_binding_outcome(
        self,
        tmp_path: Path,
        review_preflight: PreparedDelegationMethod,
        result: DelegationResult,
        outcome: ReviewOutcome,
    ) -> None:
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=result),
            patch.object(rds, "_record_binding_outcome", return_value=1) as record,
        ):
            rds.review_implementation()

        assert record.call_args.args[2] is review_preflight
        assert record.call_args.args[3] is outcome

    def test_prompt_only_review_writes_no_receipt_or_pass(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        prompted = DelegationResult(
            success=True,
            feedback="Review this diff yourself.",
            mode=DelegationMode.PROMPT,
        )
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=prompted),
            patch.object(rds, "_record_binding_outcome") as record,
            patch.object(rds, "_announce_review_pass_budget") as announce,
        ):
            result = rds.review_implementation()

        assert result.mode is DelegationMode.PROMPT
        record.assert_not_called()
        announce.assert_not_called()

    def test_explicit_self_review_acknowledgement_writes_receipt_without_delegating(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation") as delegate,
            patch.object(rds, "_record_binding_outcome", return_value=1) as record,
            patch.object(rds, "_announce_review_pass_budget") as announce,
        ):
            result = rds.review_implementation(ack_self_review=True)

        assert result.success is True
        delegate.assert_not_called()
        assert record.call_args.args[2] is review_preflight
        assert record.call_args.args[3] is ReviewOutcome.REVIEWED
        announce.assert_called_once_with(1, 2)

    def test_staged_self_review_ack_cannot_certify_an_empty_index(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        diffs = rds._ReviewDiffs(
            committed="diff --git a b",
            staged="",
            unstaged="",
        )
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds, "_collect_review_diffs", return_value=diffs),
            patch.object(rds, "_record_binding_outcome") as record,
        ):
            result = rds.review_implementation(staged=True, ack_self_review=True)

        assert result.success is False
        assert result.exit_code == 1
        record.assert_called_once()
        assert record.call_args.args[3] is ReviewOutcome.NOTHING_STAGED

    def test_launch_failure_does_not_record_or_spend_pass(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        failure = DelegationResult(
            success=False,
            feedback="Not logged in",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
        )
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=failure),
            patch.object(rds, "_record_binding_outcome") as record,
            patch.object(rds.console, "warn") as warn,
        ):
            rds.review_implementation()

        record.assert_not_called()
        assert "no review-pass budget" in warn.call_args.args[0]


class TestAnnounceReviewPassBudget:
    def test_remaining_budget_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        rds._announce_review_pass_budget(1, 2)
        text = _cap(capsys).lower()
        assert "review pass 1 of 2" in text
        assert "1 pass left" in text
        assert "done.max_review_passes" in text

    def test_cap_reached_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        rds._announce_review_pass_budget(2, 2)
        text = _cap(capsys).lower()
        assert "review pass 2 of 2" in text
        assert "reached" in text

    def test_review_forwards_persisted_binding_count(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        success = DelegationResult(success=True, feedback="ok", mode=DelegationMode.HEADLESS)
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=success),
            patch.object(rds, "_record_binding_outcome", return_value=2),
            patch.object(rds, "_announce_review_pass_budget") as announce,
        ):
            rds.review_implementation()

        announce.assert_called_once_with(2, 2)

    def test_failed_receipt_write_is_not_announced(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        success = DelegationResult(success=True, feedback="ok", mode=DelegationMode.HEADLESS)
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=success),
            patch.object(rds, "_record_binding_outcome", return_value=None),
            patch.object(rds, "_announce_review_pass_budget") as announce,
        ):
            rds.review_implementation()

        announce.assert_not_called()
