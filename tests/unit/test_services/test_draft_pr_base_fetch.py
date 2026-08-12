"""Regression tests: bootstrap_draft_pr fetches a declared base before rejecting it.

A local remote-tracking cache miss is NOT proof the base is absent on origin — a
teammate may have just pushed it. bootstrap_draft_pr must fetch the specific ref
and re-resolve before failing, so a valid-but-unfetched base still creates the
draft PR (#376).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.pr import PRLookup
from wade.git.repo import GitError
from wade.services.implementation_service.draft_pr import bootstrap_draft_pr

_D = "wade.services.implementation_service.draft_pr"


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.project.main_branch = "main"
    cfg.project.branch_prefix = "feat"
    return cfg


@patch(f"{_D}.git_pr.create_pr", return_value={"number": 5, "url": "http://x/5"})
@patch(f"{_D}.git_repo.push_branch")
@patch(f"{_D}.git_branch.commits_ahead", return_value=1)
@patch(f"{_D}.git_branch.create_branch")
@patch(f"{_D}.git_branch.branch_exists", return_value=False)
@patch(f"{_D}.git_repo.fetch_ref")
@patch(f"{_D}.git_repo.has_remote", return_value=True)
@patch(f"{_D}.git_branch.resolve_start_point")
@patch(f"{_D}.git_pr.get_pr_for_branch", return_value=PRLookup(found=False))
@patch(f"{_D}.git_branch.make_branch_name", return_value="feat/42-x")
def test_fetches_then_resolves_uncached_base(
    _mk: MagicMock,
    _lookup: MagicMock,
    mock_resolve: MagicMock,
    mock_has_remote: MagicMock,
    mock_fetch: MagicMock,
    _exists: MagicMock,
    mock_create_branch: MagicMock,
    _ahead: MagicMock,
    _push: MagicMock,
    mock_create_pr: MagicMock,
) -> None:
    # First resolve misses (ref not yet fetched); after the fetch it resolves to
    # origin/release/x.
    mock_resolve.side_effect = [None, "origin/release/x"]

    result = bootstrap_draft_pr(
        "42", "Title", "plan body", _cfg(), Path("/repo"), base_branch="release/x"
    )

    assert result == {"number": 5, "url": "http://x/5"}
    # The fetch targeted the specific ref with an explicit remote-tracking refspec.
    mock_fetch.assert_called_once_with(
        Path("/repo"), "origin", "release/x:refs/remotes/origin/release/x"
    )
    # Branch was cut from the newly fetched start point, and the PR was created.
    mock_create_branch.assert_called_once_with(Path("/repo"), "feat/42-x", "origin/release/x")
    mock_create_pr.assert_called_once()
    assert mock_create_pr.call_args.kwargs["base"] == "release/x"


@patch(f"{_D}.git_pr.create_pr")
@patch(f"{_D}.git_branch.create_branch")
@patch(f"{_D}.git_repo.fetch_ref")
@patch(f"{_D}.git_repo.has_remote", return_value=True)
@patch(f"{_D}.git_branch.resolve_start_point", return_value=None)
@patch(f"{_D}.git_pr.get_pr_for_branch", return_value=PRLookup(found=False))
@patch(f"{_D}.git_branch.make_branch_name", return_value="feat/42-x")
def test_still_fails_when_base_absent_after_fetch(
    _mk: MagicMock,
    _lookup: MagicMock,
    _resolve: MagicMock,
    _has_remote: MagicMock,
    mock_fetch: MagicMock,
    mock_create_branch: MagicMock,
    mock_create_pr: MagicMock,
) -> None:
    # The base truly does not exist on origin — resolve misses both times.
    result = bootstrap_draft_pr(
        "42", "Title", "plan body", _cfg(), Path("/repo"), base_branch="ghost"
    )

    assert result is None
    mock_fetch.assert_called_once()  # a fetch was attempted before giving up
    mock_create_branch.assert_not_called()
    mock_create_pr.assert_not_called()


@patch(f"{_D}.git_pr.create_pr")
@patch(f"{_D}.git_branch.create_branch")
@patch(f"{_D}.git_repo.fetch_ref", side_effect=GitError("Could not resolve host"))
@patch(f"{_D}.git_repo.has_remote", return_value=True)
@patch(f"{_D}.git_branch.resolve_start_point", return_value=None)
@patch(f"{_D}.git_pr.get_pr_for_branch", return_value=PRLookup(found=False))
@patch(f"{_D}.git_branch.make_branch_name", return_value="feat/42-x")
def test_fetch_failure_reported_not_swallowed(
    _mk: MagicMock,
    _lookup: MagicMock,
    _resolve: MagicMock,
    _has_remote: MagicMock,
    mock_fetch: MagicMock,
    mock_create_branch: MagicMock,
    mock_create_pr: MagicMock,
) -> None:
    # A network/auth failure during fetch must not be misreported as "base absent";
    # bootstrap aborts (returns None) rather than proceeding on an unknown base.
    result = bootstrap_draft_pr(
        "42", "Title", "plan body", _cfg(), Path("/repo"), base_branch="develop"
    )

    assert result is None
    mock_fetch.assert_called_once()
    mock_create_branch.assert_not_called()
    mock_create_pr.assert_not_called()


@patch(f"{_D}.git_pr.create_pr")
@patch(f"{_D}.git_branch.create_branch")
@patch(f"{_D}.git_repo.fetch_ref")
@patch(f"{_D}.git_repo.has_remote", return_value=False)
@patch(f"{_D}.git_branch.resolve_start_point", return_value=None)
@patch(f"{_D}.git_pr.get_pr_for_branch", return_value=PRLookup(found=False))
@patch(f"{_D}.git_branch.make_branch_name", return_value="feat/42-x")
def test_no_remote_skips_fetch(
    _mk: MagicMock,
    _lookup: MagicMock,
    _resolve: MagicMock,
    _has_remote: MagicMock,
    mock_fetch: MagicMock,
    mock_create_branch: MagicMock,
    mock_create_pr: MagicMock,
) -> None:
    # No remote to fetch from — fail immediately without attempting a fetch.
    result = bootstrap_draft_pr(
        "42", "Title", "plan body", _cfg(), Path("/repo"), base_branch="develop"
    )

    assert result is None
    mock_fetch.assert_not_called()
    mock_create_branch.assert_not_called()
    mock_create_pr.assert_not_called()
