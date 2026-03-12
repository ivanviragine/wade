"""Tests for batch review domain models."""

from __future__ import annotations

from wade.models.batch import BatchIssueContext, BatchReviewContext


class TestBatchIssueContext:
    def test_minimal(self) -> None:
        ctx = BatchIssueContext(issue_number="42", issue_title="Add auth")
        assert ctx.issue_number == "42"
        assert ctx.issue_title == "Add auth"
        assert ctx.branch_name is None
        assert ctx.pr_number is None
        assert ctx.pr_url is None
        assert ctx.diff_stat == ""
        assert ctx.merged is False
        assert ctx.conflict is False
        assert ctx.status == ""

    def test_full(self) -> None:
        ctx = BatchIssueContext(
            issue_number="10",
            issue_title="Fix bug",
            branch_name="feat/10-fix-bug",
            pr_number=55,
            pr_url="https://github.com/org/repo/pull/55",
            diff_stat=" 3 files changed",
            merged=True,
            conflict=False,
            status="OPEN",
        )
        assert ctx.branch_name == "feat/10-fix-bug"
        assert ctx.pr_number == 55
        assert ctx.merged is True
        assert ctx.status == "OPEN"

    def test_roundtrip(self) -> None:
        original = BatchIssueContext(
            issue_number="1",
            issue_title="Test",
            branch_name="feat/1-test",
            merged=True,
        )
        data = original.model_dump()
        restored = BatchIssueContext(**data)
        assert restored == original


class TestBatchReviewContext:
    def test_defaults(self) -> None:
        ctx = BatchReviewContext()
        assert ctx.issues == []
        assert ctx.main_branch == "main"
        assert ctx.tracking_issue is None
        assert ctx.integration_branch is None
        assert ctx.pr_number is None
        assert ctx.pr_url is None

    def test_with_issues(self) -> None:
        issues = [
            BatchIssueContext(issue_number="1", issue_title="First"),
            BatchIssueContext(issue_number="2", issue_title="Second"),
        ]
        ctx = BatchReviewContext(
            issues=issues,
            tracking_issue="100",
            main_branch="main",
        )
        assert len(ctx.issues) == 2
        assert ctx.tracking_issue == "100"

    def test_roundtrip(self) -> None:
        original = BatchReviewContext(
            issues=[BatchIssueContext(issue_number="5", issue_title="Feature")],
            tracking_issue="50",
            integration_branch="batch-review/50",
            pr_number=99,
            pr_url="https://github.com/org/repo/pull/99",
        )
        data = original.model_dump()
        restored = BatchReviewContext(**data)
        assert restored == original
