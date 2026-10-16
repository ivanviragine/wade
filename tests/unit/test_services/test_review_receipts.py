"""Review-command orchestration for binding-aware receipts and pass budgets."""

from __future__ import annotations

import importlib
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.models.config import AICommandConfig, AIConfig, DoneConfig, ProjectConfig
from wade.models.delegation import DelegationMode, DelegationResult
from wade.models.session_manifest import (
    ResolvedBinding,
    ReviewOutcome,
    ReviewRecord,
    SessionManifest,
)
from wade.models.skill import ResolvedSkill, SkillSlot
from wade.models.workflow import SESSION_DEFINITIONS, AICommandKey, DelegationKind, SessionKind
from wade.services.implementation_service.done import _gate_review_ran
from wade.services.review_record_service import (
    count_binding_passes,
    read_review_record,
    write_review_record,
)
from wade.services.skill_invocation_service import PreparedDelegationMethod
from wade.skills.materializer import compute_session_bundle_digest
from wade.skills.validation import inspect_skill
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
        announce.assert_called_once_with(1, 2, host_session=None)

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

    def test_launch_failure_records_unattempted_without_spending_pass(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        failure = DelegationResult(
            success=False,
            feedback="Not logged in",
            never_launched=True,
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

        assert record.call_args.args[3] is ReviewOutcome.UNATTEMPTED
        assert "no review-pass budget" in warn.call_args.args[0]


class TestImplementationReviewPreflightCap:
    """The review command must enforce the same scoped budget as ``done``."""

    @staticmethod
    def _prepared_for(
        prepared: PreparedDelegationMethod,
        session: SessionKind | None,
        tmp_path: Path,
    ) -> PreparedDelegationMethod:
        return replace(
            prepared,
            host_session=session,
            operation_bundle=tmp_path / ".wade" / "operations" / "review-test",
        )

    @staticmethod
    def _config(limit: int) -> ProjectConfig:
        return ProjectConfig(done=DoneConfig(max_review_passes=limit))

    @staticmethod
    def _write_passes(root: Path, binding: ResolvedBinding, passes: int) -> None:
        for index in range(passes):
            record = write_review_record(
                root,
                delegation=DelegationKind.CODE_REVIEW,
                commit=f"{index + 1:040x}",
                binding=binding,
                outcome=ReviewOutcome.REVIEWED,
            )
            assert record is not None

    def test_exhausted_binding_skips_without_recording_or_delegating(
        self,
        tmp_path: Path,
        review_preflight: PreparedDelegationMethod,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config = self._config(limit=3)
        prepared = self._prepared_for(review_preflight, SessionKind.IMPLEMENTATION, tmp_path)
        self._write_passes(tmp_path, prepared.binding, passes=3)

        with (
            patch.object(rds, "prepare_delegation_method", return_value=prepared),
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(
                rds,
                "_collect_review_diffs",
                return_value=rds._ReviewDiffs(committed="diff --git a b", staged="", unstaged=""),
            ),
            patch.object(
                rds,
                "_load_review_config",
                return_value=(config, config.ai.review_implementation),
            ),
            patch.object(rds, "_run_review_delegation") as delegate,
            patch.object(rds, "_record_binding_outcome") as record,
            patch.object(rds, "cleanup_delegation_bundle") as cleanup,
        ):
            result = rds.review_implementation()

        assert result.success is True
        assert result.skipped is True
        assert "3 of 3" in result.feedback
        assert "No additional implementation review was launched" in result.feedback
        assert "wade implementation-session done" in result.feedback
        assert "active frozen review binding" in _cap(capsys)
        delegate.assert_not_called()
        record.assert_not_called()
        cleanup.assert_called_once_with(prepared, preserve=False)
        assert (
            count_binding_passes(
                tmp_path,
                delegation=DelegationKind.CODE_REVIEW,
                binding=prepared.binding,
            )
            == 3
        )

    def test_exhausted_binding_allows_self_review_acknowledgement(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        config = self._config(limit=1)
        prepared = self._prepared_for(review_preflight, SessionKind.IMPLEMENTATION, tmp_path)
        self._write_passes(tmp_path, prepared.binding, passes=1)

        with (
            patch.object(rds, "prepare_delegation_method", return_value=prepared),
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(
                rds,
                "_collect_review_diffs",
                return_value=rds._ReviewDiffs(committed="diff --git a b", staged="", unstaged=""),
            ),
            patch.object(
                rds,
                "_load_review_config",
                return_value=(config, config.ai.review_implementation),
            ),
            patch.object(rds, "_run_review_delegation") as delegate,
        ):
            result = rds.review_implementation(ack_self_review=True)

        assert result.success is True
        assert result.skipped is False
        assert "Self-review acknowledged" in result.feedback
        delegate.assert_not_called()
        receipt = read_review_record(
            tmp_path,
            delegation=DelegationKind.CODE_REVIEW,
            commit="a" * 40,
            binding=prepared.binding,
        )
        assert receipt is not None
        assert receipt.satisfies_review

    def test_below_limit_still_delegates(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        config = self._config(limit=3)
        prepared = self._prepared_for(review_preflight, SessionKind.IMPLEMENTATION, tmp_path)
        self._write_passes(tmp_path, prepared.binding, passes=2)
        prompted = DelegationResult(
            success=True,
            feedback="Review the diff.",
            mode=DelegationMode.PROMPT,
        )

        with (
            patch.object(rds, "prepare_delegation_method", return_value=prepared),
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(
                rds,
                "_collect_review_diffs",
                return_value=rds._ReviewDiffs(committed="diff --git a b", staged="", unstaged=""),
            ),
            patch.object(
                rds,
                "_load_review_config",
                return_value=(config, config.ai.review_implementation),
            ),
            patch.object(rds, "_run_review_delegation", return_value=prompted) as delegate,
        ):
            result = rds.review_implementation()

        assert result is prompted
        delegate.assert_called_once()

    def test_exhausted_binding_repairs_a_receipt_from_another_reviewer(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        config = self._config(limit=2)
        prepared = self._prepared_for(review_preflight, SessionKind.IMPLEMENTATION, tmp_path)
        self._write_passes(tmp_path, prepared.binding, passes=2)
        other_skill = ResolvedSkill(
            canonical_ref="builtin:alternate-code-review",
            source_path="templates/skills/alternate-code-review",
            materialized_path=".wade/operations/code-review/test/skills/builtin/alternate-code-review",
            content_digest=f"sha256:{'2' * 64}",
            files=("SKILL.md",),
        )
        other_binding = ResolvedBinding.from_skills((other_skill,))
        other_receipt = write_review_record(
            tmp_path,
            delegation=DelegationKind.CODE_REVIEW,
            commit="a" * 40,
            binding=other_binding,
            outcome=ReviewOutcome.REVIEWED,
        )
        assert other_receipt is not None
        prompted = DelegationResult(
            success=True,
            feedback="Review the diff.",
            mode=DelegationMode.PROMPT,
        )

        with (
            patch.object(rds, "prepare_delegation_method", return_value=prepared),
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(
                rds,
                "_collect_review_diffs",
                return_value=rds._ReviewDiffs(committed="diff --git a b", staged="", unstaged=""),
            ),
            patch.object(
                rds,
                "_load_review_config",
                return_value=(config, config.ai.review_implementation),
            ),
            patch.object(rds, "_run_review_delegation", return_value=prompted) as delegate,
        ):
            result = rds.review_implementation()

        assert result is prompted
        delegate.assert_called_once()

    def test_exhausted_binding_with_its_own_receipt_stays_skipped(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        config = self._config(limit=2)
        prepared = self._prepared_for(review_preflight, SessionKind.IMPLEMENTATION, tmp_path)
        self._write_passes(tmp_path, prepared.binding, passes=1)
        active_receipt = write_review_record(
            tmp_path,
            delegation=DelegationKind.CODE_REVIEW,
            commit="a" * 40,
            binding=prepared.binding,
            outcome=ReviewOutcome.REVIEWED,
        )
        assert active_receipt is not None
        other_skill = ResolvedSkill(
            canonical_ref="builtin:alternate-code-review",
            source_path="templates/skills/alternate-code-review",
            materialized_path=".wade/operations/code-review/test/skills/builtin/alternate-code-review",
            content_digest=f"sha256:{'2' * 64}",
            files=("SKILL.md",),
        )
        other_binding = ResolvedBinding.from_skills((other_skill,))
        other_receipt = write_review_record(
            tmp_path,
            delegation=DelegationKind.CODE_REVIEW,
            commit="a" * 40,
            binding=other_binding,
            outcome=ReviewOutcome.REVIEWED,
        )
        assert other_receipt is not None

        with (
            patch.object(rds, "prepare_delegation_method", return_value=prepared),
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(
                rds,
                "_collect_review_diffs",
                return_value=rds._ReviewDiffs(committed="diff --git a b", staged="", unstaged=""),
            ),
            patch.object(
                rds,
                "_load_review_config",
                return_value=(config, config.ai.review_implementation),
            ),
            patch.object(rds, "_run_review_delegation") as delegate,
        ):
            result = rds.review_implementation()

        assert result.success is True
        assert result.skipped is True
        assert "2 of 2" in result.feedback
        delegate.assert_not_called()

    def test_reservation_acquisition_failure_returns_reservation_error(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        config = self._config(limit=2)
        prepared = self._prepared_for(review_preflight, SessionKind.IMPLEMENTATION, tmp_path)
        reservation = MagicMock()
        reservation.__enter__.side_effect = OSError("lock unavailable")

        with (
            patch.object(rds, "prepare_delegation_method", return_value=prepared),
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(
                rds,
                "_collect_review_diffs",
                return_value=rds._ReviewDiffs(committed="diff --git a b", staged="", unstaged=""),
            ),
            patch.object(
                rds,
                "_load_review_config",
                return_value=(config, config.ai.review_implementation),
            ),
            patch.object(rds, "binding_pass_reservation", return_value=reservation),
            patch.object(rds, "cleanup_delegation_bundle") as cleanup,
        ):
            result = rds.review_implementation()

        assert result.success is False
        assert result.exit_code == 1
        assert (
            "Could not reserve an implementation review pass: lock unavailable" in result.feedback
        )
        cleanup.assert_called_once_with(prepared, preserve=True)

    def test_execution_os_error_is_not_reported_as_a_reservation_failure(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        config = self._config(limit=2)
        prepared = self._prepared_for(review_preflight, SessionKind.IMPLEMENTATION, tmp_path)
        reservation = MagicMock()

        with (
            patch.object(rds, "prepare_delegation_method", return_value=prepared),
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(
                rds,
                "_collect_review_diffs",
                return_value=rds._ReviewDiffs(committed="diff --git a b", staged="", unstaged=""),
            ),
            patch.object(
                rds,
                "_load_review_config",
                return_value=(config, config.ai.review_implementation),
            ),
            patch.object(rds, "binding_pass_reservation", return_value=reservation),
            patch.object(
                rds,
                "_run_review_delegation",
                side_effect=FileNotFoundError("review-code.md"),
            ),
            patch.object(rds, "cleanup_delegation_bundle") as cleanup,
            pytest.raises(FileNotFoundError, match="review-code\\.md"),
        ):
            rds.review_implementation()

        reservation.__enter__.assert_called_once_with()
        cleanup.assert_not_called()

    def test_historical_passes_for_another_binding_do_not_suppress_review(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        config = self._config(limit=2)
        prepared = self._prepared_for(review_preflight, SessionKind.IMPLEMENTATION, tmp_path)
        other_skill = ResolvedSkill(
            canonical_ref="builtin:alternate-code-review",
            source_path="templates/skills/alternate-code-review",
            materialized_path=".wade/operations/code-review/test/skills/builtin/alternate-code-review",
            content_digest=f"sha256:{'2' * 64}",
            files=("SKILL.md",),
        )
        other_binding = ResolvedBinding.from_skills((other_skill,))
        self._write_passes(tmp_path, other_binding, passes=2)
        prompted = DelegationResult(
            success=True,
            feedback="Review the diff.",
            mode=DelegationMode.PROMPT,
        )

        with (
            patch.object(rds, "prepare_delegation_method", return_value=prepared),
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(
                rds,
                "_collect_review_diffs",
                return_value=rds._ReviewDiffs(committed="diff --git a b", staged="", unstaged=""),
            ),
            patch.object(
                rds,
                "_load_review_config",
                return_value=(config, config.ai.review_implementation),
            ),
            patch.object(rds, "_run_review_delegation", return_value=prompted) as delegate,
        ):
            result = rds.review_implementation()

        assert result is prompted
        delegate.assert_called_once()

    @pytest.mark.parametrize(
        ("session", "should_skip"),
        (
            (SessionKind.IMPLEMENTATION, True),
            (SessionKind.REVIEW_PR_COMMENTS, False),
            (None, False),
        ),
    )
    def test_cap_is_limited_to_implementation_sessions(
        self,
        tmp_path: Path,
        review_preflight: PreparedDelegationMethod,
        session: SessionKind | None,
        should_skip: bool,
    ) -> None:
        config = self._config(limit=1)
        prepared = self._prepared_for(review_preflight, session, tmp_path)
        self._write_passes(tmp_path, prepared.binding, passes=1)
        prompted = DelegationResult(
            success=True,
            feedback="Review the diff.",
            mode=DelegationMode.PROMPT,
        )

        with (
            patch.object(rds, "prepare_delegation_method", return_value=prepared),
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(
                rds,
                "_collect_review_diffs",
                return_value=rds._ReviewDiffs(committed="diff --git a b", staged="", unstaged=""),
            ),
            patch.object(
                rds,
                "_load_review_config",
                return_value=(config, config.ai.review_implementation),
            ),
            patch.object(rds, "_run_review_delegation", return_value=prompted) as delegate,
        ):
            result = rds.review_implementation()

        assert result.skipped is should_skip
        assert delegate.called is not should_skip

    def test_cap_guard_does_not_certify_an_empty_staged_acknowledgement(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        config = self._config(limit=1)
        prepared = self._prepared_for(review_preflight, SessionKind.IMPLEMENTATION, tmp_path)
        self._write_passes(tmp_path, prepared.binding, passes=1)
        diffs = rds._ReviewDiffs(committed="diff --git a b", staged="", unstaged="")

        with (
            patch.object(rds, "prepare_delegation_method", return_value=prepared),
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds, "_collect_review_diffs", return_value=diffs),
            patch.object(
                rds,
                "_load_review_config",
                return_value=(config, config.ai.review_implementation),
            ),
            patch.object(rds, "_record_binding_outcome") as record,
        ):
            result = rds.review_implementation(staged=True, ack_self_review=True)

        assert result.success is False
        assert result.exit_code == 1
        record.assert_called_once()
        assert record.call_args.args[3] is ReviewOutcome.NOTHING_STAGED

    @pytest.mark.parametrize("succeeds", (True, False))
    def test_concurrent_reviews_cannot_dispatch_beyond_the_binding_cap(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod, succeeds: bool
    ) -> None:
        config = self._config(limit=1)
        prepared = self._prepared_for(review_preflight, SessionKind.IMPLEMENTATION, tmp_path)
        completed = DelegationResult(
            success=succeeds,
            feedback="result",
            mode=DelegationMode.HEADLESS,
            exit_code=0 if succeeds else 1,
        )
        first_started = threading.Event()
        release_first = threading.Event()
        second_finished = threading.Event()
        calls = 0
        results: list[DelegationResult] = []
        errors: list[BaseException] = []

        def delegate(*args: object, **kwargs: object) -> DelegationResult:
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                release_first.wait(timeout=5)
            return completed

        def invoke(*, second: bool) -> None:
            try:
                results.append(rds.review_implementation())
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)
            finally:
                if second:
                    second_finished.set()

        with (
            patch.object(rds, "prepare_delegation_method", return_value=prepared),
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(
                rds,
                "_collect_review_diffs",
                return_value=rds._ReviewDiffs(committed="diff --git a b", staged="", unstaged=""),
            ),
            patch.object(
                rds,
                "_load_review_config",
                return_value=(config, config.ai.review_implementation),
            ),
            patch.object(rds, "_run_review_delegation", side_effect=delegate),
            patch.object(rds, "cleanup_delegation_bundle"),
        ):
            first = threading.Thread(target=invoke, kwargs={"second": False})
            second = threading.Thread(target=invoke, kwargs={"second": True})
            first.start()
            try:
                assert first_started.wait(timeout=1)
                second.start()
                assert not second_finished.wait(timeout=0.1)
            finally:
                release_first.set()
                first.join(timeout=2)
                second.join(timeout=2)

        assert not first.is_alive()
        assert not second.is_alive()
        assert errors == []
        assert calls == 1
        assert len(results) == 2
        assert sum(result.skipped for result in results) == 1
        assert (
            count_binding_passes(
                tmp_path,
                delegation=DelegationKind.CODE_REVIEW,
                binding=prepared.binding,
            )
            == 1
        )


class TestAnnounceReviewPassBudget:
    def test_remaining_budget_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        rds._announce_review_pass_budget(1, 2, host_session=SessionKind.IMPLEMENTATION)
        text = _cap(capsys).lower()
        assert "review pass 1 of 2" in text
        assert "1 pass left" in text
        assert "done.max_review_passes" in text

    def test_cap_reached_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        rds._announce_review_pass_budget(2, 2, host_session=SessionKind.IMPLEMENTATION)
        text = _cap(capsys).lower()
        assert "review pass 2 of 2" in text
        assert "reached" in text

    def test_pr_comment_session_never_reports_the_implementation_cap(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """PR-comment review is uncapped; ``3 of 2`` must never be printed (#547)."""
        rds._announce_review_pass_budget(3, 2, host_session=SessionKind.REVIEW_PR_COMMENTS)
        text = _cap(capsys)
        assert "Review pass 3 recorded for the PR-comment review binding" in text
        assert "wade review-pr-comments-session done" in text
        assert "satisfying review of the current commit" in text
        assert "3 of 2" not in text
        assert "cap is now reached" not in text
        assert "without requiring re-review" not in text
        assert "implementation-session" not in text

    def test_standalone_review_reports_no_session_cap(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rds._announce_review_pass_budget(3, 2, host_session=None)
        text = _cap(capsys)
        assert "Review pass 3 recorded for this standalone review binding" in text
        assert "3 of 2" not in text
        assert "implementation-session" not in text

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

        announce.assert_called_once_with(2, 2, host_session=None)

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
        launch_attempted: bool = False,
    ) -> DelegationResult:
        return DelegationResult(
            success=False,
            feedback=feedback,
            mode=DelegationMode.HEADLESS,
            exit_code=1,
            never_launched=True,
            launch_attempted=launch_attempted,
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

    def test_a_launched_nonzero_exit_records_failed_without_approval(
        self, tmp_path: Path, review_preflight: PreparedDelegationMethod
    ) -> None:
        """A running reviewer spends budget without certifying completion."""
        ran_and_failed = DelegationResult(
            success=False,
            feedback="exited 3",
            mode=DelegationMode.HEADLESS,
            exit_code=3,
        )
        record, _ = self._run(tmp_path, ran_and_failed)

        record.assert_called_once()
        assert record.call_args.args[3].value == "failed"

    @pytest.mark.parametrize(
        "result",
        (
            DelegationResult(success=False, feedback="failure", mode=DelegationMode.PROMPT),
            DelegationResult(
                success=False, feedback="failure", mode=DelegationMode.INTERACTIVE, cancelled=True
            ),
            DelegationResult(
                success=True, feedback="skip", mode=DelegationMode.HEADLESS, skipped=True
            ),
        ),
    )
    def test_exempt_results_do_not_write_or_announce_a_pass(
        self,
        tmp_path: Path,
        review_preflight: PreparedDelegationMethod,
        result: DelegationResult,
    ) -> None:
        record, announce = self._run(tmp_path, result)
        record.assert_not_called()
        announce.assert_not_called()

    @pytest.mark.parametrize("mode", (DelegationMode.HEADLESS, DelegationMode.INTERACTIVE))
    def test_launched_failure_persists_once_without_certifying_review(
        self,
        tmp_path: Path,
        review_preflight: PreparedDelegationMethod,
        mode: DelegationMode,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Message shape cannot override the structured launch boundary.
        failed = DelegationResult(
            success=False, feedback="permission denied", mode=mode, exit_code=3
        )
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=tmp_path),
            patch.object(rds.git_repo, "diff_worktree", return_value="diff --git a b"),
            patch.object(rds, "_run_review_delegation", return_value=failed),
        ):
            for _ in range(2):
                assert rds.review_implementation() is failed
        receipt = read_review_record(
            tmp_path,
            delegation=DelegationKind.CODE_REVIEW,
            commit="a" * 40,
            binding=review_preflight.binding,
        )
        assert receipt is not None and receipt.outcome is ReviewOutcome.FAILED
        assert receipt.consumes_pass and not receipt.satisfies_review
        assert (
            count_binding_passes(
                tmp_path,
                delegation=DelegationKind.CODE_REVIEW,
                binding=review_preflight.binding,
            )
            == 1
        )
        text = _cap(capsys)
        assert "reviewer ran and failed" in text
        assert "Review pass 1 recorded for this standalone review binding" in text
        assert "1 of 2" not in text
        assert "reviewer never started" not in text

    def test_failed_receipt_write_does_not_claim_budget_or_persistence(
        self,
        tmp_path: Path,
        review_preflight: PreparedDelegationMethod,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        failed = DelegationResult(
            success=False, feedback="failure", mode=DelegationMode.HEADLESS, exit_code=1
        )
        with (
            patch.object(rds, "write_review_record", return_value=None),
            patch.object(rds, "_announce_review_pass_budget") as announce,
        ):
            rds._report_failed_review(
                tmp_path,
                "a" * 40,
                review_preflight,
                failed,
                max_passes=2,
            )
        announce.assert_not_called()
        text = _cap(capsys)
        assert "could not be persisted" in text
        assert "Could not confirm a failed-review receipt" in text
        assert "current failed receipt consumes" not in text

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
                "launch denied by sandbox policy",
                inherited_sandbox_profile_mismatch=True,
                launch_attempted=True,
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
                "launch denied by sandbox policy",
                inherited_sandbox_profile_mismatch=True,
                launch_attempted=True,
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


def _write_session(root: Path, session_kind: SessionKind) -> ResolvedBinding:
    """Freeze a real mapped session bundle whose REVIEW binding the command resolves."""

    session = root / ".wade" / "session"
    skill_dir = session / "skills" / "builtin" / "code-review"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (session / "WORKFLOW.md").write_text("# Workflow\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("# Code review methodology\n", encoding="utf-8")
    inspected = inspect_skill(skill_dir, project_root=session)
    binding = ResolvedBinding.from_skills(
        (
            ResolvedSkill(
                canonical_ref="builtin:code-review",
                source_path="templates/skills/code-review",
                materialized_path=".wade/session/skills/builtin/code-review",
                content_digest=inspected.digest,
                files=inspected.files,
            ),
        )
    )
    manifest = SessionManifest(
        session=session_kind,
        workflow_revision=SESSION_DEFINITIONS[session_kind].workflow_revision,
        bundle_digest=compute_session_bundle_digest(session),
        task_id="42",
        ai_command=(
            AICommandKey.REVIEW_PR_COMMENTS
            if session_kind is SessionKind.REVIEW_PR_COMMENTS
            else AICommandKey.IMPLEMENT
        ),
        bindings={SkillSlot.WORK: binding, SkillSlot.REVIEW: binding},
    )
    (session / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    return binding


class TestEmptyReviewerAssessment:
    """A zero-exit reviewer without an assessment is a launched failure (#547).

    These run the real mapped-binding preparation, assessment validation,
    receipt persistence and ``done`` review gate; only the external reviewer
    process and Git/PR lookups are replaced.
    """

    HEAD = "a" * 40

    def _review(
        self,
        root: Path,
        delegated: DelegationResult,
        *,
        limit: int = 2,
    ) -> tuple[DelegationResult, MagicMock]:
        config = ProjectConfig(
            ai=AIConfig(
                default_tool="claude",
                review_implementation=AICommandConfig(mode="headless", enabled=True),
            ),
            done=DoneConfig(max_review_passes=limit),
        )
        with (
            patch.object(rds.git_repo, "get_repo_root", return_value=root),
            patch.object(rds.git_repo, "rev_parse", return_value=self.HEAD),
            patch.object(
                rds,
                "_load_review_config",
                return_value=(config, config.ai.review_implementation),
            ),
            patch.object(rds, "_review_input_context", return_value=rds._ReviewInputContext()),
            patch.object(
                rds,
                "_collect_review_diffs",
                return_value=rds._ReviewDiffs(committed="diff --git a b", staged="", unstaged=""),
            ),
            patch.object(
                rds,
                "confirm_ai_selection",
                side_effect=lambda tool, model, **kw: (
                    tool,
                    model,
                    kw["resolved_effort"],
                    kw["resolved_permission_mode"],
                ),
            ),
            patch.object(rds, "delegate", return_value=delegated) as delegate,
        ):
            result = rds.review_implementation()
        return result, delegate

    def _gate(self, root: Path, session_kind: SessionKind, *, limit: int = 2) -> bool:
        return _gate_review_ran(
            ProjectConfig(done=DoneConfig(max_review_passes=limit)),
            root,
            self.HEAD,
            skip_review=False,
            session_type=session_kind.value,
        )

    @pytest.mark.parametrize(
        "session_kind", (SessionKind.IMPLEMENTATION, SessionKind.REVIEW_PR_COMMENTS)
    )
    @pytest.mark.parametrize("assessment", ("", "  \n\t \n"))
    def test_empty_assessment_records_a_failed_non_satisfying_pass(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        session_kind: SessionKind,
        assessment: str,
    ) -> None:
        binding = _write_session(tmp_path, session_kind)
        delegated = DelegationResult(
            success=True,
            feedback=assessment,
            mode=DelegationMode.HEADLESS,
            diagnostics="Headless session stderr:\nquota exhausted",
            relaunch_command="wade review implementation --mode headless --ai claude",
        )

        result, delegate = self._review(tmp_path, delegated)

        delegate.assert_called_once()
        assert result.success is False
        assert result.exit_code == 1
        assert result.empty_assessment is True
        assert result.never_launched is False
        assert result.completion_command is None
        receipt = read_review_record(
            tmp_path, delegation=DelegationKind.CODE_REVIEW, commit=self.HEAD, binding=binding
        )
        assert receipt is not None and receipt.outcome is ReviewOutcome.FAILED
        assert receipt.consumes_pass and not receipt.satisfies_review
        assert (
            count_binding_passes(tmp_path, delegation=DelegationKind.CODE_REVIEW, binding=binding)
            == 1
        )
        assert self._gate(tmp_path, session_kind) is False

        text = _cap(capsys)
        assert "returned no assessment output" in text
        assert "quota exhausted" in text
        assert "never substitute a self-review" in text
        assert "wade review implementation --mode headless --ai claude" in text
        assert "reviewer never started" not in text
        if session_kind is SessionKind.IMPLEMENTATION:
            assert "Review pass 1 of 2 recorded" in text
        else:
            assert "Review pass 1 recorded for the PR-comment review binding" in text
            assert "1 of 2" not in text
            assert "implementation-session" not in text

    def test_empty_assessment_only_passes_through_the_explicit_implementation_cap(
        self, tmp_path: Path
    ) -> None:
        """The cap remains explicit policy; the failed receipt itself never satisfies."""
        _write_session(tmp_path, SessionKind.IMPLEMENTATION)
        empty = DelegationResult(success=True, feedback="", mode=DelegationMode.HEADLESS)

        self._review(tmp_path, empty, limit=1)

        assert self._gate(tmp_path, SessionKind.IMPLEMENTATION, limit=2) is False
        assert self._gate(tmp_path, SessionKind.IMPLEMENTATION, limit=1) is True

    @pytest.mark.parametrize(
        ("session_kind", "completion"),
        (
            (SessionKind.IMPLEMENTATION, "wade implementation-session done"),
            (SessionKind.REVIEW_PR_COMMENTS, "wade review-pr-comments-session done"),
        ),
    )
    def test_explicit_no_findings_assessment_satisfies_review(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        session_kind: SessionKind,
        completion: str,
    ) -> None:
        binding = _write_session(tmp_path, session_kind)
        delegated = DelegationResult(
            success=True, feedback="No findings.", mode=DelegationMode.HEADLESS
        )

        result, _ = self._review(tmp_path, delegated)

        assert result.success is True
        assert result.feedback == "No findings."
        assert result.completion_command == completion
        receipt = read_review_record(
            tmp_path, delegation=DelegationKind.CODE_REVIEW, commit=self.HEAD, binding=binding
        )
        assert receipt is not None and receipt.outcome is ReviewOutcome.REVIEWED
        assert self._gate(tmp_path, session_kind) is True
        assert "No findings." in _cap(capsys)

    def test_pr_comment_review_beyond_the_implementation_cap_still_dispatches(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        binding = _write_session(tmp_path, SessionKind.REVIEW_PR_COMMENTS)
        for commit in ("1" * 40, "2" * 40):
            assert (
                write_review_record(
                    tmp_path,
                    delegation=DelegationKind.CODE_REVIEW,
                    commit=commit,
                    binding=binding,
                    outcome=ReviewOutcome.REVIEWED,
                )
                is not None
            )
        delegated = DelegationResult(
            success=True, feedback="One finding.", mode=DelegationMode.HEADLESS
        )

        result, delegate = self._review(tmp_path, delegated, limit=2)

        delegate.assert_called_once()
        assert result.success is True and result.skipped is False
        text = _cap(capsys)
        assert "Review pass 3 recorded for the PR-comment review binding" in text
        assert "3 of 2" not in text
        assert "cap is now reached" not in text
        assert "implementation-session done" not in text
        assert self._gate(tmp_path, SessionKind.REVIEW_PR_COMMENTS) is True


class TestRequireReviewAssessment:
    @pytest.mark.parametrize(
        "result",
        (
            DelegationResult(success=True, feedback="Findings", mode=DelegationMode.HEADLESS),
            DelegationResult(success=True, feedback="Review this.", mode=DelegationMode.PROMPT),
            DelegationResult(success=True, feedback="", mode=DelegationMode.HEADLESS, skipped=True),
            DelegationResult(
                success=False,
                feedback="<no output before the budget elapsed>",
                mode=DelegationMode.HEADLESS,
                exit_code=1,
                timed_out=True,
            ),
            DelegationResult(
                success=False,
                feedback="Unknown AI tool: x",
                mode=DelegationMode.HEADLESS,
                exit_code=1,
                never_launched=True,
            ),
        ),
    )
    def test_non_empty_or_non_success_results_are_unchanged(self, result: DelegationResult) -> None:
        assert rds._require_review_assessment(result) is result

    @pytest.mark.parametrize("mode", (DelegationMode.HEADLESS, DelegationMode.INTERACTIVE))
    def test_empty_success_becomes_an_idempotent_launched_failure(
        self, mode: DelegationMode
    ) -> None:
        converted = rds._require_review_assessment(
            DelegationResult(success=True, feedback=" \n", mode=mode)
        )
        assert converted.success is False
        assert converted.exit_code == 1
        assert converted.empty_assessment is True
        assert converted.never_launched is False
        assert converted.mode is mode
        assert converted.feedback == rds._EMPTY_ASSESSMENT_FEEDBACK
        assert rds._require_review_assessment(converted) is converted
