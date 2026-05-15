"""Settle-window helpers for review polling.

Pure functions that compute the effective settle wait based on a
``PRReviewStatus`` snapshot.  Lives in the service layer (not models) because
it encodes polling/business logic, not data structure.
"""

from __future__ import annotations

from datetime import UTC, datetime

from wade.models.review import PRReviewStatus, ReviewBotStatus


def latest_signal_ts(status: PRReviewStatus) -> datetime | None:
    """Return the newest timestamp across all thread comments and reviews.

    Considers ``created_at`` from every comment in
    ``effective_unresolved_threads`` and ``submitted_at`` from every entry in
    ``reviews``.  Naive datetimes are treated as UTC, matching
    ``is_commit_fresh()``.  Returns ``None`` when no timestamps are available.
    """
    candidates: list[datetime] = []
    for thread in status.effective_unresolved_threads:
        for comment in thread.comments:
            if comment.created_at is not None:
                ts = comment.created_at
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                candidates.append(ts)
    for review in status.reviews:
        if review.submitted_at is not None:
            ts = review.submitted_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            candidates.append(ts)
    return max(candidates) if candidates else None


def compute_effective_settle(
    status: PRReviewStatus,
    settle: int,
    poll_interval: int,
    now: datetime,
    latest: datetime | None,
) -> int:
    """Compute the effective settle wait in seconds based on signal age.

    Decision matrix (see PLAN.md for full table):
    - No timestamps available → ``settle`` (conservative fallback)
    - ``bot_status == PAUSED`` → ``settle`` (bot may resume and post more)
    - Newest signal age ≥ ``settle`` → 0 (burst provably over)
    - bot None/COMPLETED + no pending reviewers + age >= 2*poll_interval → 0
    - Otherwise → ``max(0, settle - age)``

    ``now`` must be a UTC-aware datetime; the call site uses
    ``datetime.now(UTC)``.  ``latest`` is the pre-computed result of
    ``latest_signal_ts(status)`` — passed in to avoid a second iteration and
    to keep logging values at the call site consistent with the decision.
    This function is pure — no clock or I/O.
    """
    if latest is None:
        return settle

    now_aware = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    age = max(0, int((now_aware - latest).total_seconds()))

    if status.bot_status == ReviewBotStatus.PAUSED:
        return settle

    if age >= settle:
        return 0

    if (
        status.bot_status in (None, ReviewBotStatus.COMPLETED)
        and not status.pending_reviewers
        and age >= 2 * poll_interval
    ):
        return 0

    return settle - age
