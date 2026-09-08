"""Safe persisted context tests for PR-comment review cycles."""

from __future__ import annotations

from pathlib import Path

from wade.services.review_cycle_service import (
    clear_review_cycle,
    initialize_or_refresh_review_cycle,
    read_review_cycle,
)

BASELINE = "a" * 40
LATER_HEAD = "b" * 40


def test_cycle_refresh_preserves_original_baseline_and_updates_feedback(tmp_path: Path) -> None:
    created = initialize_or_refresh_review_cycle(
        tmp_path,
        issue_number="42",
        pr_number=99,
        baseline_commit=BASELINE,
        feedback="First requested fix",
    )
    assert created.context is not None
    assert created.context.baseline_commit == BASELINE

    refreshed = initialize_or_refresh_review_cycle(
        tmp_path,
        issue_number="42",
        pr_number=99,
        baseline_commit=LATER_HEAD,
        feedback="Refreshed requested fix\r\nwith detail",
    )
    assert refreshed.context is not None
    assert refreshed.context.baseline_commit == BASELINE
    assert refreshed.context.feedback == "Refreshed requested fix\nwith detail"


def test_cycle_rejects_mismatched_pr_identity(tmp_path: Path) -> None:
    assert (
        initialize_or_refresh_review_cycle(
            tmp_path,
            issue_number="42",
            pr_number=99,
            baseline_commit=BASELINE,
            feedback="Fix this",
        ).context
        is not None
    )

    mismatched = read_review_cycle(tmp_path, issue_number="42", pr_number=100)
    assert mismatched.context is None
    assert mismatched.diagnostic is not None
    assert "PR identity" in mismatched.diagnostic


def test_unsafe_cycle_directory_is_diagnostic_and_never_written_through(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / ".wade").mkdir()
    (tmp_path / ".wade" / "review-cycles").symlink_to(external, target_is_directory=True)

    result = initialize_or_refresh_review_cycle(
        tmp_path,
        issue_number="42",
        pr_number=99,
        baseline_commit=BASELINE,
        feedback="Fix this",
    )

    assert result.context is None
    assert result.diagnostic is not None
    assert not list(external.iterdir())


def test_successful_cycle_cleanup_allows_a_later_fresh_cycle(tmp_path: Path) -> None:
    assert (
        initialize_or_refresh_review_cycle(
            tmp_path,
            issue_number="42",
            pr_number=99,
            baseline_commit=BASELINE,
            feedback="Fix this",
        ).context
        is not None
    )

    assert clear_review_cycle(tmp_path, issue_number="42")
    assert read_review_cycle(tmp_path, issue_number="42", pr_number=99).context is None

    later = initialize_or_refresh_review_cycle(
        tmp_path,
        issue_number="42",
        pr_number=99,
        baseline_commit=LATER_HEAD,
        feedback="A new batch of feedback",
    )
    assert later.context is not None
    assert later.context.baseline_commit == LATER_HEAD
