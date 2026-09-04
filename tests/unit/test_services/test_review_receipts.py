"""Review-command orchestration for binding-aware receipts and pass budgets."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.models.delegation import DelegationMode, DelegationResult
from wade.models.session_manifest import ResolvedBinding, ReviewOutcome, ReviewRecord
from wade.models.skill import ResolvedSkill
from wade.models.workflow import DelegationKind
from wade.services.review_record_service import (
    count_binding_passes,
    read_review_record,
    write_review_record,
)
from wade.services.skill_invocation_service import PreparedDelegationMethod
from wade.utils.runtime_env import CODEX_SANDBOX_ENV

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


class TestUnattemptedReviewGate:
    """A reviewer that never started must leave every gate exactly as it was (#480)."""

    @staticmethod
    def _never_launched(
        feedback: str = "Unknown AI tool: claude",
        *,
        inherited_sandbox_profile_mismatch: bool = False,
    ) -> DelegationResult:
        return DelegationResult(
            success=False,
            feedback=feedback,
            mode=DelegationMode.HEADLESS,
            exit_code=1,
            never_launched=True,
            inherited_sandbox_profile_mismatch=inherited_sandbox_profile_mismatch,
        )

    def _run(
        self,
        tmp_path: Path,
        result: DelegationResult,
    ) -> tuple[MagicMock, MagicMock]:
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=result),
            patch.object(rds, "_record_binding_outcome", return_value=0) as record,
            patch.object(rds, "_announce_review_pass_budget") as announce,
        ):
            rds.review_implementation()
        return record, announce

    def test_records_unattempted_without_spending_budget(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        record, announce = self._run(tmp_path, self._never_launched())

        assert record.call_args.args[3] is ReviewOutcome.UNATTEMPTED
        announce.assert_not_called()

    def test_capability_refusal_records_unattempted_without_spending_budget(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        result = self._never_launched("sandbox unavailable")

        record, announce = self._run(tmp_path, result)

        assert record.call_args.args[3] is ReviewOutcome.UNATTEMPTED
        announce.assert_not_called()

    def test_the_outcome_neither_satisfies_nor_consumes(self) -> None:
        assert ReviewOutcome.UNATTEMPTED.satisfies_review is False
        assert ReviewOutcome.UNATTEMPTED.consumes_pass is False

    def test_a_nonzero_exit_is_still_not_recorded(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        """Only "never started" is unattempted; a reviewer that ran is not."""
        ran_and_failed = DelegationResult(
            success=False,
            feedback="exited 3",
            mode=DelegationMode.HEADLESS,
            exit_code=3,
        )
        record, _ = self._run(tmp_path, ran_and_failed)

        record.assert_not_called()

    def test_a_known_sandboxed_parent_keeps_a_generic_denial_hedged(
        self,
        tmp_path: Path,
        review_preflight: PreparedDelegationMethod,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # The probe reports the *first* marker it recognises, so a suite run from
        # inside another AI session would otherwise name that one instead.
        for name in ("CLAUDE_CODE", "CLAUDE_CODE_ENTRYPOINT", "COPILOT_CLI"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        monkeypatch.setenv("CODEX_CLI", "1")
        self._run(
            tmp_path,
            self._never_launched("permission denied", inherited_sandbox_profile_mismatch=True),
        )

        text = _cap(capsys)
        assert " ".join(rds._HEDGED_REVIEW_FAILURE.split()) in text
        assert "executable permissions or network configuration" in text
        assert "could not reach its own host credentials" not in text

    def test_an_explicit_sandbox_policy_gets_the_specific_cause(
        self,
        tmp_path: Path,
        review_preflight: PreparedDelegationMethod,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        for name in ("CLAUDE_CODE", "CLAUDE_CODE_ENTRYPOINT", "COPILOT_CLI"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        monkeypatch.setenv("CODEX_CLI", "1")
        self._run(
            tmp_path,
            self._never_launched(
                "launch denied by sandbox policy", inherited_sandbox_profile_mismatch=True
            ),
        )

        text = _cap(capsys)
        assert "Codex CLI is sandboxed" in text
        assert "wade review implementation --no-sandbox" in text
        assert "No review-pass budget was consumed" in text
        assert "could not confirm an unattempted review record" in text

    def test_specific_failure_does_not_overstate_a_retained_reviewed_receipt(
        self,
        tmp_path: Path,
        review_preflight: PreparedDelegationMethod,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A failed attempt cannot claim it wrote an unattempted record over success."""
        write_review_record(
            tmp_path,
            delegation=DelegationKind.CODE_REVIEW,
            commit="a" * 40,
            binding=review_preflight.binding,
            outcome=ReviewOutcome.REVIEWED,
        )
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        monkeypatch.setenv("CODEX_CLI", "1")

        rds._report_failed_review(
            tmp_path,
            "a" * 40,
            review_preflight,
            self._never_launched(
                "launch denied by sandbox policy", inherited_sandbox_profile_mismatch=True
            ),
        )

        text = _cap(capsys)
        assert "existing reviewed review record was retained" in text
        assert "unattempted audit record was written" not in text

    def test_a_sandboxed_parent_still_needs_a_denial_shaped_failure(
        self,
        tmp_path: Path,
        review_preflight: PreparedDelegationMethod,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A known boundary says the reviewer *could* be denied, never that it was.

        ``Unknown AI tool`` is a configuration refusal that never touched the OS.
        Blaming it on inaccessible host credentials would be a confident wrong
        cause, and would suppress the generic remediation that actually helps.
        """
        for name in ("CLAUDE_CODE", "CLAUDE_CODE_ENTRYPOINT", "COPILOT_CLI"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        monkeypatch.setenv("CODEX_CLI", "1")
        self._run(tmp_path, self._never_launched("Unknown AI tool: claude"))

        text = _cap(capsys)
        assert "is sandboxed" not in text
        assert " ".join(rds._HEDGED_REVIEW_FAILURE.split()) in text

    def test_an_unknown_parent_keeps_the_hedged_wording_verbatim(
        self,
        tmp_path: Path,
        review_preflight: PreparedDelegationMethod,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No signal from the runtime means no claim about it — today's text stands."""
        self._run(tmp_path, self._never_launched("some unrelated failure"))

        text = _cap(capsys)
        assert " ".join(rds._HEDGED_REVIEW_FAILURE.split()) in text
        assert "is sandboxed" not in text

    def test_a_denial_shape_with_no_signal_is_offered_as_a_possibility(
        self,
        tmp_path: Path,
        review_preflight: PreparedDelegationMethod,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._run(tmp_path, self._never_launched("open /home/me/.claude: permission denied"))

        text = _cap(capsys)
        assert " ".join(rds._HEDGED_REVIEW_FAILURE.split()) in text
        assert "cannot confirm" in text


class TestUnattemptedReviewRecordPrecedence:
    """An unattempted record can never overwrite or downgrade a real one."""

    @staticmethod
    def _binding() -> ResolvedBinding:
        skill = ResolvedSkill(
            canonical_ref="builtin:code-review",
            source_path="templates/skills/code-review",
            materialized_path=".wade/operations/code-review/test/skills/builtin/code-review",
            content_digest=f"sha256:{'1' * 64}",
            files=("SKILL.md",),
        )
        return ResolvedBinding.from_skills((skill,))

    def _write(self, root: Path, commit: str, outcome: ReviewOutcome) -> None:
        write_review_record(
            root,
            delegation=DelegationKind.CODE_REVIEW,
            commit=commit,
            binding=self._binding(),
            outcome=outcome,
        )

    def _read(self, root: Path, commit: str) -> ReviewRecord | None:
        return read_review_record(
            root,
            delegation=DelegationKind.CODE_REVIEW,
            commit=commit,
            binding=self._binding(),
        )

    def test_unattempted_does_not_replace_an_existing_reviewed_receipt(
        self, tmp_path: Path
    ) -> None:
        commit = "a" * 40
        self._write(tmp_path, commit, ReviewOutcome.REVIEWED)
        self._write(tmp_path, commit, ReviewOutcome.UNATTEMPTED)

        record = self._read(tmp_path, commit)
        assert record is not None
        assert record.outcome is ReviewOutcome.REVIEWED

    def test_unattempted_adds_no_pass_to_the_binding_count(self, tmp_path: Path) -> None:
        self._write(tmp_path, "b" * 40, ReviewOutcome.UNATTEMPTED)

        assert (
            count_binding_passes(
                tmp_path,
                delegation=DelegationKind.CODE_REVIEW,
                binding=self._binding(),
            )
            == 0
        )

    def test_a_real_receipt_still_promotes_over_an_unattempted_one(self, tmp_path: Path) -> None:
        commit = "c" * 40
        self._write(tmp_path, commit, ReviewOutcome.UNATTEMPTED)
        self._write(tmp_path, commit, ReviewOutcome.REVIEWED)

        record = self._read(tmp_path, commit)
        assert record is not None
        assert record.outcome is ReviewOutcome.REVIEWED
        assert record.consumes_pass is True
