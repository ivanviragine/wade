"""Concurrency lane — B3: an issue with two PRs classifies deterministically (#357).

When the same issue has more than one PR (e.g. a reopened/duplicate), batch
status must not be last-wins over the ``gh pr list`` order. It picks
deterministically (open non-draft, then most-recently-updated, then highest
number), so repeated polls in a running batch never flap.
"""

from __future__ import annotations

import itertools
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


def _picks_over_all_orderings(tmp_path: Path, prs: list[PRSummary]) -> set[int]:
    """Return the set of PRs picked across every permutation of *prs*."""
    picks: set[int] = set()
    for ordering in itertools.permutations(prs):
        with patch("wade.git.pr.list_prs", return_value=list(ordering)):
            index = _build_pr_index(tmp_path, ["42"])
        assert index["42"].number is not None
        picks.add(index["42"].number)
    return picks


def test_competing_open_prs_pick_most_recently_updated(tmp_path: Path) -> None:
    # TWO eligible (open, non-draft) PRs — selection must be order-independent
    # and pick the most-recently-updated one, not last-wins over gh's order.
    prs = [
        _pr(11, state="OPEN", draft=False, updated="2026-01-01"),
        _pr(13, state="OPEN", draft=False, updated="2026-06-01"),  # newer
        _pr(10, state="CLOSED", draft=False, updated="2026-12-01"),  # newest but closed
    ]
    # #13 wins: the newer OPEN non-draft. The CLOSED #10's newer timestamp is
    # irrelevant — open-non-draft dominates the sort key.
    assert _picks_over_all_orderings(tmp_path, prs) == {13}


def test_competing_open_prs_updated_tie_breaks_by_highest_number(tmp_path: Path) -> None:
    # Two eligible PRs with IDENTICAL updatedAt → the higher PR number wins,
    # deterministically, regardless of input order.
    prs = [
        _pr(11, state="OPEN", draft=False, updated="2026-06-01"),
        _pr(14, state="OPEN", draft=False, updated="2026-06-01"),  # same time, higher #
    ]
    assert _picks_over_all_orderings(tmp_path, prs) == {14}
