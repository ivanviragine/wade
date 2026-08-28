"""Binding-aware review record and completion-gate tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from wade.models.config import ProjectConfig
from wade.models.session_manifest import (
    ResolvedBinding,
    ReviewBinding,
    ReviewOutcome,
    ReviewRecord,
    SessionManifest,
)
from wade.models.skill import ResolvedSkill, SkillSlot
from wade.models.workflow import AICommandKey, DelegationKind, SessionKind
from wade.services.implementation_service.done import _classify_review
from wade.services.implementation_service.lifecycle import ReviewStatusKind
from wade.services.review_record_service import (
    count_binding_passes,
    list_review_records,
    read_review_record,
    review_record_filename,
    write_review_record,
)

HEAD = "a" * 40


def _binding(name: str, digest_char: str) -> ResolvedBinding:
    skill = ResolvedSkill(
        canonical_ref=f"project:{name}",
        source_path=f".agents/skills/{name}",
        materialized_path=f".wade/session/skills/project/agents/{name}",
        content_digest=f"sha256:{digest_char * 64}",
        files=("SKILL.md",),
    )
    return ResolvedBinding.from_skills((skill,))


def _write_manifest(
    root: Path,
    *,
    work: ResolvedBinding,
    review: ResolvedBinding,
) -> None:
    manifest = SessionManifest(
        session=SessionKind.IMPLEMENTATION,
        workflow_revision=1,
        task_id="123",
        ai_command=AICommandKey.IMPLEMENT,
        bindings={SkillSlot.WORK: work, SkillSlot.REVIEW: review},
    )
    session = root / ".wade" / "session"
    session.mkdir(parents=True, exist_ok=True)
    (session / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")


def test_outcome_pass_semantics_are_schema_enforced() -> None:
    binding = _binding("review-a", "1")
    with pytest.raises(ValidationError, match="consumes_pass"):
        ReviewRecord(
            commit=HEAD,
            delegation=DelegationKind.CODE_REVIEW,
            outcome=ReviewOutcome.NO_DIFF,
            consumes_pass=True,
            binding=ReviewBinding.from_resolved(binding),
        )


def test_record_is_idempotent_promotable_and_never_downgraded(tmp_path: Path) -> None:
    binding = _binding("review-a", "1")
    timed_out = write_review_record(
        tmp_path,
        delegation=DelegationKind.CODE_REVIEW,
        commit=HEAD,
        binding=binding,
        outcome=ReviewOutcome.TIMED_OUT,
    )
    assert timed_out is not None and timed_out.outcome is ReviewOutcome.TIMED_OUT
    assert (
        count_binding_passes(tmp_path, delegation=DelegationKind.CODE_REVIEW, binding=binding) == 1
    )

    reviewed = write_review_record(
        tmp_path,
        delegation=DelegationKind.CODE_REVIEW,
        commit=HEAD,
        binding=binding,
        outcome=ReviewOutcome.REVIEWED,
    )
    assert reviewed is not None and reviewed.outcome is ReviewOutcome.REVIEWED

    retained = write_review_record(
        tmp_path,
        delegation=DelegationKind.CODE_REVIEW,
        commit=HEAD,
        binding=binding,
        outcome=ReviewOutcome.TIMED_OUT,
    )
    assert retained is not None and retained.outcome is ReviewOutcome.REVIEWED
    assert len(list_review_records(tmp_path)) == 1


def test_no_diff_satisfies_without_consuming_a_pass(tmp_path: Path) -> None:
    binding = _binding("review-a", "1")
    record = write_review_record(
        tmp_path,
        delegation=DelegationKind.CODE_REVIEW,
        commit=HEAD,
        binding=binding,
        outcome=ReviewOutcome.NO_DIFF,
    )
    assert record is not None and record.satisfies_review
    assert (
        count_binding_passes(tmp_path, delegation=DelegationKind.CODE_REVIEW, binding=binding) == 0
    )


def test_filename_body_mismatch_and_malformed_json_are_absent(tmp_path: Path) -> None:
    binding = _binding("review-a", "1")
    reviews = tmp_path / ".wade" / "reviews"
    reviews.mkdir(parents=True)
    wrong_name = review_record_filename(DelegationKind.CODE_REVIEW, "b" * 40, binding.digest)
    body = ReviewRecord(
        commit=HEAD,
        delegation=DelegationKind.CODE_REVIEW,
        outcome=ReviewOutcome.REVIEWED,
        consumes_pass=True,
        binding=ReviewBinding.from_resolved(binding),
    )
    (reviews / wrong_name).write_text(body.model_dump_json(), encoding="utf-8")
    (reviews / "review@broken.json").write_text("{", encoding="utf-8")
    assert list_review_records(tmp_path) == ()


def test_symlinked_reviews_directory_fails_closed(tmp_path: Path) -> None:
    binding = _binding("review-a", "1")
    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / ".wade").mkdir()
    (tmp_path / ".wade" / "reviews").symlink_to(external, target_is_directory=True)
    assert (
        write_review_record(
            tmp_path,
            delegation=DelegationKind.CODE_REVIEW,
            commit=HEAD,
            binding=binding,
            outcome=ReviewOutcome.REVIEWED,
        )
        is None
    )
    assert not list(external.iterdir())


def test_changed_reviewer_is_visible_and_old_passes_do_not_count(
    tmp_path: Path,
) -> None:
    work = _binding("implementation", "3")
    reviewer_a = _binding("review-a", "1")
    reviewer_b = _binding("review-b", "2")
    write_review_record(
        tmp_path,
        delegation=DelegationKind.CODE_REVIEW,
        commit=HEAD,
        binding=reviewer_a,
        outcome=ReviewOutcome.REVIEWED,
    )
    _write_manifest(tmp_path, work=work, review=reviewer_b)

    status = _classify_review(ProjectConfig(), tmp_path, HEAD, skip_review=False)
    assert status.kind is ReviewStatusKind.REVIEWER_CHANGED
    assert status.passes == 0


def test_a_to_b_to_a_reuses_receipt_and_work_only_change_preserves_it(
    tmp_path: Path,
) -> None:
    work_a = _binding("implementation-a", "3")
    work_b = _binding("implementation-b", "4")
    reviewer_a = _binding("review-a", "1")
    reviewer_b = _binding("review-b", "2")
    write_review_record(
        tmp_path,
        delegation=DelegationKind.CODE_REVIEW,
        commit=HEAD,
        binding=reviewer_a,
        outcome=ReviewOutcome.REVIEWED,
    )

    _write_manifest(tmp_path, work=work_b, review=reviewer_a)
    assert (
        _classify_review(ProjectConfig(), tmp_path, HEAD, skip_review=False).kind
        is ReviewStatusKind.REVIEWED
    )

    _write_manifest(tmp_path, work=work_a, review=reviewer_b)
    assert (
        _classify_review(ProjectConfig(), tmp_path, HEAD, skip_review=False).kind
        is ReviewStatusKind.REVIEWER_CHANGED
    )

    _write_manifest(tmp_path, work=work_b, review=reviewer_a)
    assert (
        _classify_review(ProjectConfig(), tmp_path, HEAD, skip_review=False).kind
        is ReviewStatusKind.REVIEWED
    )


def test_legacy_markers_are_ignored_when_versioned_manifest_is_invalid(
    tmp_path: Path,
) -> None:
    from wade.utils import markers

    markers.write_marker(tmp_path, "reviewed", HEAD)
    session = tmp_path / ".wade" / "session"
    session.mkdir()
    (session / "manifest.json").write_text(json.dumps({"schema_version": 999}))

    status = _classify_review(ProjectConfig(), tmp_path, HEAD, skip_review=False)
    assert status.kind is ReviewStatusKind.NOT_REVIEWED


def test_exact_record_reader_requires_the_requested_binding(tmp_path: Path) -> None:
    reviewer_a = _binding("review-a", "1")
    reviewer_b = _binding("review-b", "2")
    write_review_record(
        tmp_path,
        delegation=DelegationKind.CODE_REVIEW,
        commit=HEAD,
        binding=reviewer_a,
        outcome=ReviewOutcome.REVIEWED,
    )
    assert (
        read_review_record(
            tmp_path,
            delegation=DelegationKind.CODE_REVIEW,
            commit=HEAD,
            binding=reviewer_b,
        )
        is None
    )
