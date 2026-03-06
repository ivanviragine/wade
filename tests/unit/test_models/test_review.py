"""Tests for review domain models — ReviewComment, ReviewThread, and formatting helpers."""

from __future__ import annotations

from wade.models.review import (
    ReviewComment,
    ReviewThread,
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
