"""Review domain models — ReviewComment, ReviewThread, and formatting helpers.

Used by the ``review pr-comments`` flow to represent unresolved PR review
threads and render them as structured markdown for AI consumption.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel


def _as_utc(ts: datetime) -> datetime:
    """Treat a naive datetime as UTC; leave aware datetimes untouched.

    Shared normalization for every signal-timestamp comparison in this module
    (``is_commit_fresh``, ``latest_signal_ts``, ``latest_bot_signal_ts``,
    ``review_covers_latest_commit``) so they stay consistent.
    """
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


class ReviewComment(BaseModel):
    """A single comment within a PR review thread."""

    author: str = ""
    body: str = ""
    path: str | None = None
    line: int | None = None
    created_at: datetime | None = None
    url: str | None = None


class ReviewThread(BaseModel):
    """A PR review thread — a group of comments on the same code location."""

    id: str = ""
    is_resolved: bool = False
    is_outdated: bool = False
    comments: list[ReviewComment] = []

    @property
    def first_comment(self) -> ReviewComment | None:
        """The thread-starting comment (convenience accessor)."""
        return self.comments[0] if self.comments else None


# ---------------------------------------------------------------------------
# Review bot status detection
# ---------------------------------------------------------------------------


class PRComment(BaseModel):
    """A PR-level issue comment (not a code-review thread comment).

    Used by review-bot status detection. Providers that surface PR comments
    return these via :meth:`AbstractTaskProvider.get_pr_issue_comments`.
    """

    login: str
    body: str
    updated_at: datetime | None = None


class ReviewBotStatus(StrEnum):
    """Status of a review bot's review on a PR."""

    PAUSED = "paused"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class PollOutcome(StrEnum):
    """Outcome of a ``poll_for_reviews()`` call."""

    COMMENTS_FOUND = "comments_found"
    QUIET_TIMEOUT = "quiet_timeout"
    REVIEW_COMPLETE = "review_complete"
    PR_CLOSED = "pr_closed"
    INTERRUPTED = "interrupted"


# Grace period: if the latest commit is younger than this, suppress "all clear".
RECENT_COMMIT_GRACE_SECONDS = 120


def detect_coderabbit_review_status(
    comments: list[PRComment],
) -> tuple[ReviewBotStatus | None, datetime | None]:
    """Detect CodeRabbit review status from PR issue comments.

    Looks for the ``coderabbitai[bot]`` summary comment and checks for
    status markers embedded as HTML comments.

    Args:
        comments: List of PR-level comments.

    Returns:
        A ``(status, updated_at)`` tuple. ``status`` is a :class:`ReviewBotStatus`
        when a CodeRabbit summary comment is present, else ``None``. ``updated_at``
        is the matched comment's ``updated_at`` (CodeRabbit edits its summary in
        place, so this is the freshest "the bot touched the PR" signal) — used to
        populate ``PRReviewStatus.bot_status_ts`` for staleness checks. Both are
        ``None`` when no CodeRabbit comment is found.
    """
    # Find the latest CodeRabbit comment (last in the list = most recent)
    latest: PRComment | None = None
    for c in reversed(comments):
        if "coderabbit" in c.login.lower():
            latest = c
            break

    if latest is None:
        return None, None

    normalized = latest.body.casefold()

    if "review paused by coderabbit.ai" in normalized:
        return ReviewBotStatus.PAUSED, latest.updated_at
    if "review in progress by coderabbit.ai" in normalized:
        return ReviewBotStatus.IN_PROGRESS, latest.updated_at

    return ReviewBotStatus.COMPLETED, latest.updated_at


# ---------------------------------------------------------------------------
# CodeRabbit AI-agent prompt extraction
# ---------------------------------------------------------------------------

_CODERABBIT_PROMPT_RE = re.compile(
    r"<details>\s*<summary>🤖\s*Prompt for AI Agents</summary>\s*(.*?)\s*</details>",
    re.DOTALL,
)

_CODE_FENCE_RE = re.compile(r"```[\w]*\n(.*?)```", re.DOTALL)


def extract_coderabbit_ai_prompt(body: str) -> str | None:
    """Extract the ``🤖 Prompt for AI Agents`` block from a CodeRabbit comment.

    CodeRabbit wraps its AI-agent-specific instructions in::

        <details>
        <summary>🤖 Prompt for AI Agents</summary>

        ```
        <instruction text>
        ```

        </details>

    Returns the inner text (stripped of the code fence), or ``None`` if not found.
    """
    match = _CODERABBIT_PROMPT_RE.search(body)
    if not match:
        return None

    inner = match.group(1).strip()
    # Strip code fences if present
    fence_match = _CODE_FENCE_RE.search(inner)
    if fence_match:
        return fence_match.group(1).strip()
    return inner


# ---------------------------------------------------------------------------
# Thread filtering
# ---------------------------------------------------------------------------


def filter_actionable_threads(threads: list[ReviewThread]) -> list[ReviewThread]:
    """Return only unresolved, non-outdated threads with at least one comment."""
    return [t for t in threads if not t.is_resolved and not t.is_outdated and t.comments]


def filter_unresolved_threads(threads: list[ReviewThread]) -> list[ReviewThread]:
    """Return all unresolved threads with at least one comment, including outdated ones."""
    return [t for t in threads if not t.is_resolved and t.comments]


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------


def format_review_threads_markdown(threads: list[ReviewThread]) -> str:
    """Format review threads as structured markdown grouped by file.

    For CodeRabbit comments, the extracted ``🤖 Prompt for AI Agents`` content
    is used as the primary instruction, with the full comment body collapsed
    below.  For human comments, the full body is the instruction.
    """
    # Group threads by file path
    by_file: dict[str, list[ReviewThread]] = {}
    no_file: list[ReviewThread] = []

    for thread in threads:
        first = thread.first_comment
        if not first:
            continue
        path = first.path or ""
        if path:
            by_file.setdefault(path, []).append(thread)
        else:
            no_file.append(thread)

    lines: list[str] = ["# Review Comments to Address", ""]

    total = len(threads)
    file_count = len(by_file) + (1 if no_file else 0)
    lines.append(f"**{total}** unresolved comment(s) across **{file_count}** file(s).")
    lines.append("")

    # Render grouped by file
    for path in sorted(by_file.keys()):
        lines.append(f"## `{path}`")
        lines.append("")
        for thread in by_file[path]:
            lines.extend(_format_thread(thread))
        lines.append("")

    # General comments (no file)
    if no_file:
        lines.append("## General Comments")
        lines.append("")
        for thread in no_file:
            lines.extend(_format_thread(thread))
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _format_thread(thread: ReviewThread) -> list[str]:
    """Format a single thread as markdown."""
    first = thread.first_comment
    if not first:
        return []

    lines: list[str] = []

    # Location header
    loc_parts: list[str] = []
    if first.path:
        loc = first.path
        if first.line:
            loc += f":{first.line}"
        loc_parts.append(f"`{loc}`")
    if first.author:
        loc_parts.append(f"by **@{first.author}**")
    if first.url:
        loc_parts.append(f"([link]({first.url}))")

    if thread.is_outdated:
        loc_parts.append("[OUTDATED]")

    lines.append(f"### {' '.join(loc_parts)}" if loc_parts else "### Comment")
    lines.append("")

    # Outdated notice
    if thread.is_outdated:
        lines.append(
            "> **Note:** This thread is outdated — the code it references has changed."
            " Address the underlying concern in the current version of the code."
        )
        lines.append("")

    # Thread ID for resolution
    if thread.id:
        lines.append(f"**Thread ID:** `{thread.id}`")
        lines.append("")

    # CodeRabbit: extract AI-agent prompt as primary instruction
    ai_prompt = extract_coderabbit_ai_prompt(first.body)
    if ai_prompt:
        lines.append("**Instruction (from CodeRabbit):**")
        lines.append("")
        lines.append(ai_prompt)
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>Full CodeRabbit comment</summary>")
        lines.append("")
        lines.append(first.body)
        lines.append("")
        lines.append("</details>")
        lines.append("")
    else:
        # Human comment — full body is the instruction
        lines.append(first.body)
        lines.append("")

    # Follow-up comments in the thread
    if len(thread.comments) > 1:
        lines.append("**Follow-up comments:**")
        lines.append("")
        for comment in thread.comments[1:]:
            author = f"**@{comment.author}**: " if comment.author else ""
            lines.append(f"- {author}{comment.body}")
        lines.append("")

    lines.append("---")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# PR-level review state models
# ---------------------------------------------------------------------------


class ReviewState(StrEnum):
    """State of a PR-level review submission."""

    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    COMMENTED = "COMMENTED"
    PENDING = "PENDING"
    DISMISSED = "DISMISSED"


class PRReview(BaseModel):
    """A PR-level review submission (APPROVED, CHANGES_REQUESTED, etc.)."""

    author: str = ""
    state: ReviewState = ReviewState.COMMENTED
    body: str = ""
    submitted_at: datetime | None = None
    is_bot: bool = False


class PendingReviewer(BaseModel):
    """A reviewer who has been requested but hasn't submitted a review yet."""

    name: str = ""
    is_team: bool = False


class PRReviewStatus(BaseModel):
    """Unified container for all PR review status information.

    Combines inline review threads, PR-level review submissions, pending
    reviewer assignments, and bot status into a single model that consumers
    can query for actionable status.
    """

    actionable_threads: list[ReviewThread] = []
    all_unresolved_threads: list[ReviewThread] = []
    reviews: list[PRReview] = []
    pending_reviewers: list[PendingReviewer] = []
    bot_status: ReviewBotStatus | None = None
    bot_status_ts: datetime | None = None
    fetch_failed: bool = False
    latest_commit_pushed_at: datetime | None = None

    @property
    def effective_unresolved_threads(self) -> list[ReviewThread]:
        """Best available unresolved thread list.

        Prefers ``all_unresolved_threads`` (includes outdated) when populated.
        Falls back to ``actionable_threads`` for providers that only set the
        legacy field, preserving backward compatibility.
        """
        return self.all_unresolved_threads or self.actionable_threads

    def is_commit_fresh(self, grace_seconds: int = RECENT_COMMIT_GRACE_SECONDS) -> bool:
        """True if the latest commit is within the recent-commit grace period.

        Returns False when the timestamp is unavailable — we never assume
        freshness when we don't know the commit age.
        """
        if self.latest_commit_pushed_at is None:
            return False
        now_ts = datetime.now(UTC)
        pushed = _as_utc(self.latest_commit_pushed_at)
        return (now_ts - pushed).total_seconds() < grace_seconds

    @property
    def latest_reviews_by_author(self) -> dict[str, PRReview]:
        """Deduplicate reviews — keep only the latest per author.

        Reviews are assumed to be ordered chronologically (oldest first).
        Later reviews from the same author supersede earlier ones.
        Bot reviews are excluded from deduplication.
        """
        by_author: dict[str, PRReview] = {}
        for review in self.reviews:
            if review.is_bot:
                continue
            if review.author:
                by_author[review.author] = review
        return by_author

    @property
    def has_changes_requested(self) -> bool:
        """True if any non-bot reviewer's latest review is CHANGES_REQUESTED."""
        return any(
            r.state == ReviewState.CHANGES_REQUESTED for r in self.latest_reviews_by_author.values()
        )

    @property
    def approvals(self) -> list[str]:
        """Authors whose latest review is APPROVED."""
        return [
            author
            for author, review in self.latest_reviews_by_author.items()
            if review.state == ReviewState.APPROVED
        ]

    @property
    def changes_requested_by(self) -> list[str]:
        """Authors whose latest review is CHANGES_REQUESTED."""
        return [
            author
            for author, review in self.latest_reviews_by_author.items()
            if review.state == ReviewState.CHANGES_REQUESTED
        ]

    @property
    def review_covers_latest_commit(self) -> bool:
        """True when *every* distinct bot signal is at/after the latest commit.

        A ``bot_status == COMPLETED`` marker carries no information about *which*
        commit was reviewed, so a completion from before the latest push must not
        count as "done with HEAD". This predicate gates every "review complete /
        all clear" surface on the bot having actually reviewed the current commit.

        Covered (``True``) when the commit timestamp is unknown, or no bot signal
        exists (nothing to be stale relative to — a human-only or never-reviewed
        PR always stays covered), or every distinct bot source's latest signal is
        at/after the commit. Not covered (``False``) when any bot source's latest
        signal is strictly older than the latest commit — a fresh signal from one
        bot (e.g. Codex) cannot mask a stale one from another (e.g. CodeRabbit);
        see ``latest_bot_signal_ts``.

        Bot signals only (``bot_status_ts`` + bot ``submitted_at``): human review
        timestamps are deliberately excluded so an approve-then-fixup-commit flow
        does not spuriously flip to "not covered" — GitHub itself does not
        invalidate approvals on push, and ``has_changes_requested`` gates humans
        separately.
        """
        if self.latest_commit_pushed_at is None:
            return True
        bot_ts = latest_bot_signal_ts(self)
        if bot_ts is None:
            return True
        return bot_ts >= _as_utc(self.latest_commit_pushed_at)

    @property
    def is_all_clear(self) -> bool:
        """True when there's nothing blocking the PR.

        All clear requires:
        - Status was fetched successfully (no transient failures)
        - No unresolved threads (including outdated ones)
        - No CHANGES_REQUESTED from any reviewer
        - No bot currently processing (IN_PROGRESS)
        - The latest commit is covered by a bot review (not stale — see
          ``review_covers_latest_commit``)

        Note: pending reviewers do NOT block all-clear (informational only).
        """
        if self.fetch_failed:
            return False
        if self.effective_unresolved_threads:
            return False
        if self.has_changes_requested:
            return False
        if not self.review_covers_latest_commit:
            return False
        return self.bot_status != ReviewBotStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# Signal-timestamp helpers
# ---------------------------------------------------------------------------
#
# Pure functions of ``PRReviewStatus`` fields. They live here (the leaf models
# layer) rather than in ``services/review_settle.py`` so ``PRReviewStatus`` can
# compute commit-staleness without a model->service import. ``review_settle``
# re-imports ``latest_signal_ts`` from here (services->models is allowed).


def latest_signal_ts(status: PRReviewStatus) -> datetime | None:
    """Return the newest timestamp across all thread comments and reviews.

    Considers ``created_at`` from every comment in
    ``effective_unresolved_threads`` and ``submitted_at`` from every entry in
    ``reviews`` (human and bot alike).  Naive datetimes are treated as UTC,
    matching ``is_commit_fresh()``.  Returns ``None`` when no timestamps are
    available.  Used by the settle-window logic in ``review_settle.py``.
    """
    candidates: list[datetime] = []
    for thread in status.effective_unresolved_threads:
        for comment in thread.comments:
            if comment.created_at is not None:
                candidates.append(_as_utc(comment.created_at))
    for review in status.reviews:
        if review.submitted_at is not None:
            candidates.append(_as_utc(review.submitted_at))
    return max(candidates) if candidates else None


def latest_bot_signal_ts(status: PRReviewStatus) -> datetime | None:
    """Return the *oldest* "most-recent signal" among distinct bot sources.

    Grouped by source — every review author whose login contains
    "coderabbit" (case-insensitive) shares one ``"coderabbit"`` group with
    ``bot_status_ts`` (the CodeRabbit summary marker: also that bot's
    signal), and every other distinct bot review author (``review.is_bot``)
    gets its own group. Within a group the newest timestamp wins (a bot may
    post more than one signal — e.g. CodeRabbit's own review plus a later
    summary-comment edit); across groups the *oldest* per-group max wins.

    This is deliberately a min-of-maxes, not a global max: when CodeRabbit and
    another bot (e.g. Codex) are both enabled, a fresh Codex review must not
    paper over a stale CodeRabbit summary — every bot that has posted a signal
    has to individually cover the latest commit before the PR counts as
    reviewed at HEAD (see ``review_covers_latest_commit``). Merging CodeRabbit's
    own review and summary-marker signals into one group (rather than treating
    them as two independent bots) matters: without it, an old first-pass
    CodeRabbit review sitting in ``status.reviews`` would permanently drag the
    min below any later commit even after CodeRabbit re-analyzes and only
    touches its summary comment (the "found nothing new" case), making
    ``review_covers_latest_commit`` stuck at ``False`` forever. Human review
    ``submitted_at`` is deliberately excluded from every group — staleness is
    measured only against bots. Unresolved-thread comment timestamps are moot:
    every completion/all-clear surface is only reached when there are no
    unresolved threads, so they cannot contribute.
    """
    latest_by_source: dict[str, datetime] = {}

    def _bump(source: str, ts: datetime) -> None:
        if source not in latest_by_source or ts > latest_by_source[source]:
            latest_by_source[source] = ts

    if status.bot_status_ts is not None:
        _bump("coderabbit", _as_utc(status.bot_status_ts))
    for review in status.reviews:
        if review.is_bot and review.submitted_at is not None:
            source = (
                "coderabbit"
                if "coderabbit" in review.author.lower()
                else (review.author or "__unknown_bot__")
            )
            _bump(source, _as_utc(review.submitted_at))
    return min(latest_by_source.values()) if latest_by_source else None


# ---------------------------------------------------------------------------
# Review status summary formatting
# ---------------------------------------------------------------------------

# Level constants for format_review_status_summary tuples
LEVEL_SUCCESS = "success"
LEVEL_INFO = "info"
LEVEL_WARN = "warn"


def format_review_status_summary(
    status: PRReviewStatus,
    *,
    include_all_clear: bool = True,
) -> list[tuple[str, str]]:
    """Format a PRReviewStatus into (level, message) tuples for console display.

    Levels: "success", "info", "warn".

    Returns a list of messages covering:
    - Unresolved threads (warn)
    - Changes requested (warn)
    - Bot in-progress / paused (warn)
    - Approvals (success)
    - Pending reviewers (info)
    - All-clear (success), unless ``include_all_clear`` is False — callers that
      already emit their own report-by-exception line for the all-clear case
      pass this to avoid a redundant "nothing to address" reassurance.
    """
    messages: list[tuple[str, str]] = []

    # Fetch failure — indeterminate status
    if status.fetch_failed:
        messages.append(
            (
                LEVEL_WARN,
                "Review status fetch failed — status may be incomplete.",
            )
        )

    # Unresolved threads (includes outdated; falls back to actionable for legacy providers)
    thread_count = len(status.effective_unresolved_threads)
    if thread_count > 0:
        messages.append(
            (
                LEVEL_WARN,
                f"{thread_count} unresolved review thread(s) remain. "
                "Consider running wade review-pr-comments-session resolve for each.",
            )
        )

    # Changes requested (without inline threads)
    for author in status.changes_requested_by:
        messages.append(
            (
                LEVEL_WARN,
                f"Changes requested by @{author} (PR-level review).",
            )
        )

    # Bot status
    if status.bot_status == ReviewBotStatus.IN_PROGRESS:
        messages.append(
            (
                LEVEL_WARN,
                "A review bot is still processing — additional comments may arrive.",
            )
        )
    elif status.bot_status == ReviewBotStatus.PAUSED:
        messages.append(
            (
                LEVEL_WARN,
                "CodeRabbit review is paused — comments may arrive when resumed.",
            )
        )

    # Stale coverage: a bot signal exists but predates the latest commit, so the
    # newest push has not been re-reviewed. Explain it instead of going quiet —
    # ``is_all_clear`` is already False here, so the SESSION COMPLETE / all-clear
    # lines below are suppressed.
    if not status.review_covers_latest_commit:
        messages.append(
            (
                LEVEL_WARN,
                "Latest commit has not been reviewed yet — an updated review may still arrive.",
            )
        )

    # Approvals
    if status.approvals:
        names = ", ".join(f"@{a}" for a in status.approvals)
        messages.append((LEVEL_SUCCESS, f"Approved by {names}."))

    # Pending reviewers (informational)
    if status.pending_reviewers:
        names = ", ".join(
            f"@{r.name}" + (" (team)" if r.is_team else "") for r in status.pending_reviewers
        )
        messages.append((LEVEL_INFO, f"Awaiting review from {names}."))

    # All-clear
    if include_all_clear and status.is_all_clear:
        if not status.approvals and thread_count == 0:
            messages.append((LEVEL_SUCCESS, "All review threads resolved — nothing to address."))
        elif status.approvals and thread_count == 0:
            messages.append((LEVEL_SUCCESS, "SESSION COMPLETE — all review threads resolved."))

    return messages
