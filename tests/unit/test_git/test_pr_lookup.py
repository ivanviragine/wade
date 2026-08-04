"""Regression tests for the typed PR lookup (issue #357, defect B1).

``get_pr_for_branch`` must distinguish three realities that the old
``dict | None`` return type conflated: no PR exists, the lookup itself failed
(transient/permanent ``gh`` error), and a PR exists with a known state. No
caller may act on a merged/closed PR as if it were open.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.pr import PRLookup, PRRef, get_pr_for_branch


def _gh_result(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


class TestGetPrForBranchLookup:
    @patch("wade.git.pr._run_gh")
    def test_open_pr_is_found_and_open(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = _gh_result(
            0,
            stdout='{"number": 42, "url": "u", "title": "t", "state": "OPEN", "isDraft": false}',
        )
        lookup = get_pr_for_branch(Path("/repo"), "feat/42-x")
        assert lookup.found is True
        assert lookup.lookup_failed is False
        assert lookup.is_open is True
        assert lookup.is_merged is False
        assert lookup.is_closed_or_merged is False
        assert lookup.number == 42
        assert lookup.url == "u"
        assert lookup.state == "OPEN"

    @patch("wade.git.pr._run_gh")
    def test_merged_pr_is_found_but_not_open(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = _gh_result(
            0, stdout='{"number": 7, "url": "u", "state": "MERGED", "isDraft": false}'
        )
        lookup = get_pr_for_branch(Path("/repo"), "feat/7-x")
        assert lookup.found is True
        assert lookup.is_open is False
        assert lookup.is_merged is True
        assert lookup.is_closed_or_merged is True

    @patch("wade.git.pr._run_gh")
    def test_closed_pr_is_closed_or_merged_but_not_merged(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = _gh_result(
            0, stdout='{"number": 9, "state": "CLOSED", "isDraft": false}'
        )
        lookup = get_pr_for_branch(Path("/repo"), "feat/9-x")
        assert lookup.is_open is False
        assert lookup.is_merged is False
        assert lookup.is_closed_or_merged is True

    @patch("wade.git.pr._run_gh")
    def test_draft_state_is_exposed(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = _gh_result(
            0, stdout='{"number": 3, "state": "OPEN", "isDraft": true}'
        )
        lookup = get_pr_for_branch(Path("/repo"), "feat/3-x")
        assert lookup.is_draft is True

    @patch("wade.git.pr._run_gh")
    def test_no_pr_signal_is_not_a_lookup_failure(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = _gh_result(1, stderr='no pull requests found for branch "feat/1-x"')
        lookup = get_pr_for_branch(Path("/repo"), "feat/1-x")
        assert lookup.found is False
        assert lookup.lookup_failed is False
        assert lookup.pr is None
        assert lookup.number is None

    @patch("wade.git.pr._run_gh")
    def test_transient_error_is_a_lookup_failure_not_no_pr(self, mock_gh: MagicMock) -> None:
        # A network/auth error exits non-zero WITHOUT the "no PR" message; this
        # must NOT be reported as "no PR" — callers retry/report instead.
        mock_gh.return_value = _gh_result(
            1, stderr="error connecting to api.github.com: connection reset by peer"
        )
        lookup = get_pr_for_branch(Path("/repo"), "feat/2-x")
        assert lookup.found is False
        assert lookup.lookup_failed is True

    @patch("wade.git.pr._run_gh")
    def test_unparseable_output_is_a_lookup_failure(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = _gh_result(0, stdout="not json at all")
        lookup = get_pr_for_branch(Path("/repo"), "feat/4-x")
        assert lookup.found is False
        assert lookup.lookup_failed is True


class TestPRLookupProperties:
    def test_empty_lookup_defaults(self) -> None:
        lookup = PRLookup()
        assert lookup.found is False
        assert lookup.lookup_failed is False
        assert lookup.pr is None
        assert lookup.state == ""
        assert lookup.number is None
        assert lookup.url == ""
        assert lookup.is_open is False
        assert lookup.is_closed_or_merged is False

    def test_state_matching_is_case_insensitive(self) -> None:
        lookup = PRLookup(found=True, pr=PRRef(number=1, state="open"))
        assert lookup.is_open is True
        merged = PRLookup(found=True, pr=PRRef(number=1, state="merged"))
        assert merged.is_merged is True
