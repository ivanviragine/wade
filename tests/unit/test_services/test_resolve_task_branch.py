"""Tests for branch resolution by stable issue number.

``resolve_task_branch`` / ``find_existing_branch_for_issue`` resolve a task's
branch by its issue *number* rather than a re-slugified title. The slug is
frozen at ``wade implement`` time, so a title edited afterward (commonly:
``done`` renaming the issue to conventional-commit form when its PR opens) must
NOT change which branch/worktree/PR a re-run acts on. Regression coverage for
the ``implement``/``smart_start`` resume paths and #417's review path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wade.git.branch import make_branch_name
from wade.git.repo import GitError
from wade.models.worktree import Worktree
from wade.services.implementation_service._shared import (
    find_existing_branch_for_issue,
    resolve_task_branch,
)

_S = "wade.services.implementation_service._shared"


class TestFindExistingBranchForIssue:
    """Resolve / disambiguate an existing branch by issue number (#417 review)."""

    def test_prefers_reconstructed_name_on_ambiguity(self, tmp_path: Path) -> None:
        """>1 same-issue branch, no worktree → prefer the freshest (reconstructed) name."""
        with (
            patch(f"{_S}.git_worktree.list_worktrees", return_value=[]),
            patch(
                f"{_S}.git_branch.list_branch_names",
                return_value={"main", "feat/42-old-slug", "feat/42-new-slug"},
            ),
        ):
            got = find_existing_branch_for_issue(tmp_path, "42", preferred="feat/42-new-slug")
        assert got == "feat/42-new-slug"

    def test_ambiguity_without_preference_is_deterministic(self, tmp_path: Path) -> None:
        """Ambiguous match without a preference is sorted (stable), not hash-ordered."""
        branches = {"main", "feat/42-old-slug", "feat/42-new-slug"}
        with (
            patch(f"{_S}.git_worktree.list_worktrees", return_value=[]),
            patch(f"{_S}.git_branch.list_branch_names", return_value=branches),
        ):
            first = find_existing_branch_for_issue(tmp_path, "42")
            second = find_existing_branch_for_issue(tmp_path, "42")
        assert first == second == "feat/42-new-slug"  # sorted(): "new" < "old"

    def test_worktree_branch_wins_over_reconstructed(self, tmp_path: Path) -> None:
        """A live worktree's branch is authoritative even when a preferred name is given."""
        with patch(
            f"{_S}.git_worktree.list_worktrees",
            return_value=[Worktree(path=str(tmp_path / "wt"), branch="feat/42-frozen-slug")],
        ):
            got = find_existing_branch_for_issue(tmp_path, "42", preferred="feat/42-renamed")
        assert got == "feat/42-frozen-slug"

    def test_remote_branch_matched_when_no_worktree(self, tmp_path: Path) -> None:
        """A cleaned-up worktree still resolves to the real remote branch."""
        with (
            patch(f"{_S}.git_worktree.list_worktrees", return_value=[]),
            patch(
                f"{_S}.git_branch.list_branch_names",
                return_value={"main", "origin/feat/42-frozen-slug"},
            ),
        ):
            got = find_existing_branch_for_issue(tmp_path, "42")
        assert got == "feat/42-frozen-slug"

    def test_no_match_returns_none(self, tmp_path: Path) -> None:
        with (
            patch(f"{_S}.git_worktree.list_worktrees", return_value=[]),
            patch(f"{_S}.git_branch.list_branch_names", return_value={"main", "feat/99-other"}),
        ):
            assert find_existing_branch_for_issue(tmp_path, "42") is None

    def test_sub_issue_number_is_not_matched(self, tmp_path: Path) -> None:
        """Issue 42 must not match branch for issue 420 (full-number extraction)."""
        with (
            patch(f"{_S}.git_worktree.list_worktrees", return_value=[]),
            patch(f"{_S}.git_branch.list_branch_names", return_value={"feat/420-unrelated"}),
        ):
            assert find_existing_branch_for_issue(tmp_path, "42") is None


class TestResolveTaskBranch:
    """Full resolution order: checked-out branch → existing branch → reconstruct."""

    def test_current_checked_out_branch_wins(self, tmp_path: Path) -> None:
        """An in-worktree caller on the issue's branch gets that exact ref."""
        with patch(f"{_S}.git_repo.get_current_branch", return_value="feat/42-frozen-slug"):
            got = resolve_task_branch(tmp_path, "42", "Renamed title", "feat")
        assert got == "feat/42-frozen-slug"

    def test_retitled_issue_resolves_to_existing_worktree_branch(self, tmp_path: Path) -> None:
        """The core bug: title changed since implement → resume the real branch.

        Running ``wade 42`` from the main repo (not on the issue branch) with a
        title that would now re-slugify differently must still resolve to the
        branch the worktree/PR actually live on, not the drifted reconstruction.
        """
        frozen = "feat/42-original-title-slug"
        with (
            patch(f"{_S}.git_repo.get_current_branch", return_value="main"),
            patch(
                f"{_S}.git_worktree.list_worktrees",
                return_value=[Worktree(path=str(tmp_path / "wt"), branch=frozen)],
            ),
        ):
            got = resolve_task_branch(tmp_path, "42", "fix: completely renamed title", "feat")
        assert got == frozen
        # Guard: the naive path would have produced a different, orphaning name.
        assert got != make_branch_name("feat", 42, "fix: completely renamed title")

    def test_first_time_reconstructs_from_title(self, tmp_path: Path) -> None:
        """No checked-out/worktree/local/remote branch → reconstruct from title."""
        with (
            patch(f"{_S}.git_repo.get_current_branch", side_effect=GitError("detached")),
            patch(f"{_S}.git_worktree.list_worktrees", return_value=[]),
            patch(f"{_S}.git_branch.list_branch_names", return_value={"main"}),
        ):
            got = resolve_task_branch(tmp_path, "42", "Add user auth", "feat")
        assert got == make_branch_name("feat", 42, "Add user auth")

    def test_detached_head_falls_through_to_existing_branch(self, tmp_path: Path) -> None:
        """A GitError reading the current branch must not abort resolution."""
        with (
            patch(f"{_S}.git_repo.get_current_branch", side_effect=GitError("detached")),
            patch(f"{_S}.git_worktree.list_worktrees", return_value=[]),
            patch(
                f"{_S}.git_branch.list_branch_names",
                return_value={"main", "feat/42-frozen-slug"},
            ),
        ):
            got = resolve_task_branch(tmp_path, "42", "Renamed", "feat")
        assert got == "feat/42-frozen-slug"
