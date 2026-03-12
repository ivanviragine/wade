"""Tests for the batch review service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.models.batch import BatchIssueContext, BatchReviewContext
from wade.models.config import ProjectConfig
from wade.models.delegation import DelegationMode, DelegationResult
from wade.services.batch_review_service import (
    _format_batch_context,
    extract_child_issues,
)

# ---------------------------------------------------------------------------
# extract_child_issues
# ---------------------------------------------------------------------------


class TestExtractChildIssues:
    def test_unchecked_items(self) -> None:
        body = "## Tasks\n- [ ] #10\n- [ ] #11\n- [ ] #12\n"
        result = extract_child_issues(body)
        assert result == ["10", "11", "12"]

    def test_checked_items(self) -> None:
        body = "- [x] #5\n- [ ] #6\n- [x] #7\n"
        result = extract_child_issues(body)
        assert result == ["5", "6", "7"]

    def test_empty_body(self) -> None:
        assert extract_child_issues("") == []

    def test_no_checklist(self) -> None:
        body = "This is a regular issue body with #42 mention."
        assert extract_child_issues(body) == []

    def test_mixed_content(self) -> None:
        body = (
            "# Tracking Issue\n\n"
            "Some description.\n\n"
            "## Checklist\n"
            "- [ ] #100\n"
            "- [x] #101\n"
            "- [ ] #102\n\n"
            "## Notes\nSome notes here."
        )
        result = extract_child_issues(body)
        assert result == ["100", "101", "102"]


# ---------------------------------------------------------------------------
# gather_batch_context
# ---------------------------------------------------------------------------


class TestGatherBatchContext:
    @patch("wade.services.batch_review_service.git_repo")
    @patch("wade.services.batch_review_service.git_pr")
    @patch("wade.services.batch_review_service.git_branch")
    @patch("wade.services.batch_review_service.get_provider")
    @patch("wade.services.batch_review_service.load_config")
    def test_reads_issues(
        self,
        mock_config: MagicMock,
        mock_provider_fn: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_repo: MagicMock,
    ) -> None:
        from wade.services.batch_review_service import gather_batch_context

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = Path("/repo")
        mock_repo.detect_main_branch.return_value = "main"

        provider = MagicMock()
        mock_provider_fn.return_value = provider

        # Tracking issue
        tracking = MagicMock()
        tracking.body = "- [ ] #10\n- [ ] #11\n"

        # Child issues
        issue10 = MagicMock()
        issue10.title = "Add feature A"
        issue11 = MagicMock()
        issue11.title = "Add feature B"

        provider.read_task.side_effect = [tracking, issue10, issue11]

        mock_branch.make_branch_name.side_effect = [
            "feat/10-add-feature-a",
            "feat/11-add-feature-b",
        ]
        mock_branch.branch_exists.return_value = True
        mock_pr.get_pr_for_branch.return_value = None
        mock_repo.diff_stat_between.return_value = " 2 files changed"

        ctx = gather_batch_context("99", repo_root=Path("/repo"))

        assert ctx.tracking_issue == "99"
        assert len(ctx.issues) == 2
        assert ctx.issues[0].issue_number == "10"
        assert ctx.issues[0].issue_title == "Add feature A"
        assert ctx.issues[1].issue_number == "11"

    @patch("wade.services.batch_review_service.git_repo")
    @patch("wade.services.batch_review_service.git_pr")
    @patch("wade.services.batch_review_service.git_branch")
    @patch("wade.services.batch_review_service.get_provider")
    @patch("wade.services.batch_review_service.load_config")
    def test_missing_issue_graceful(
        self,
        mock_config: MagicMock,
        mock_provider_fn: MagicMock,
        mock_branch: MagicMock,
        mock_pr: MagicMock,
        mock_repo: MagicMock,
    ) -> None:
        from wade.services.batch_review_service import gather_batch_context

        mock_config.return_value = ProjectConfig()
        mock_repo.get_repo_root.return_value = Path("/repo")
        mock_repo.detect_main_branch.return_value = "main"

        provider = MagicMock()
        mock_provider_fn.return_value = provider

        tracking = MagicMock()
        tracking.body = "- [ ] #10\n- [ ] #11\n"

        issue10 = MagicMock()
        issue10.title = "Good issue"

        # First call: tracking, second: issue10 succeeds, third: issue11 raises
        provider.read_task.side_effect = [tracking, issue10, Exception("Not found")]

        from wade.git.repo import GitError

        mock_branch.make_branch_name.return_value = "feat/10-good-issue"
        mock_branch.branch_exists.return_value = False
        mock_pr.get_pr_for_branch.return_value = None
        mock_repo.fetch_ref.side_effect = GitError("no remote")

        ctx = gather_batch_context("99", repo_root=Path("/repo"))

        assert len(ctx.issues) == 2
        assert ctx.issues[0].issue_number == "10"
        assert ctx.issues[1].issue_number == "11"
        assert ctx.issues[1].issue_title == "(unreadable #11)"


# ---------------------------------------------------------------------------
# create_integration_branch
# ---------------------------------------------------------------------------


class TestCreateIntegrationBranch:
    @patch("wade.services.batch_review_service.git_sync")
    @patch("wade.services.batch_review_service.git_repo")
    @patch("wade.services.batch_review_service.git_branch")
    def test_merges_branches(
        self,
        mock_branch: MagicMock,
        mock_repo: MagicMock,
        mock_sync: MagicMock,
    ) -> None:
        from wade.services.batch_review_service import create_integration_branch

        mock_branch.branch_exists.return_value = False

        ctx = BatchReviewContext(
            issues=[
                BatchIssueContext(
                    issue_number="10",
                    issue_title="Feature A",
                    branch_name="feat/10-feature-a",
                ),
                BatchIssueContext(
                    issue_number="11",
                    issue_title="Feature B",
                    branch_name="feat/11-feature-b",
                ),
            ],
            main_branch="main",
            tracking_issue="99",
        )

        result = create_integration_branch(Path("/repo"), ctx)

        assert result.integration_branch == "batch-review/99"
        mock_branch.create_branch.assert_called_once_with(Path("/repo"), "batch-review/99", "main")
        mock_repo.checkout.assert_called_once_with(Path("/repo"), "batch-review/99")
        assert mock_repo.merge_no_edit.call_count == 2
        assert result.issues[0].merged is True
        assert result.issues[1].merged is True

    @patch("wade.services.batch_review_service.git_sync")
    @patch("wade.services.batch_review_service.git_repo")
    @patch("wade.services.batch_review_service.git_branch")
    def test_handles_conflicts(
        self,
        mock_branch: MagicMock,
        mock_repo: MagicMock,
        mock_sync: MagicMock,
    ) -> None:
        from wade.git.repo import GitError
        from wade.services.batch_review_service import create_integration_branch

        mock_branch.branch_exists.return_value = False

        ctx = BatchReviewContext(
            issues=[
                BatchIssueContext(
                    issue_number="10",
                    issue_title="Feature A",
                    branch_name="feat/10-feature-a",
                ),
                BatchIssueContext(
                    issue_number="11",
                    issue_title="Feature B",
                    branch_name="feat/11-feature-b",
                ),
            ],
            main_branch="main",
            tracking_issue="99",
        )

        # First merge succeeds, second conflicts
        mock_repo.merge_no_edit.side_effect = [None, GitError("conflict")]

        result = create_integration_branch(Path("/repo"), ctx)

        assert result.issues[0].merged is True
        assert result.issues[0].conflict is False
        assert result.issues[1].merged is False
        assert result.issues[1].conflict is True
        mock_sync.abort_merge.assert_called_once()

    @patch("wade.services.batch_review_service.git_sync")
    @patch("wade.services.batch_review_service.git_repo")
    @patch("wade.services.batch_review_service.git_branch")
    def test_skips_issues_without_branch(
        self,
        mock_branch: MagicMock,
        mock_repo: MagicMock,
        mock_sync: MagicMock,
    ) -> None:
        from wade.services.batch_review_service import create_integration_branch

        mock_branch.branch_exists.return_value = False

        ctx = BatchReviewContext(
            issues=[
                BatchIssueContext(issue_number="10", issue_title="No branch"),
            ],
            main_branch="main",
            tracking_issue="99",
        )

        result = create_integration_branch(Path("/repo"), ctx)

        mock_repo.merge_no_edit.assert_not_called()
        assert result.issues[0].merged is False


# ---------------------------------------------------------------------------
# create_review_pr
# ---------------------------------------------------------------------------


class TestCreateReviewPr:
    @patch("wade.services.batch_review_service.git_pr")
    @patch("wade.services.batch_review_service.git_repo")
    def test_creates_draft_pr(
        self,
        mock_repo: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        from wade.services.batch_review_service import create_review_pr

        mock_pr.create_pr.return_value = {
            "number": 42,
            "url": "https://github.com/org/repo/pull/42",
        }

        ctx = BatchReviewContext(
            issues=[
                BatchIssueContext(
                    issue_number="10",
                    issue_title="Feature",
                    merged=True,
                ),
            ],
            main_branch="main",
            tracking_issue="99",
            integration_branch="batch-review/99",
        )

        result = create_review_pr(Path("/repo"), ctx)

        assert result.pr_number == 42
        assert result.pr_url == "https://github.com/org/repo/pull/42"
        mock_pr.create_pr.assert_called_once()
        call_kwargs = mock_pr.create_pr.call_args
        assert call_kwargs[1]["draft"] is True
        assert "Part of #99" in call_kwargs[1]["body"]


# ---------------------------------------------------------------------------
# _format_batch_context
# ---------------------------------------------------------------------------


class TestFormatBatchContext:
    def test_renders_markdown(self) -> None:
        ctx = BatchReviewContext(
            issues=[
                BatchIssueContext(
                    issue_number="10",
                    issue_title="Feature A",
                    branch_name="feat/10-feature-a",
                    merged=True,
                    diff_stat=" 3 files changed, 50 insertions(+)",
                ),
                BatchIssueContext(
                    issue_number="11",
                    issue_title="Feature B",
                    branch_name="feat/11-feature-b",
                    conflict=True,
                ),
            ],
            main_branch="main",
            tracking_issue="99",
            integration_branch="batch-review/99",
        )

        output = _format_batch_context(ctx)

        assert "## Tracking issue: #99" in output
        assert "### Issue #10: Feature A" in output
        assert "successfully merged" in output
        assert "### Issue #11: Feature B" in output
        assert "CONFLICT" in output
        assert "3 files changed" in output

    def test_issue_without_branch(self) -> None:
        ctx = BatchReviewContext(
            issues=[
                BatchIssueContext(
                    issue_number="10",
                    issue_title="No branch yet",
                ),
            ],
            tracking_issue="50",
        )

        output = _format_batch_context(ctx)
        assert "(no branch)" in output
        assert "skipped" in output


# ---------------------------------------------------------------------------
# run_coherence_review
# ---------------------------------------------------------------------------


class TestRunCoherenceReview:
    @patch("wade.services.batch_review_service._check_review_enabled")
    def test_skipped_when_disabled(
        self,
        mock_check: MagicMock,
    ) -> None:
        from wade.services.batch_review_service import run_coherence_review

        mock_check.return_value = DelegationResult(
            success=True,
            feedback="Review skipped.",
            mode=DelegationMode.PROMPT,
            skipped=True,
        )

        ctx = BatchReviewContext(tracking_issue="99")
        result = run_coherence_review(ctx)

        assert result.success is True
        assert result.skipped is True

    @patch("wade.services.batch_review_service.git_pr")
    @patch("wade.services.batch_review_service.git_repo")
    @patch("wade.services.batch_review_service._run_review_delegation")
    @patch("wade.services.batch_review_service._check_review_enabled")
    @patch("wade.services.batch_review_service.load_prompt_template")
    def test_posts_to_pr(
        self,
        mock_template: MagicMock,
        mock_check: MagicMock,
        mock_delegation: MagicMock,
        mock_repo: MagicMock,
        mock_pr: MagicMock,
    ) -> None:
        from wade.services.batch_review_service import run_coherence_review

        mock_check.return_value = None
        mock_template.return_value = "Review:\n{batch_context}"
        mock_delegation.return_value = DelegationResult(
            success=True,
            feedback="All looks good!",
            mode=DelegationMode.PROMPT,
        )
        mock_repo.get_repo_root.return_value = Path("/repo")

        ctx = BatchReviewContext(
            issues=[
                BatchIssueContext(issue_number="10", issue_title="Feature A", merged=True),
            ],
            tracking_issue="99",
            pr_number=42,
        )

        result = run_coherence_review(ctx)

        assert result.success is True
        mock_pr.comment_on_pr.assert_called_once_with(Path("/repo"), 42, "All looks good!")


# ---------------------------------------------------------------------------
# review_batch (main entry point)
# ---------------------------------------------------------------------------


class TestReviewBatch:
    @patch("wade.services.batch_review_service.git_repo")
    def test_not_in_repo_fails(
        self,
        mock_repo: MagicMock,
    ) -> None:
        from wade.git.repo import GitError
        from wade.services.batch_review_service import review_batch

        mock_repo.get_repo_root.side_effect = GitError("not a repo")

        result = review_batch("99")

        assert result.success is False
        assert "Not inside a git repository" in result.feedback

    @patch("wade.services.batch_review_service.run_coherence_review")
    @patch("wade.services.batch_review_service.create_review_pr")
    @patch("wade.services.batch_review_service.create_integration_branch")
    @patch("wade.services.batch_review_service.gather_batch_context")
    @patch("wade.services.batch_review_service.git_repo")
    def test_no_issues_skips(
        self,
        mock_repo: MagicMock,
        mock_gather: MagicMock,
        mock_integration: MagicMock,
        mock_pr: MagicMock,
        mock_review: MagicMock,
    ) -> None:
        from wade.services.batch_review_service import review_batch

        mock_repo.get_repo_root.return_value = Path("/repo")
        mock_gather.return_value = BatchReviewContext(tracking_issue="99")

        result = review_batch("99")

        assert result.success is True
        assert result.skipped is True
        mock_integration.assert_not_called()
