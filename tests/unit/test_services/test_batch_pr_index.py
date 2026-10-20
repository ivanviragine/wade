"""Regression tests for deterministic batch PR classification (#357, B3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wade.git.pr import PRSummary
from wade.services.implementation_service.batch import (
    _BATCH_STATUS_UNKNOWN,
    _build_pr_index,
    _classify_issue_status,
    _pick_pr,
)


def _pr(number: int, *, state: str = "OPEN", draft: bool = False, updated: str = "") -> PRSummary:
    return PRSummary(
        number=number,
        url=f"http://pr/{number}",
        headRefName="feat/42-x",
        state=state,
        isDraft=draft,
        mergedAt=None,
        updatedAt=updated or None,
    )


class TestPickPr:
    def test_prefers_open_non_draft(self) -> None:
        draft = _pr(1, state="OPEN", draft=True, updated="2026-01-02")
        closed = _pr(2, state="CLOSED", updated="2026-01-03")
        open_ready = _pr(3, state="OPEN", draft=False, updated="2026-01-01")
        assert _pick_pr([draft, closed, open_ready]).number == 3

    def test_tiebreak_by_most_recently_updated(self) -> None:
        older = _pr(5, state="OPEN", updated="2026-01-01")
        newer = _pr(4, state="OPEN", updated="2026-06-01")
        assert _pick_pr([older, newer]).number == 4

    def test_final_tiebreak_by_highest_number(self) -> None:
        a = _pr(7, state="OPEN", updated="2026-01-01")
        b = _pr(9, state="OPEN", updated="2026-01-01")
        assert _pick_pr([a, b]).number == 9


class TestBuildPrIndexDeterministic:
    def test_multiple_prs_for_one_issue_pick_is_stable(self, tmp_path: Path) -> None:
        prs = [
            _pr(10, state="CLOSED", updated="2026-05-01"),
            _pr(11, state="OPEN", draft=False, updated="2026-01-01"),
            _pr(12, state="OPEN", draft=True, updated="2026-09-01"),
        ]
        with patch("wade.git.pr.list_prs", return_value=list(prs)):
            first = _build_pr_index(tmp_path, ["42"])
        # Reversed input must yield the same choice — no last-wins.
        with patch("wade.git.pr.list_prs", return_value=list(reversed(prs))):
            second = _build_pr_index(tmp_path, ["42"])
        assert first["42"].number == 11  # open non-draft wins
        assert second["42"].number == 11

    def test_truncation_warns(self, tmp_path: Path) -> None:
        many = [
            PRSummary(
                number=n,
                url=f"http://pr/{n}",
                headRefName=f"feat/{n}-x",
                state="OPEN",
                isDraft=False,
            )
            for n in range(200)
        ]
        with (
            patch("wade.git.pr.list_prs", return_value=many),
            patch("wade.services.implementation_service.batch.console") as mock_console,
        ):
            _build_pr_index(tmp_path, ["1"])
        mock_console.warn.assert_called_once()


class TestClassifyUnknown:
    def test_branch_exists_but_unreadable_is_unknown(self, tmp_path: Path) -> None:
        from wade.git.repo import GitError

        branches = {"origin/feat/42-x"}
        with (
            patch(
                "wade.services.implementation_service.batch._is_merged_to_main",
                return_value=False,
            ),
            patch("wade.git.branch.commits_ahead", side_effect=GitError("bad ref")),
        ):
            result = _classify_issue_status("42", {}, branches, "main", tmp_path)
        assert result == _BATCH_STATUS_UNKNOWN


class TestOpaqueBatchIdentity:
    """Batch status consumes exact decoded IDs — no prefix or zero-padding matching (#490)."""

    def test_pr_index_maps_opaque_heads_exactly(self, tmp_path: Path) -> None:
        heads = {
            1: "feat/idv1_61626331323334-x",  # abc1234
            2: "feat/idv1_41424331323334-x",  # ABC1234
            3: "feat/0042-x",
            4: "feat/42-x",
        }
        prs = [
            _pr(number).model_copy(update={"head_ref_name": head}) for number, head in heads.items()
        ]
        with patch("wade.git.pr.list_prs", return_value=prs):
            index = _build_pr_index(tmp_path, ["abc1234", "0042", "42"])
        assert {issue: pr.number for issue, pr in index.items()} == {"abc1234": 1, "42": 4}

    def test_branch_status_does_not_conflate_zero_padded_or_longer_ids(
        self, tmp_path: Path
    ) -> None:
        branches = {"origin/feat/0042-x", "feat/420-x", "feat/idv1_61626331323334-x"}
        with (
            patch(
                "wade.services.implementation_service.batch.git_branch.commits_ahead",
                return_value=1,
            ),
            patch(
                "wade.services.implementation_service.batch.git_branch.is_merged_into",
                return_value=False,
            ),
        ):
            assert _classify_issue_status("42", {}, branches, "main", tmp_path) == "not_started"
            assert _classify_issue_status("abc1234", {}, branches, "main", tmp_path) == (
                "in_progress"
            )
