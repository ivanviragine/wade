"""Concurrency lane — B3: an issue with two PRs classifies deterministically (#357).

When the same issue has more than one PR (e.g. a reopened/duplicate), batch
status must not be last-wins over the ``gh pr list`` order. It picks
deterministically (open non-draft, then most-recently-updated, then highest
number), so repeated polls in a running batch never flap.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from wade.git.pr import PRSummary
from wade.services.implementation_service.batch import _build_pr_index

pytestmark = pytest.mark.concurrency


def _pr(number: int, *, state: str, draft: bool, updated: str) -> PRSummary:
    return PRSummary(
        number=number,
        url=f"http://pr/{number}",
        headRefName="feat/42-x",
        state=state,
        isDraft=draft,
        mergedAt=None,
        updatedAt=updated,
    )


def test_two_prs_for_one_issue_pick_is_order_independent(tmp_path: Path) -> None:
    prs = [
        _pr(10, state="CLOSED", draft=False, updated="2026-09-01"),
        _pr(11, state="OPEN", draft=False, updated="2026-01-01"),
        _pr(12, state="OPEN", draft=True, updated="2026-12-01"),
    ]

    picks: set[int] = set()
    for ordering in (prs, list(reversed(prs)), [prs[1], prs[0], prs[2]]):
        with patch("wade.git.pr.list_prs", return_value=list(ordering)):
            index = _build_pr_index(tmp_path, ["42"])
        assert index["42"].number is not None
        picks.add(index["42"].number)

    # Every ordering yields the SAME PR: the open non-draft one (#11).
    assert picks == {11}
