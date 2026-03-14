"""Review domain models — ReviewComment, ReviewThread, and formatting helpers.

Used by the ``review pr-comments`` flow to represent unresolved PR review
threads and render them as structured markdown for AI consumption.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


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


class ReviewBotStatus(StrEnum):
    """Status of a review bot's review on a PR."""

    PAUSED = "paused"
    IN_PROGRESS = "in_progress"


def detect_coderabbit_review_status(
    comments: list[dict[str, str]],
) -> ReviewBotStatus | None:
    """Detect CodeRabbit review status from PR issue comments.

    Looks for the ``coderabbitai[bot]`` summary comment and checks for
    status markers embedded as HTML comments.

    Args:
        comments: List of dicts with ``login`` and ``body`` keys.

    Returns:
        A :class:`ReviewBotStatus` if CodeRabbit is mid-review, else ``None``.
    """
    # Find the latest CodeRabbit comment (last in the list = most recent)
    latest_body: str | None = None
    for c in reversed(comments):
        if "coderabbit" in c.get("login", "").lower():
            latest_body = c.get("body", "")
            break

    if latest_body is None:
        return None

    if "review paused by coderabbit.ai" in latest_body:
        return ReviewBotStatus.PAUSED
    if "review in progress by coderabbit.ai" in latest_body:
        return ReviewBotStatus.IN_PROGRESS

    return None


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

    lines.append(f"### {' '.join(loc_parts)}" if loc_parts else "### Comment")
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
    reviews: list[PRReview] = []
    pending_reviewers: list[PendingReviewer] = []
    bot_status: ReviewBotStatus | None = None
    fetch_failed: bool = False

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
    def is_all_clear(self) -> bool:
        """True when there's nothing blocking the PR.

        All clear requires:
        - Status was fetched successfully (no transient failures)
        - No unresolved actionable threads
        - No CHANGES_REQUESTED from any reviewer
        - No bot currently processing (IN_PROGRESS)

        Note: pending reviewers do NOT block all-clear (informational only).
        """
        if self.fetch_failed:
            return False
        if self.actionable_threads:
            return False
        if self.has_changes_requested:
            return False
        return self.bot_status != ReviewBotStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# Review status summary formatting
# ---------------------------------------------------------------------------

# Level constants for format_review_status_summary tuples
LEVEL_SUCCESS = "success"
LEVEL_INFO = "info"
LEVEL_WARN = "warn"


def format_review_status_summary(
    status: PRReviewStatus,
) -> list[tuple[str, str]]:
    """Format a PRReviewStatus into (level, message) tuples for console display.

    Levels: "success", "info", "warn".

    Returns a list of messages covering:
    - Unresolved threads (warn)
    - Changes requested (warn)
    - Bot in-progress / paused (warn)
    - Approvals (success)
    - Pending reviewers (info)
    - All-clear (success)
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

    # Unresolved threads
    thread_count = len(status.actionable_threads)
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
    if status.is_all_clear:
        if not status.approvals and thread_count == 0:
            messages.append((LEVEL_SUCCESS, "All review threads resolved — nothing to address."))
        elif status.approvals and thread_count == 0:
            messages.append((LEVEL_SUCCESS, "SESSION COMPLETE — all review threads resolved."))

    return messages
