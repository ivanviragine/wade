"""Safe persisted context tests for PR-comment review cycles."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wade.models.review_cycle import ReviewCycleContext
from wade.services import review_cycle_service
from wade.services.review_cycle_service import (
    clear_review_cycle,
    initialize_or_refresh_review_cycle,
    read_review_cycle,
)
from wade.utils.safe_state import MAX_STATE_FILE_BYTES

BASELINE = "a" * 40
LATER_HEAD = "b" * 40


def _thread(thread_id: str, feedback: str) -> str:
    return f"### Comment\n\n**Thread ID:** `{thread_id}`\n\n{feedback}"


def test_cycle_refresh_preserves_original_baseline_and_accumulates_new_feedback(
    tmp_path: Path,
) -> None:
    first = _thread("first", "First requested fix")
    second = _thread("second", "Second requested fix")
    created = initialize_or_refresh_review_cycle(
        tmp_path,
        issue_number="42",
        pr_number=99,
        baseline_commit=BASELINE,
        feedback=f"{first}\n\n{second}",
    )
    assert created.context is not None
    assert created.context.baseline_commit == BASELINE

    third = _thread("third", "Newly requested fix")
    refreshed = initialize_or_refresh_review_cycle(
        tmp_path,
        issue_number="42",
        pr_number=99,
        baseline_commit=LATER_HEAD,
        feedback=f"{second}\r\n\r\n{third}",
    )
    assert refreshed.context is not None
    assert refreshed.context.baseline_commit == BASELINE
    assert refreshed.context.feedback.count("First requested fix") == 1
    assert refreshed.context.feedback.count("Second requested fix") == 1
    assert refreshed.context.feedback.count("Newly requested fix") == 1


def test_cycle_accepts_unicode_feedback_at_serialized_payload_byte_limit(tmp_path: Path) -> None:
    template = ReviewCycleContext(
        issue_number="42",
        pr_number=99,
        baseline_commit=BASELINE,
        feedback="x",
    )
    fixed_bytes = len(template.serialized_payload()) - 1
    emoji_bytes = len(b"\\ud83d\\ude00")
    emoji_count, ascii_bytes = divmod(MAX_STATE_FILE_BYTES - fixed_bytes, emoji_bytes)
    feedback = "😀" * emoji_count + "x" * ascii_bytes

    context = ReviewCycleContext(
        issue_number="42",
        pr_number=99,
        baseline_commit=BASELINE,
        feedback=feedback,
    )
    assert len(context.serialized_payload()) == MAX_STATE_FILE_BYTES

    created = initialize_or_refresh_review_cycle(
        tmp_path,
        issue_number="42",
        pr_number=99,
        baseline_commit=BASELINE,
        feedback=feedback,
    )
    assert created.context is not None
    state_path = tmp_path / ".wade" / "review-cycles" / "review-cycle@42.json"
    assert state_path.stat().st_size == MAX_STATE_FILE_BYTES

    rejected = initialize_or_refresh_review_cycle(
        tmp_path / "oversized",
        issue_number="42",
        pr_number=99,
        baseline_commit=BASELINE,
        feedback=f"{feedback}x",
    )
    assert rejected.context is None
    assert rejected.diagnostic is not None
    assert "serialized payload exceeds" in rejected.diagnostic


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


def test_failed_refresh_invalidates_prior_cycle_before_the_next_review(tmp_path: Path) -> None:
    assert (
        initialize_or_refresh_review_cycle(
            tmp_path,
            issue_number="42",
            pr_number=99,
            baseline_commit=BASELINE,
            feedback="First feedback",
        ).context
        is not None
    )

    write_state = review_cycle_service.atomic_write_state_file
    with patch(
        "wade.services.review_cycle_service.atomic_write_state_file",
        side_effect=lambda root, directories, filename, data: (
            False
            if directories == ("review-cycles",)
            else write_state(root, directories, filename, data)
        ),
    ):
        failed = initialize_or_refresh_review_cycle(
            tmp_path,
            issue_number="42",
            pr_number=99,
            baseline_commit=LATER_HEAD,
            feedback="New feedback",
        )

    assert failed.context is None
    assert failed.diagnostic is not None
    assert "invalidated" in failed.diagnostic
    stale = read_review_cycle(tmp_path, issue_number="42", pr_number=99)
    assert stale.context is None

    fresh = initialize_or_refresh_review_cycle(
        tmp_path,
        issue_number="42",
        pr_number=99,
        baseline_commit=LATER_HEAD,
        feedback="New feedback",
    )
    assert fresh.context is not None
    assert fresh.context.baseline_commit == LATER_HEAD
    assert fresh.context.feedback == "New feedback"


def test_failed_cycle_cleanup_invalidates_readable_state(tmp_path: Path) -> None:
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
    with patch("wade.services.review_cycle_service.delete_state_file", return_value=False):
        assert clear_review_cycle(tmp_path, issue_number="42")
        lookup = read_review_cycle(tmp_path, issue_number="42", pr_number=99)
        assert lookup.context is None
        assert lookup.diagnostic is not None
        assert "invalidated" in lookup.diagnostic

    fresh = initialize_or_refresh_review_cycle(
        tmp_path,
        issue_number="42",
        pr_number=99,
        baseline_commit=LATER_HEAD,
        feedback="Later feedback",
    )
    assert fresh.context is not None
    assert fresh.context.baseline_commit == LATER_HEAD
    assert fresh.context.feedback == "Later feedback"
