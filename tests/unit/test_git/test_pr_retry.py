"""Regression tests for gh retry + create_pr number handling (#357, B4/B5).

Also covers the stale-PR-head merge diagnosis (#414).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.git.pr import (
    GhCliError,
    PRLookup,
    PRRef,
    _diagnose_stale_pr_head,
    _get_branch_tip_oid,
    _get_pr_head_ref,
    _is_stale_pr_head_error,
    _is_transient_gh_error,
    create_pr,
    merge_pr,
)


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestTransientDetection:
    @pytest.mark.parametrize(
        "stderr",
        [
            "You have exceeded a secondary rate limit",
            "connection reset by peer",
            "HTTP 502: Bad Gateway",
            "HTTP 503 Service Unavailable",
            "i/o timeout",
            "unexpected EOF",
            "EOF occurred in violation of protocol",
        ],
    )
    def test_transient_errors(self, stderr: str) -> None:
        assert _is_transient_gh_error(stderr) is True

    @pytest.mark.parametrize(
        "stderr",
        [
            "HTTP 404: Not Found",
            "HTTP 422: Unprocessable Entity (a pull request already exists)",
            "gh auth login required",
            "no pull requests found for branch",
            # A bare "eof" substring must not match a benign echoed token.
            "fatal: couldn't find remote ref feat/eof-parser",
        ],
    )
    def test_permanent_errors(self, stderr: str) -> None:
        assert _is_transient_gh_error(stderr) is False


class TestRunGhRetry:
    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr.subprocess.run")
    def test_does_not_retry_permanent_404(self, mock_run: MagicMock, _sleep: MagicMock) -> None:
        # update_pr_body uses retries=3, but a 404 is permanent → one call only.
        mock_run.return_value = _proc(1, stderr="HTTP 404: Not Found")
        from wade.git.pr import update_pr_body

        ok = update_pr_body(Path("/repo"), 42, "body")
        assert ok is False
        assert mock_run.call_count == 1

    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr.subprocess.run")
    def test_retries_transient_5xx(self, mock_run: MagicMock, _sleep: MagicMock) -> None:
        # Two transient failures then success.
        mock_run.side_effect = [
            _proc(1, stderr="HTTP 502: Bad Gateway"),
            _proc(1, stderr="HTTP 502: Bad Gateway"),
            _proc(0, stdout=""),
        ]
        from wade.git.pr import update_pr_body

        ok = update_pr_body(Path("/repo"), 42, "body")
        assert ok is True
        assert mock_run.call_count == 3


class TestMergePrStateGuard:
    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr._get_pr_state", return_value="MERGED")
    @patch("wade.git.pr.subprocess.run")
    def test_already_merged_is_success_not_retried(
        self, mock_run: MagicMock, _state: MagicMock, _sleep: MagicMock
    ) -> None:
        # First merge attempt "fails" transiently, but the PR is already MERGED
        # remotely → treat as success, never re-attempt the irreversible merge.
        mock_run.return_value = _proc(1, stderr="connection reset")
        merge_pr(Path("/repo"), 7)  # must not raise
        assert mock_run.call_count == 1  # no retry once state == MERGED

    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr._get_pr_state", return_value="OPEN")
    @patch("wade.git.pr.subprocess.run")
    def test_permanent_failure_raises(
        self, mock_run: MagicMock, _state: MagicMock, _sleep: MagicMock
    ) -> None:
        mock_run.return_value = _proc(1, stderr="HTTP 422: not mergeable")
        with pytest.raises(GhCliError):
            merge_pr(Path("/repo"), 7)
        assert mock_run.call_count == 1  # permanent → no retry


class TestCreatePrStateGuard:
    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr.get_pr_for_branch")
    @patch("wade.git.pr.subprocess.run")
    def test_existing_pr_returned_not_recreated(
        self, mock_run: MagicMock, mock_lookup: MagicMock, _sleep: MagicMock
    ) -> None:
        # First create attempt "fails" (its response was lost to a transient
        # error), but a PR for the head branch already exists → return it and
        # never create a duplicate. gh pr create is not idempotent.
        mock_run.return_value = _proc(
            1, stderr="HTTP 422: a pull request already exists for o:feat/x"
        )
        mock_lookup.return_value = PRLookup(
            found=True, pr=PRRef(number=99, url="https://github.com/o/r/pull/99")
        )
        result = create_pr(Path("/repo"), "t", "b", "main", head="feat/x")
        assert result == {"number": 99, "url": "https://github.com/o/r/pull/99"}
        assert mock_run.call_count == 1  # never retried once the PR was found

    @patch("wade.git.pr._get_pr_info_from_url", return_value=None)
    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr.get_pr_for_branch")
    @patch("wade.git.pr.subprocess.run")
    def test_transient_failure_retried_when_no_pr_yet(
        self,
        mock_run: MagicMock,
        mock_lookup: MagicMock,
        _sleep: MagicMock,
        _info: MagicMock,
    ) -> None:
        # Transient failure with no PR yet → retry, then succeed on the URL.
        mock_run.side_effect = [
            _proc(1, stderr="HTTP 502: Bad Gateway"),
            _proc(0, stdout="https://github.com/o/r/pull/7"),
        ]
        mock_lookup.return_value = PRLookup(found=False, lookup_failed=False)
        result = create_pr(Path("/repo"), "t", "b", "main", head="feat/x")
        assert result == {"number": 7, "url": "https://github.com/o/r/pull/7"}
        assert mock_run.call_count == 2

    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr.get_pr_for_branch")
    @patch("wade.git.pr.subprocess.run")
    def test_permanent_failure_without_existing_pr_raises(
        self, mock_run: MagicMock, mock_lookup: MagicMock, _sleep: MagicMock
    ) -> None:
        # Permanent failure and no PR exists for the branch → raise, no retry.
        mock_run.return_value = _proc(1, stderr="HTTP 422: validation failed")
        mock_lookup.return_value = PRLookup(found=False, lookup_failed=False)
        with pytest.raises(GhCliError):
            create_pr(Path("/repo"), "t", "b", "main", head="feat/x")
        assert mock_run.call_count == 1


class TestCreatePrNumber:
    @patch("wade.git.pr._get_pr_info_from_url", return_value=None)
    @patch("wade.git.pr.subprocess.run")
    def test_parses_number_from_url_fallback(self, mock_run: MagicMock, _info: MagicMock) -> None:
        mock_run.return_value = _proc(0, stdout="https://github.com/o/r/pull/123")
        result = create_pr(Path("/repo"), "t", "b", "main")
        assert result == {"number": 123, "url": "https://github.com/o/r/pull/123"}

    @patch("wade.git.pr._get_pr_info_from_url", return_value=None)
    @patch("wade.git.pr.subprocess.run")
    def test_returns_none_when_number_undeterminable(
        self, mock_run: MagicMock, _info: MagicMock
    ) -> None:
        # No parseable /pull/<n> in the output → never fabricate #0.
        mock_run.return_value = _proc(0, stdout="created (no url)")
        result = create_pr(Path("/repo"), "t", "b", "main")
        assert result is None


# GitHub's actual GraphQL rejection phrasing seen in the #377 incident.
_STALE_STDERR = (
    "GraphQL: Head branch is out of date. Review and try the merge again. (mergePullRequest)"
)


def _json_proc(payload: dict[str, object]) -> MagicMock:
    return _proc(0, stdout=json.dumps(payload))


class TestStalePrHeadDetection:
    @pytest.mark.parametrize(
        "stderr",
        [
            _STALE_STDERR,
            "Head branch is out of date. Review and try the merge again.",
            "head branch is out of date",
        ],
    )
    def test_stale_head_matches(self, stderr: str) -> None:
        assert _is_stale_pr_head_error(stderr) is True

    @pytest.mark.parametrize(
        "stderr",
        [
            "HTTP 422: not mergeable",
            "connection reset by peer",
            # A different "out of date" phrase must not trip the matcher.
            "your branch is out of date with the base branch",
            "",
        ],
    )
    def test_non_stale_does_not_match(self, stderr: str) -> None:
        assert _is_stale_pr_head_error(stderr) is False


class TestGetPrHeadRef:
    @patch("wade.git.pr._run_gh")
    def test_returns_oid_and_name(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = _json_proc(
            {"headRefOid": "2698105aaaaaaaa", "headRefName": "feat/376-x"}
        )
        assert _get_pr_head_ref(Path("/repo"), 377) == ("2698105aaaaaaaa", "feat/376-x")

    @patch("wade.git.pr._run_gh")
    def test_non_zero_exit_returns_none(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = _proc(1, stderr="HTTP 404: Not Found")
        assert _get_pr_head_ref(Path("/repo"), 7) is None

    @pytest.mark.parametrize(
        "payload",
        [
            {"headRefName": "feat/x"},  # missing headRefOid
            {"headRefOid": "abc123"},  # missing headRefName
            {"headRefOid": "", "headRefName": "feat/x"},  # empty oid
            {"headRefOid": "abc123", "headRefName": ""},  # empty name
        ],
    )
    @patch("wade.git.pr._run_gh")
    def test_incomplete_json_returns_none(
        self, mock_gh: MagicMock, payload: dict[str, object]
    ) -> None:
        mock_gh.return_value = _json_proc(payload)
        assert _get_pr_head_ref(Path("/repo"), 7) is None

    @patch("wade.git.pr._run_gh")
    def test_malformed_json_returns_none(self, mock_gh: MagicMock) -> None:
        mock_gh.return_value = _proc(0, stdout="not json")
        assert _get_pr_head_ref(Path("/repo"), 7) is None


class TestDiagnoseStalePrHead:
    @patch("wade.git.pr._run_gh")
    def test_mismatch_returns_actionable_message(self, mock_gh: MagicMock) -> None:
        # PR record's head OID differs from the branch ref tip → stale-sync case.
        mock_gh.side_effect = [
            _json_proc({"headRefOid": "2698105aaaaaaaa", "headRefName": "feat/376-x"}),
            _json_proc({"object": {"sha": "e009513bbbbbbbb"}}),
        ]
        msg = _diagnose_stale_pr_head(Path("/repo"), 377)
        assert msg is not None
        assert "close 377" in msg and "reopen 377" in msg
        assert "2698105" in msg and "e009513" in msg

    @patch("wade.git.pr._run_gh")
    def test_matching_oids_returns_none(self, mock_gh: MagicMock) -> None:
        # OIDs equal → genuine branch-behind-base; no fabricated diagnosis.
        mock_gh.side_effect = [
            _json_proc({"headRefOid": "abc123456789", "headRefName": "feat/x"}),
            _json_proc({"object": {"sha": "abc123456789"}}),
        ]
        assert _diagnose_stale_pr_head(Path("/repo"), 7) is None

    @patch("wade.git.pr._run_gh")
    def test_head_fetch_failure_returns_none(self, mock_gh: MagicMock) -> None:
        # PR head lookup fails → None, and the branch tip is never probed.
        mock_gh.side_effect = [_proc(1, stderr="HTTP 404: Not Found")]
        assert _diagnose_stale_pr_head(Path("/repo"), 7) is None
        assert mock_gh.call_count == 1

    @patch("wade.git.pr._run_gh")
    def test_branch_tip_fetch_failure_returns_none(self, mock_gh: MagicMock) -> None:
        mock_gh.side_effect = [
            _json_proc({"headRefOid": "abc123456789", "headRefName": "feat/x"}),
            _proc(1, stderr="HTTP 404: Not Found"),
        ]
        assert _diagnose_stale_pr_head(Path("/repo"), 7) is None

    @patch("wade.git.pr._run_gh")
    def test_branch_tip_uses_singular_exact_match_path(self, mock_gh: MagicMock) -> None:
        # The gh api ref lookup MUST use the SINGULAR `git/ref/heads/<branch>`
        # exact-match path (single object with .object.sha), NOT the plural
        # `git/refs/...` prefix form (returns an array). This assertion stops a
        # future refactor from silently reintroducing the array/prefix bug.
        mock_gh.return_value = _json_proc({"object": {"sha": "deadbeef123"}})
        _get_branch_tip_oid(Path("/repo"), "feat/376-x")
        args = mock_gh.call_args.args
        assert args[0] == "api"
        assert args[1] == "repos/{owner}/{repo}/git/ref/heads/feat/376-x"
        assert "git/refs/" not in args[1]


class TestMergePrStaleHead:
    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr._diagnose_stale_pr_head")
    @patch("wade.git.pr._get_pr_state", return_value="OPEN")
    @patch("wade.git.pr.subprocess.run")
    def test_stale_head_mismatch_raises_diagnosis_not_raw(
        self,
        mock_run: MagicMock,
        _state: MagicMock,
        mock_diag: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        # Stale-head rejection that never clears → after the bounded retries the
        # diagnosis replaces GitHub's misleading raw string.
        mock_run.return_value = _proc(1, stderr=_STALE_STDERR)
        mock_diag.return_value = (
            "PR #7 merge blocked: GitHub's PR record is stale (head 2698105 != "
            "branch tip e009513). ... gh pr close 7 && gh pr reopen 7 then retry."
        )
        with pytest.raises(GhCliError) as exc:
            merge_pr(Path("/repo"), 7)
        msg = str(exc.value)
        assert "gh pr close 7" in msg and "reopen 7" in msg
        assert "Head branch is out of date" not in msg  # raw string replaced
        # 1 initial attempt + 3 bounded retries before the terminal diagnosis.
        assert mock_run.call_count == 4

    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr._diagnose_stale_pr_head", return_value=None)
    @patch("wade.git.pr._get_pr_state", return_value="OPEN")
    @patch("wade.git.pr.subprocess.run")
    def test_stale_head_falls_back_to_raw_when_no_diagnosis(
        self,
        mock_run: MagicMock,
        _state: MagicMock,
        _diag: MagicMock,
        _sleep: MagicMock,
    ) -> None:
        # Diagnosis returns None (OIDs equal, or GitHub synced mid-retry) → the
        # raw error is surfaced, never a fabricated diagnosis.
        mock_run.return_value = _proc(1, stderr=_STALE_STDERR)
        with pytest.raises(GhCliError) as exc:
            merge_pr(Path("/repo"), 7)
        msg = str(exc.value)
        assert "Head branch is out of date" in msg  # raw error preserved
        assert "gh pr close" not in msg

    @patch("wade.git.pr.time.sleep")
    @patch("wade.git.pr._get_pr_state", return_value="OPEN")
    @patch("wade.git.pr.subprocess.run")
    def test_stale_head_then_success_merges_without_raising(
        self, mock_run: MagicMock, _state: MagicMock, _sleep: MagicMock
    ) -> None:
        # A genuine push→merge race: the stale-head failure is absorbed by the
        # bounded fast-retry, then the merge succeeds.
        mock_run.side_effect = [_proc(1, stderr=_STALE_STDERR), _proc(0)]
        merge_pr(Path("/repo"), 7)  # must not raise
        assert mock_run.call_count == 2
