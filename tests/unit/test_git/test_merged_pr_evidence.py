"""Payload, identity and pagination contracts for exact merged-PR lookup."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from wade.git.pr import find_exact_merged_pr, origin_github_repository
from wade.git.repo import GitError
from wade.models.merge_evidence import GitHubRepository, MergeEvidenceState

HEAD = "a" * 40
MERGE = "b" * 40
REPOSITORY = GitHubRepository(
    host="github.com", owner="owner", name="repo", origin_url="git@github.com:owner/repo.git"
)
ROW = {
    "number": 7,
    "state": "closed",
    "merged_at": "2026-10-14T12:00:00Z",
    "merge_commit_sha": MERGE,
    "head": {
        "ref": "feat/7-fix",
        "sha": HEAD,
        "repo": {"full_name": "owner/repo", "html_url": "https://github.com/owner/repo"},
    },
    "base": {
        "ref": "main",
        "sha": MERGE,
        "repo": {"full_name": "owner/repo", "html_url": "https://github.com/owner/repo"},
    },
}


def lookup(payload: object) -> object:
    return subprocess.CompletedProcess([], 0, json.dumps(payload), "")


def test_explicit_host_repository_ignores_gh_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_REPO", "other/wrong")
    with patch("wade.git.pr._run_gh", return_value=lookup([ROW])) as query:
        result = find_exact_merged_pr(Path("/repo"), REPOSITORY, "feat/7-fix", HEAD, "main")
    assert result.state == MergeEvidenceState.VERIFIED
    assert result.pr is not None and result.pr.head == HEAD and result.pr.merge_commit == MERGE
    args = query.call_args.args
    assert args[:4] == ("api", "--hostname", "github.com", "repos/owner/repo/pulls")
    assert "head=owner:feat/7-fix" in args and "base=main" in args


@pytest.mark.parametrize(
    "path,value",
    [
        (("head", "sha"), None),
        (("head", "sha"), "broken"),
        (("merge_commit_sha",), None),
        (("merge_commit_sha",), "broken"),
        (("number",), True),
        (("number",), "7"),
        (("state",), "open"),
        (("head", "repo"), None),
        (("merged_at",), ""),
        (("merged_at",), "not-a-timestamp"),
        (("merged_at",), "2026-10-14T12:00:00"),
    ],
)
def test_malformed_evidence_is_unknown(path: tuple[str, ...], value: object) -> None:
    row = copy.deepcopy(ROW)
    target = row
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with patch("wade.git.pr._run_gh", return_value=lookup([row])):
        result = find_exact_merged_pr(Path("/repo"), REPOSITORY, "feat/7-fix", HEAD, "main")
    assert result.state == MergeEvidenceState.UNKNOWN


@pytest.mark.parametrize(
    "error",
    [
        "HTTP 404 repository missing",
        "HTTP 401 bad credentials",
        "HTTP 403 rate limit",
        "connection reset",
        "gh CLI not found",
    ],
)
def test_lookup_failure_is_distinct_from_no_match(error: str) -> None:
    with patch("wade.git.pr._run_gh", side_effect=GitError(error)):
        result = find_exact_merged_pr(Path("/repo"), REPOSITORY, "feat/7-fix", HEAD, "main")
    assert result.state == MergeEvidenceState.UNKNOWN and error in result.reason
    with patch("wade.git.pr._run_gh", return_value=lookup([])):
        missing = find_exact_merged_pr(Path("/repo"), REPOSITORY, "feat/7-fix", HEAD, "main")
    assert missing.state == MergeEvidenceState.NOT_MATCHED


def test_exhausts_pages_to_find_recorded_head_on_reused_branch() -> None:
    page = []
    for number in range(100):
        row = copy.deepcopy(ROW)
        row["number"] = number + 100
        row["head"]["sha"] = "c" * 40
        page.append(row)
    with patch("wade.git.pr._run_gh", side_effect=[lookup(page), lookup([ROW])]) as query:
        result = find_exact_merged_pr(Path("/repo"), REPOSITORY, "feat/7-fix", HEAD, "main")
    assert query.call_count == 2 and "page=2" in query.call_args.args
    assert result.state == MergeEvidenceState.VERIFIED


def test_ambiguous_records_fail_closed() -> None:
    second = copy.deepcopy(ROW)
    second["number"] = 8
    with patch("wade.git.pr._run_gh", return_value=lookup([ROW, second])):
        result = find_exact_merged_pr(Path("/repo"), REPOSITORY, "feat/7-fix", HEAD, "main")
    assert result.state == MergeEvidenceState.UNKNOWN


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:owner/repo.git",
        "https://github.com/owner/repo.git",
        "https://github.com/owner/repo",
        "ssh://git@github.com/owner/repo.git",
    ],
)
def test_supported_origin_spellings(url: str) -> None:
    with patch(
        "wade.git.pr._run_git", return_value=subprocess.CompletedProcess([], 0, url + "\n", "")
    ):
        repository = origin_github_repository(Path("/repo"))
    assert repository.model_dump() == {
        "host": "github.com",
        "owner": "owner",
        "name": "repo",
        "origin_url": url,
    }


@pytest.mark.parametrize(
    "url",
    [
        "/local/repo",
        "https://github.com/owner/repo/extra",
        "https://user@github.com/owner/repo",
        "ssh://git@github.com:222/owner/repo",
        "https://github.com/owner/repo?query",
        "https://github.com/owner/repo\nhttps://github.com/other/repo",
    ],
)
def test_unsupported_or_ambiguous_origin_is_unknown(url: str) -> None:
    with (
        patch(
            "wade.git.pr._run_git", return_value=subprocess.CompletedProcess([], 0, url + "\n", "")
        ),
        pytest.raises((GitError, ValueError)),
    ):
        origin_github_repository(Path("/repo"))


def test_repeated_full_page_is_incomplete_not_proof() -> None:
    page = []
    for number in range(100):
        row = copy.deepcopy(ROW)
        row["number"] = number + 100
        page.append(row)
    with patch("wade.git.pr._run_gh", side_effect=[lookup(page), lookup(page)]):
        result = find_exact_merged_pr(Path("/repo"), REPOSITORY, "feat/7-fix", HEAD, "main")
    assert result.state == MergeEvidenceState.UNKNOWN
    assert "repeated PR identity" in result.reason
