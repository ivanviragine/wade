"""Regression tests: a failed PR lookup is never treated as "no PR" (#357).

A transient ``gh`` failure returns ``PRLookup(lookup_failed=True)``. The
create-or-reuse paths must stop rather than fall through to ``create_pr``, which
would open a duplicate PR (or fail with GitHub's "a pull request already
exists") for a branch that may already have an open PR.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.pr import PRLookup
from wade.models.batch import BatchReviewContext
from wade.services.batch_review_service import create_review_pr
from wade.services.implementation_service.draft_pr import bootstrap_draft_pr

_D = "wade.services.implementation_service.draft_pr"
_B = "wade.services.batch_review_service"


@patch(f"{_D}.git_pr.create_pr")
@patch(f"{_D}.git_branch.create_branch")
@patch(f"{_D}.git_pr.get_pr_for_branch")
@patch(f"{_D}.git_branch.make_branch_name", return_value="feat/42-x")
def test_bootstrap_draft_pr_aborts_on_lookup_failure(
    _mk: MagicMock,
    mock_lookup: MagicMock,
    mock_create_branch: MagicMock,
    mock_create_pr: MagicMock,
) -> None:
    mock_lookup.return_value = PRLookup(found=False, lookup_failed=True)
    result = bootstrap_draft_pr("42", "Title", "plan body", MagicMock(), Path("/repo"))
    assert result is None  # never treated as "no PR"
    mock_create_branch.assert_not_called()
    mock_create_pr.assert_not_called()


@patch(f"{_B}.git_pr.create_pr")
@patch(f"{_B}.git_pr.get_pr_for_branch")
@patch(f"{_B}.git_repo.push_branch")
def test_create_review_pr_skips_on_lookup_failure(
    _push: MagicMock,
    mock_lookup: MagicMock,
    mock_create_pr: MagicMock,
) -> None:
    mock_lookup.return_value = PRLookup(found=False, lookup_failed=True)
    ctx = BatchReviewContext(integration_branch="wade/batch-1", tracking_issue="9")
    out = create_review_pr(Path("/repo"), ctx)
    assert out.pr_number is None  # ctx unchanged — no duplicate integration PR
    mock_create_pr.assert_not_called()
