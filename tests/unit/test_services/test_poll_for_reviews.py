"""Unit tests for review_service.poll_for_reviews()."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.models.review import (
    PendingReviewer,
    PollOutcome,
    PRReviewStatus,
    ReviewBotStatus,
    ReviewComment,
    ReviewThread,
)
from wade.services.review_service import poll_for_reviews

_SLEEP = "wade.services.review_service.time.sleep"
_TIME = "wade.services.review_service.time.time"
_STATUS = "wade.services.review_service.get_comprehensive_review_status"
_GET_PR = "wade.services.review_service.git_pr.get_pr_for_branch"


def _make_thread() -> ReviewThread:
    return ReviewThread(
        id="t1",
        is_resolved=False,
        comments=[ReviewComment(author="alice", body="please fix this")],
    )


def _provider() -> MagicMock:
    return MagicMock()


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_human_comments_settle_120s(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """Human reviewer comment found — should settle for 120s and return COMMENTS_FOUND."""
    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    thread = _make_thread()
    mock_status.return_value = PRReviewStatus(
        actionable_threads=[thread],
        all_unresolved_threads=[thread],
        bot_status=None,
    )

    result = poll_for_reviews(
        _provider(),
        tmp_path,
        42,
        "feat/42-test",
        poll_interval=60,
        bot_settle=60,
        human_settle=120,
    )

    assert result == PollOutcome.COMMENTS_FOUND
    mock_sleep.assert_called_once_with(120)


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_bot_comments_settle_60s(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """Bot reviewer comment found — should settle for 60s and return COMMENTS_FOUND."""
    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    thread = _make_thread()
    mock_status.return_value = PRReviewStatus(
        actionable_threads=[thread],
        all_unresolved_threads=[thread],
        bot_status=ReviewBotStatus.PAUSED,
    )

    result = poll_for_reviews(
        _provider(),
        tmp_path,
        42,
        "feat/42-test",
        poll_interval=60,
        bot_settle=60,
        human_settle=120,
    )

    assert result == PollOutcome.COMMENTS_FOUND
    mock_sleep.assert_called_once_with(60)


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_pr_merged_returns_pr_closed(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """PR merged externally — should return PR_CLOSED without checking status."""
    mock_get_pr.return_value = {"number": 42, "state": "MERGED"}

    result = poll_for_reviews(_provider(), tmp_path, 42, "feat/42-test")

    assert result == PollOutcome.PR_CLOSED
    mock_status.assert_not_called()
    mock_sleep.assert_not_called()


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_pr_closed_returns_pr_closed(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    _mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """PR closed externally — should return PR_CLOSED."""
    mock_get_pr.return_value = {"number": 42, "state": "CLOSED"}

    result = poll_for_reviews(_provider(), tmp_path, 42, "feat/42-test")

    assert result == PollOutcome.PR_CLOSED
    mock_status.assert_not_called()


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_bot_in_progress_keeps_polling_then_finds_comments(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """Bot IN_PROGRESS should keep polling, then detect comments after bot finishes."""
    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    thread = _make_thread()
    mock_status.side_effect = [
        PRReviewStatus(bot_status=ReviewBotStatus.IN_PROGRESS),
        PRReviewStatus(
            actionable_threads=[thread],
            all_unresolved_threads=[thread],
            bot_status=ReviewBotStatus.PAUSED,
        ),
    ]

    result = poll_for_reviews(
        _provider(),
        tmp_path,
        42,
        "feat/42-test",
        poll_interval=60,
        bot_settle=60,
        human_settle=120,
    )

    assert result == PollOutcome.COMMENTS_FOUND
    # sleep called twice: once for IN_PROGRESS wait, once for settle
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(60)


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_transient_fetch_failure_keeps_polling(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """Transient fetch failure should keep polling and eventually find comments."""
    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    thread = _make_thread()
    mock_status.side_effect = [
        PRReviewStatus(fetch_failed=True),
        PRReviewStatus(
            actionable_threads=[thread], all_unresolved_threads=[thread], bot_status=None
        ),
    ]

    result = poll_for_reviews(
        _provider(),
        tmp_path,
        42,
        "feat/42-test",
        poll_interval=60,
        bot_settle=60,
        human_settle=120,
    )

    assert result == PollOutcome.COMMENTS_FOUND
    assert mock_sleep.call_count == 2


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_keyboard_interrupt_returns_interrupted(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    _mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """KeyboardInterrupt during status check should be caught and return INTERRUPTED."""
    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    mock_status.side_effect = KeyboardInterrupt

    result = poll_for_reviews(_provider(), tmp_path, 42, "feat/42-test")

    assert result == PollOutcome.INTERRUPTED


@patch(_SLEEP)
@patch(_TIME)
@patch(_STATUS)
@patch(_GET_PR)
def test_quiet_timeout_after_old_commit(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    mock_time: MagicMock,
    _mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """After quiet_timeout seconds with no comments (old commit), return QUIET_TIMEOUT."""
    from datetime import datetime, timedelta

    old_commit_time = datetime.now(UTC) - timedelta(minutes=30)
    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    # Each call returns no comments, commit is old
    mock_status.return_value = PRReviewStatus(
        actionable_threads=[],
        bot_status=None,
        latest_commit_pushed_at=old_commit_time,
    )
    # Simulate time progressing: first call sets quiet_start, second call triggers timeout
    mock_time.side_effect = [100.0, 800.0]  # elapsed = 700s > quiet_timeout=600

    result = poll_for_reviews(
        _provider(),
        tmp_path,
        42,
        "feat/42-test",
        poll_interval=60,
        quiet_timeout=600,
    )

    assert result == PollOutcome.QUIET_TIMEOUT


@patch(_SLEEP)
@patch(_TIME)
@patch(_STATUS)
@patch(_GET_PR)
def test_fresh_commit_resets_quiet_timer(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    mock_time: MagicMock,
    _mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """A fresh commit keeps the quiet timer reset; old commit lets it advance."""
    from datetime import datetime, timedelta

    fresh_commit = datetime.now(UTC) - timedelta(seconds=30)
    old_commit = datetime.now(UTC) - timedelta(minutes=30)

    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    # First two calls: fresh commit → reset timer; third: old + elapsed → timeout
    mock_status.side_effect = [
        PRReviewStatus(bot_status=None, latest_commit_pushed_at=fresh_commit),
        PRReviewStatus(bot_status=None, latest_commit_pushed_at=old_commit),
        PRReviewStatus(bot_status=None, latest_commit_pushed_at=old_commit),
    ]
    # time.time() called twice: once to set quiet_start, once to check elapsed
    mock_time.side_effect = [100.0, 800.0]

    result = poll_for_reviews(
        _provider(),
        tmp_path,
        42,
        "feat/42-test",
        poll_interval=60,
        quiet_timeout=600,
    )

    assert result == PollOutcome.QUIET_TIMEOUT


# ---------------------------------------------------------------------------
# New tests: outdated threads and PR-level changes_requested
# ---------------------------------------------------------------------------


def _make_outdated_thread() -> ReviewThread:
    return ReviewThread(
        id="t_outdated",
        is_resolved=False,
        is_outdated=True,
        comments=[ReviewComment(author="alice", body="please fix this")],
    )


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_outdated_threads_return_comments_found(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """Outdated-but-unresolved threads should be detected and return COMMENTS_FOUND."""
    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    outdated = _make_outdated_thread()
    mock_status.return_value = PRReviewStatus(
        actionable_threads=[],
        all_unresolved_threads=[outdated],
        bot_status=None,
    )

    result = poll_for_reviews(
        _provider(),
        tmp_path,
        42,
        "feat/42-test",
        poll_interval=60,
        bot_settle=60,
        human_settle=120,
    )

    assert result == PollOutcome.COMMENTS_FOUND
    mock_sleep.assert_called_once_with(120)


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_changes_requested_no_threads_returns_comments_found(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """PR-level CHANGES_REQUESTED with no inline threads should return COMMENTS_FOUND."""
    from wade.models.review import PRReview, ReviewState

    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    mock_status.return_value = PRReviewStatus(
        actionable_threads=[],
        all_unresolved_threads=[],
        reviews=[PRReview(author="bob", state=ReviewState.CHANGES_REQUESTED)],
        bot_status=None,
    )

    result = poll_for_reviews(
        _provider(),
        tmp_path,
        42,
        "feat/42-test",
        poll_interval=60,
        bot_settle=60,
        human_settle=120,
    )

    assert result == PollOutcome.COMMENTS_FOUND
    mock_sleep.assert_called_once_with(120)


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_bot_completed_with_outdated_threads_returns_comments_found(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    _mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """Bot completed but outdated threads remain — COMMENTS_FOUND, not REVIEW_COMPLETE."""
    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    outdated = _make_outdated_thread()
    mock_status.return_value = PRReviewStatus(
        actionable_threads=[],
        all_unresolved_threads=[outdated],
        bot_status=ReviewBotStatus.COMPLETED,
    )

    result = poll_for_reviews(
        _provider(),
        tmp_path,
        42,
        "feat/42-test",
        poll_interval=60,
        bot_settle=60,
        human_settle=120,
    )

    assert result == PollOutcome.COMMENTS_FOUND


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_bot_completed_with_changes_requested_returns_comments_found(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    _mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """Bot completed + CHANGES_REQUESTED with no threads — should return COMMENTS_FOUND."""
    from wade.models.review import PRReview, ReviewState

    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    mock_status.return_value = PRReviewStatus(
        actionable_threads=[],
        all_unresolved_threads=[],
        reviews=[PRReview(author="alice", state=ReviewState.CHANGES_REQUESTED)],
        bot_status=ReviewBotStatus.COMPLETED,
    )

    result = poll_for_reviews(
        _provider(),
        tmp_path,
        42,
        "feat/42-test",
        poll_interval=60,
        bot_settle=60,
        human_settle=120,
    )

    assert result == PollOutcome.COMMENTS_FOUND


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_bot_completed_no_signals_returns_review_complete(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """Bot completed with no threads and no changes_requested — REVIEW_COMPLETE."""
    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    mock_status.return_value = PRReviewStatus(
        actionable_threads=[],
        all_unresolved_threads=[],
        bot_status=ReviewBotStatus.COMPLETED,
        pending_reviewers=[],
    )

    result = poll_for_reviews(_provider(), tmp_path, 42, "feat/42-test")

    assert result == PollOutcome.REVIEW_COMPLETE
    mock_sleep.assert_not_called()


@patch(_SLEEP)
@patch(_TIME)
@patch(_STATUS)
@patch(_GET_PR)
def test_bot_completed_with_pending_reviewers_keeps_polling(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    mock_time: MagicMock,
    _mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """Bot completed but pending reviewers exist — loop continues, not REVIEW_COMPLETE.

    Regression test for issue #268: polling should continue when CodeRabbit
    completes with no actionable comments but other reviewers are still pending
    (e.g. @copilot-pull-request-reviewer).
    """
    from datetime import datetime, timedelta

    old_commit_time = datetime.now(UTC) - timedelta(minutes=30)
    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    mock_status.return_value = PRReviewStatus(
        actionable_threads=[],
        all_unresolved_threads=[],
        bot_status=ReviewBotStatus.COMPLETED,
        pending_reviewers=[PendingReviewer(name="copilot-pull-request-reviewer", is_team=False)],
        latest_commit_pushed_at=old_commit_time,
    )
    # Simulate quiet timeout trigger: first call sets quiet_start, second triggers timeout
    mock_time.side_effect = [100.0, 800.0]  # elapsed = 700s > quiet_timeout=600

    result = poll_for_reviews(
        _provider(),
        tmp_path,
        42,
        "feat/42-test",
        poll_interval=60,
        quiet_timeout=600,
    )

    # Should return QUIET_TIMEOUT (not REVIEW_COMPLETE) — polling continued until timeout
    assert result == PollOutcome.QUIET_TIMEOUT


@patch(_SLEEP)
@patch(_STATUS)
@patch(_GET_PR)
def test_actionable_only_no_all_unresolved_threads(
    mock_get_pr: MagicMock,
    mock_status: MagicMock,
    mock_sleep: MagicMock,
    tmp_path: Path,
) -> None:
    """Compat: provider sets only actionable_threads (all_unresolved_threads empty).

    Polling must still detect the thread and return COMMENTS_FOUND, exercising
    the _effective_unresolved_threads fallback path.
    """
    mock_get_pr.return_value = {"number": 42, "state": "OPEN"}
    thread = _make_thread()
    # Simulate a legacy provider that only populates actionable_threads
    mock_status.return_value = PRReviewStatus(
        actionable_threads=[thread],
        # all_unresolved_threads intentionally omitted — defaults to []
        bot_status=None,
    )

    result = poll_for_reviews(
        _provider(),
        tmp_path,
        42,
        "feat/42-test",
        poll_interval=60,
        bot_settle=60,
        human_settle=120,
    )

    assert result == PollOutcome.COMMENTS_FOUND
    mock_sleep.assert_called_once_with(120)
