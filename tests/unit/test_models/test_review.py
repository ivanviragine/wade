"""Tests for review domain models — ReviewComment, ReviewThread, and formatting helpers."""

from __future__ import annotations

from wade.models.review import (
    ReviewBotStatus,
    ReviewComment,
    ReviewThread,
    detect_coderabbit_review_status,
    extract_coderabbit_ai_prompt,
    filter_actionable_threads,
    format_review_threads_markdown,
)

# ---------------------------------------------------------------------------
# Model basics
# ---------------------------------------------------------------------------


class TestReviewComment:
    def test_create_minimal(self) -> None:
        comment = ReviewComment()
        assert comment.author == ""
        assert comment.body == ""
        assert comment.path is None
        assert comment.line is None

    def test_create_full(self) -> None:
        comment = ReviewComment(
            author="octocat",
            body="Fix this bug",
            path="src/main.py",
            line=42,
            url="https://github.com/owner/repo/pull/1#discussion_r123",
        )
        assert comment.author == "octocat"
        assert comment.path == "src/main.py"
        assert comment.line == 42


class TestReviewThread:
    def test_empty_thread(self) -> None:
        thread = ReviewThread()
        assert thread.is_resolved is False
        assert thread.is_outdated is False
        assert thread.comments == []
        assert thread.first_comment is None
        assert thread.id == ""

    def test_first_comment(self) -> None:
        c1 = ReviewComment(author="alice", body="First")
        c2 = ReviewComment(author="bob", body="Second")
        thread = ReviewThread(comments=[c1, c2])
        assert thread.first_comment is c1

    def test_resolved_thread(self) -> None:
        thread = ReviewThread(
            is_resolved=True,
            comments=[ReviewComment(body="resolved")],
        )
        assert thread.is_resolved is True

    def test_thread_with_id(self) -> None:
        thread = ReviewThread(
            id="PRRT_kwDONS4bTM6VIKWd",
            comments=[ReviewComment(body="Fix this")],
        )
        assert thread.id == "PRRT_kwDONS4bTM6VIKWd"


# ---------------------------------------------------------------------------
# Review bot status detection
# ---------------------------------------------------------------------------


class TestDetectCoderabbitReviewStatus:
    def test_paused_review(self) -> None:
        comments = [
            {
                "login": "coderabbitai[bot]",
                "body": (
                    "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\n"
                    "<!-- This is an auto-generated comment: review paused by coderabbit.ai -->\n"
                    "\n> [!NOTE]\n> ## Reviews paused\n"
                ),
            }
        ]
        assert detect_coderabbit_review_status(comments) == ReviewBotStatus.PAUSED

    def test_in_progress_review(self) -> None:
        comments = [
            {
                "login": "coderabbitai[bot]",
                "body": (
                    "<!-- This is an auto-generated comment:"
                    " review in progress by coderabbit.ai -->"
                ),
            }
        ]
        assert detect_coderabbit_review_status(comments) == ReviewBotStatus.IN_PROGRESS

    def test_completed_review_returns_none(self) -> None:
        comments = [
            {
                "login": "coderabbitai[bot]",
                "body": (
                    "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\n"
                    "## Walkthrough\nSome summary here."
                ),
            }
        ]
        assert detect_coderabbit_review_status(comments) is None

    def test_no_coderabbit_comments(self) -> None:
        comments = [
            {"login": "octocat", "body": "Looks good to me!"},
        ]
        assert detect_coderabbit_review_status(comments) is None

    def test_empty_comments(self) -> None:
        assert detect_coderabbit_review_status([]) is None

    def test_uses_latest_comment(self) -> None:
        """When multiple CodeRabbit comments exist, the latest (last) wins."""
        comments = [
            {
                "login": "coderabbitai[bot]",
                "body": (
                    "<!-- This is an auto-generated comment: review paused by coderabbit.ai -->"
                ),
            },
            {
                "login": "coderabbitai[bot]",
                "body": (
                    "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\nDone."
                ),
            },
        ]
        # Latest comment has no paused/in-progress marker -> None
        assert detect_coderabbit_review_status(comments) is None

    def test_paused_overrides_earlier_completed(self) -> None:
        """A newer paused comment takes precedence over an older completed one."""
        comments = [
            {
                "login": "coderabbitai[bot]",
                "body": (
                    "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\nDone."
                ),
            },
            {
                "login": "coderabbitai[bot]",
                "body": (
                    "<!-- This is an auto-generated comment: review paused by coderabbit.ai -->"
                ),
            },
        ]
        assert detect_coderabbit_review_status(comments) == ReviewBotStatus.PAUSED


# ---------------------------------------------------------------------------
# CodeRabbit AI-agent prompt extraction
# ---------------------------------------------------------------------------


class TestExtractCoderabbitAiPrompt:
    def test_extracts_prompt_from_details_block(self) -> None:
        body = """Some review text here.

<details>
<summary>🤖 Prompt for AI Agents</summary>

```
In file src/main.py around line 42, fix the null check.
```

</details>

Some more text."""
        result = extract_coderabbit_ai_prompt(body)
        assert result == "In file src/main.py around line 42, fix the null check."

    def test_extracts_prompt_with_language_fence(self) -> None:
        body = """<details>
<summary>🤖 Prompt for AI Agents</summary>

```text
Fix the import order in utils.py
```

</details>"""
        result = extract_coderabbit_ai_prompt(body)
        assert result == "Fix the import order in utils.py"

    def test_returns_none_when_no_prompt(self) -> None:
        body = "Just a regular review comment."
        assert extract_coderabbit_ai_prompt(body) is None

    def test_returns_none_for_different_details_block(self) -> None:
        body = """<details>
<summary>Some other block</summary>

Content here.

</details>"""
        assert extract_coderabbit_ai_prompt(body) is None

    def test_extracts_without_code_fence(self) -> None:
        body = """<details>
<summary>🤖 Prompt for AI Agents</summary>

Fix the null check in main.py line 42.

</details>"""
        result = extract_coderabbit_ai_prompt(body)
        assert result == "Fix the null check in main.py line 42."

    def test_real_coderabbit_sample(self) -> None:
        """Test with a realistic CodeRabbit comment structure."""
        body = """**issue (complexity):** The error handling is incorrect.

The function doesn't check for None before accessing the attribute.

**Proposed fix:**
```python
if obj is not None:
    obj.method()
```

<details>
<summary>🤖 Prompt for AI Agents</summary>

```
In src/wade/services/work_service.py around line 350, add a None check
before accessing `usage.total_tokens`. The current code will raise an
AttributeError when `usage` is None.
```

</details>"""
        result = extract_coderabbit_ai_prompt(body)
        assert result is not None
        assert "src/wade/services/work_service.py" in result
        assert "None check" in result


# ---------------------------------------------------------------------------
# Thread filtering
# ---------------------------------------------------------------------------


class TestFilterActionableThreads:
    def _thread(
        self,
        *,
        resolved: bool = False,
        outdated: bool = False,
        has_comments: bool = True,
    ) -> ReviewThread:
        comments = [ReviewComment(body="test")] if has_comments else []
        return ReviewThread(
            is_resolved=resolved,
            is_outdated=outdated,
            comments=comments,
        )

    def test_keeps_unresolved_non_outdated(self) -> None:
        threads = [self._thread()]
        assert len(filter_actionable_threads(threads)) == 1

    def test_filters_resolved(self) -> None:
        threads = [self._thread(resolved=True)]
        assert len(filter_actionable_threads(threads)) == 0

    def test_filters_outdated(self) -> None:
        threads = [self._thread(outdated=True)]
        assert len(filter_actionable_threads(threads)) == 0

    def test_filters_empty_comments(self) -> None:
        threads = [self._thread(has_comments=False)]
        assert len(filter_actionable_threads(threads)) == 0

    def test_mixed_threads(self) -> None:
        threads = [
            self._thread(),  # actionable
            self._thread(resolved=True),  # filtered
            self._thread(outdated=True),  # filtered
            self._thread(),  # actionable
        ]
        result = filter_actionable_threads(threads)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------


class TestFormatReviewThreadsMarkdown:
    def test_single_file_single_comment(self) -> None:
        threads = [
            ReviewThread(
                comments=[
                    ReviewComment(
                        author="alice",
                        body="Fix this typo",
                        path="src/main.py",
                        line=10,
                        url="https://github.com/o/r/pull/1#r1",
                    )
                ]
            )
        ]
        result = format_review_threads_markdown(threads)
        assert "# Review Comments to Address" in result
        assert "**1** unresolved comment(s)" in result
        assert "## `src/main.py`" in result
        assert "Fix this typo" in result
        assert "@alice" in result

    def test_multiple_files(self) -> None:
        threads = [
            ReviewThread(
                comments=[ReviewComment(author="alice", body="Fix A", path="a.py", line=1)]
            ),
            ReviewThread(comments=[ReviewComment(author="bob", body="Fix B", path="b.py", line=2)]),
        ]
        result = format_review_threads_markdown(threads)
        assert "**2** unresolved comment(s)" in result
        assert "## `a.py`" in result
        assert "## `b.py`" in result

    def test_general_comments_no_file(self) -> None:
        threads = [ReviewThread(comments=[ReviewComment(author="alice", body="General feedback")])]
        result = format_review_threads_markdown(threads)
        assert "## General Comments" in result
        assert "General feedback" in result

    def test_coderabbit_comment_extraction(self) -> None:
        coderabbit_body = """Some review text.

<details>
<summary>🤖 Prompt for AI Agents</summary>

```
Fix the null check in main.py.
```

</details>"""
        threads = [
            ReviewThread(
                comments=[
                    ReviewComment(
                        author="coderabbitai[bot]",
                        body=coderabbit_body,
                        path="main.py",
                        line=5,
                    )
                ]
            )
        ]
        result = format_review_threads_markdown(threads)
        assert "**Instruction (from CodeRabbit):**" in result
        assert "Fix the null check in main.py." in result
        assert "Full CodeRabbit comment" in result

    def test_thread_with_followups(self) -> None:
        threads = [
            ReviewThread(
                comments=[
                    ReviewComment(author="alice", body="First comment", path="a.py", line=1),
                    ReviewComment(author="bob", body="I agree with Alice"),
                    ReviewComment(author="alice", body="Thanks Bob"),
                ]
            )
        ]
        result = format_review_threads_markdown(threads)
        assert "**Follow-up comments:**" in result
        assert "@bob" in result
        assert "I agree with Alice" in result

    def test_empty_threads(self) -> None:
        result = format_review_threads_markdown([])
        assert "# Review Comments to Address" in result
        assert "**0** unresolved comment(s)" in result

    def test_thread_id_in_output(self) -> None:
        threads = [
            ReviewThread(
                id="PRRT_kwDONS4bTM6VIKWd",
                comments=[
                    ReviewComment(
                        author="alice",
                        body="Fix this",
                        path="main.py",
                        line=10,
                    )
                ],
            )
        ]
        result = format_review_threads_markdown(threads)
        assert "PRRT_kwDONS4bTM6VIKWd" in result
        assert "**Thread ID:**" in result

    def test_thread_id_missing_no_extra_line(self) -> None:
        threads = [
            ReviewThread(
                comments=[ReviewComment(author="alice", body="Fix this", path="main.py", line=10)],
            )
        ]
        result = format_review_threads_markdown(threads)
        assert "**Thread ID:**" not in result


# ---------------------------------------------------------------------------
# PR-level review state models
# ---------------------------------------------------------------------------


class TestReviewState:
    def test_all_states_exist(self) -> None:
        from wade.models.review import ReviewState

        assert ReviewState.APPROVED == "APPROVED"
        assert ReviewState.CHANGES_REQUESTED == "CHANGES_REQUESTED"
        assert ReviewState.COMMENTED == "COMMENTED"
        assert ReviewState.PENDING == "PENDING"
        assert ReviewState.DISMISSED == "DISMISSED"


class TestPRReview:
    def test_create_minimal(self) -> None:
        from wade.models.review import PRReview

        review = PRReview()
        assert review.author == ""
        assert review.state.value == "COMMENTED"
        assert review.body == ""
        assert review.is_bot is False

    def test_create_full(self) -> None:
        from wade.models.review import PRReview, ReviewState

        review = PRReview(
            author="octocat",
            state=ReviewState.APPROVED,
            body="LGTM!",
            is_bot=False,
        )
        assert review.author == "octocat"
        assert review.state == ReviewState.APPROVED


class TestPendingReviewer:
    def test_create_user(self) -> None:
        from wade.models.review import PendingReviewer

        reviewer = PendingReviewer(name="alice", is_team=False)
        assert reviewer.name == "alice"
        assert reviewer.is_team is False

    def test_create_team(self) -> None:
        from wade.models.review import PendingReviewer

        reviewer = PendingReviewer(name="core-team", is_team=True)
        assert reviewer.name == "core-team"
        assert reviewer.is_team is True


class TestPRReviewStatus:
    def test_empty_status_is_all_clear(self) -> None:
        from wade.models.review import PRReviewStatus

        status = PRReviewStatus()
        assert status.is_all_clear is True
        assert status.has_changes_requested is False
        assert status.approvals == []
        assert status.changes_requested_by == []

    def test_unresolved_threads_block_all_clear(self) -> None:
        from wade.models.review import PRReviewStatus

        status = PRReviewStatus(
            actionable_threads=[
                ReviewThread(
                    id="t1",
                    comments=[ReviewComment(author="alice", body="Fix")],
                )
            ]
        )
        assert status.is_all_clear is False

    def test_changes_requested_blocks_all_clear(self) -> None:
        from wade.models.review import PRReview, PRReviewStatus, ReviewState

        status = PRReviewStatus(
            reviews=[
                PRReview(author="bob", state=ReviewState.CHANGES_REQUESTED),
            ]
        )
        assert status.is_all_clear is False
        assert status.has_changes_requested is True
        assert status.changes_requested_by == ["bob"]

    def test_bot_in_progress_blocks_all_clear(self) -> None:
        from wade.models.review import PRReviewStatus

        status = PRReviewStatus(bot_status=ReviewBotStatus.IN_PROGRESS)
        assert status.is_all_clear is False

    def test_bot_paused_does_not_block_all_clear(self) -> None:
        from wade.models.review import PRReviewStatus

        status = PRReviewStatus(bot_status=ReviewBotStatus.PAUSED)
        assert status.is_all_clear is True

    def test_pending_reviewers_do_not_block_all_clear(self) -> None:
        from wade.models.review import PendingReviewer, PRReviewStatus

        status = PRReviewStatus(pending_reviewers=[PendingReviewer(name="charlie", is_team=False)])
        assert status.is_all_clear is True

    def test_latest_reviews_by_author_deduplication(self) -> None:
        from wade.models.review import PRReview, PRReviewStatus, ReviewState

        status = PRReviewStatus(
            reviews=[
                PRReview(author="alice", state=ReviewState.CHANGES_REQUESTED),
                PRReview(author="bob", state=ReviewState.APPROVED),
                PRReview(author="alice", state=ReviewState.APPROVED),
            ]
        )
        latest = status.latest_reviews_by_author
        assert latest["alice"].state == ReviewState.APPROVED
        assert latest["bob"].state == ReviewState.APPROVED
        assert status.is_all_clear is True
        assert status.approvals == ["alice", "bob"]
        assert status.changes_requested_by == []

    def test_latest_reviews_excludes_bots(self) -> None:
        from wade.models.review import PRReview, PRReviewStatus, ReviewState

        status = PRReviewStatus(
            reviews=[
                PRReview(
                    author="coderabbitai[bot]",
                    state=ReviewState.CHANGES_REQUESTED,
                    is_bot=True,
                ),
                PRReview(author="alice", state=ReviewState.APPROVED),
            ]
        )
        assert "coderabbitai[bot]" not in status.latest_reviews_by_author
        assert status.has_changes_requested is False
        assert status.approvals == ["alice"]

    def test_mixed_approved_and_changes_requested(self) -> None:
        from wade.models.review import PRReview, PRReviewStatus, ReviewState

        status = PRReviewStatus(
            reviews=[
                PRReview(author="alice", state=ReviewState.APPROVED),
                PRReview(author="bob", state=ReviewState.CHANGES_REQUESTED),
            ]
        )
        assert status.is_all_clear is False
        assert status.approvals == ["alice"]
        assert status.changes_requested_by == ["bob"]

    def test_dismissed_review_not_blocking(self) -> None:
        from wade.models.review import PRReview, PRReviewStatus, ReviewState

        status = PRReviewStatus(
            reviews=[
                PRReview(author="alice", state=ReviewState.DISMISSED),
            ]
        )
        assert status.is_all_clear is True
        assert status.changes_requested_by == []


# ---------------------------------------------------------------------------
# format_review_status_summary
# ---------------------------------------------------------------------------


class TestFormatReviewStatusSummary:
    def test_empty_status_all_clear(self) -> None:
        from wade.models.review import PRReviewStatus, format_review_status_summary

        status = PRReviewStatus()
        messages = format_review_status_summary(status)
        assert len(messages) == 1
        level, msg = messages[0]
        assert level == "success"
        assert "resolved" in msg.lower() or "nothing to address" in msg.lower()

    def test_unresolved_threads(self) -> None:
        from wade.models.review import PRReviewStatus, format_review_status_summary

        status = PRReviewStatus(
            actionable_threads=[
                ReviewThread(
                    id="t1",
                    comments=[ReviewComment(author="alice", body="Fix")],
                )
            ]
        )
        messages = format_review_status_summary(status)
        levels = [m[0] for m in messages]
        assert "warn" in levels
        assert any("1 unresolved" in m[1] for m in messages)

    def test_changes_requested(self) -> None:
        from wade.models.review import (
            PRReview,
            PRReviewStatus,
            ReviewState,
            format_review_status_summary,
        )

        status = PRReviewStatus(
            reviews=[PRReview(author="bob", state=ReviewState.CHANGES_REQUESTED)]
        )
        messages = format_review_status_summary(status)
        assert any("@bob" in m[1] and m[0] == "warn" for m in messages)

    def test_approvals_shown(self) -> None:
        from wade.models.review import (
            PRReview,
            PRReviewStatus,
            ReviewState,
            format_review_status_summary,
        )

        status = PRReviewStatus(reviews=[PRReview(author="alice", state=ReviewState.APPROVED)])
        messages = format_review_status_summary(status)
        assert any("@alice" in m[1] and m[0] == "success" for m in messages)

    def test_pending_reviewers_info_level(self) -> None:
        from wade.models.review import (
            PendingReviewer,
            PRReviewStatus,
            format_review_status_summary,
        )

        status = PRReviewStatus(pending_reviewers=[PendingReviewer(name="charlie", is_team=False)])
        messages = format_review_status_summary(status)
        assert any("@charlie" in m[1] and m[0] == "info" for m in messages)

    def test_bot_in_progress_warning(self) -> None:
        from wade.models.review import PRReviewStatus, format_review_status_summary

        status = PRReviewStatus(bot_status=ReviewBotStatus.IN_PROGRESS)
        messages = format_review_status_summary(status)
        assert any("bot" in m[1].lower() and m[0] == "warn" for m in messages)

    def test_bot_paused_warning(self) -> None:
        from wade.models.review import PRReviewStatus, format_review_status_summary

        status = PRReviewStatus(bot_status=ReviewBotStatus.PAUSED)
        messages = format_review_status_summary(status)
        assert any("paused" in m[1].lower() and m[0] == "warn" for m in messages)

    def test_team_pending_reviewer_shown(self) -> None:
        from wade.models.review import (
            PendingReviewer,
            PRReviewStatus,
            format_review_status_summary,
        )

        status = PRReviewStatus(pending_reviewers=[PendingReviewer(name="core-team", is_team=True)])
        messages = format_review_status_summary(status)
        assert any("(team)" in m[1] for m in messages)

    def test_approved_with_no_threads_shows_complete(self) -> None:
        from wade.models.review import (
            PRReview,
            PRReviewStatus,
            ReviewState,
            format_review_status_summary,
        )

        status = PRReviewStatus(reviews=[PRReview(author="alice", state=ReviewState.APPROVED)])
        messages = format_review_status_summary(status)
        assert any("SESSION COMPLETE" in m[1] for m in messages)
