"""Unit boundaries for cleanup refusal and conservative deletion (#492)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from wade.git.repo import GitError
from wade.models.merge_evidence import MergeAssessment, MergeEvidenceState
from wade.models.worktree import Worktree
from wade.services.implementation_service.cleanup import _cleanup_worktree, _worktree_loss_risk

CLEANUP = "wade.services.implementation_service.cleanup"
PROOF = MergeAssessment(
    state=MergeEvidenceState.VERIFIED, reason="contained", branch="feat/42-x", head="a" * 40
)


def test_dirty_is_independent_of_merge_proof(tmp_path: Path) -> None:
    with (
        patch(f"{CLEANUP}.git_repo.is_clean", return_value=False),
        patch(
            f"{CLEANUP}.git_repo.get_dirty_status",
            return_value={"staged": 1, "unstaged": 2, "untracked": 3},
        ),
        patch(f"{CLEANUP}.git_merge.revalidate_assessment"),
    ):
        losses = _worktree_loss_risk(tmp_path, tmp_path, "feat/42-x", "main", assessment=PROOF)
    assert losses == ["uncommitted changes (1 staged, 2 unstaged, 3 untracked)"]


def test_status_error_is_unsafe(tmp_path: Path) -> None:
    with (
        patch(f"{CLEANUP}.git_repo.is_clean", side_effect=GitError("status failed")),
        patch(f"{CLEANUP}.git_merge.revalidate_assessment"),
    ):
        losses = _worktree_loss_risk(tmp_path, tmp_path, "feat/42-x", "main", assessment=PROOF)
    assert losses == ["uncommitted changes (could not verify — treating as unsafe)"]


@pytest.mark.parametrize("branch", [None, "(detached)", "main"])
def test_unknown_or_primary_branch_is_unsafe(tmp_path: Path, branch: str | None) -> None:
    with patch(f"{CLEANUP}.git_repo.is_clean", return_value=True):
        assert _worktree_loss_risk(tmp_path, tmp_path, branch, "main", assessment=PROOF)


@pytest.mark.parametrize("discard", [False, True])
def test_only_explicit_discard_forces_worktree_and_branch_removal(discard: bool) -> None:
    with (
        patch(f"{CLEANUP}.git_repo.main_checkout_root", side_effect=lambda p: p),
        patch(
            f"{CLEANUP}._worktree_identity",
            return_value=Worktree(path="/repo/wt", branch="feat/42-x", head=PROOF.head),
        ),
        patch(f"{CLEANUP}.git_merge.assess_branch", return_value=PROOF),
        patch(f"{CLEANUP}._worktree_loss_risk", return_value=[]),
        patch(f"{CLEANUP}._preserve_session_data"),
        patch(f"{CLEANUP}.git_worktree.remove_worktree") as remove,
        patch(f"{CLEANUP}.git_branch.delete_branch", side_effect=GitError("not merged")) as delete,
        patch(f"{CLEANUP}.git_worktree.prune_worktrees"),
    ):
        assert _cleanup_worktree(Path("/repo"), Path("/repo/wt"), "main", discard_dirty=discard)
    remove.assert_called_once_with(Path("/repo"), Path("/repo/wt"), force=discard)
    delete.assert_called_once_with(Path("/repo"), "feat/42-x", force=discard)


def test_missing_inventory_never_skips_commit_guard(tmp_path: Path) -> None:
    with (
        patch(f"{CLEANUP}.git_repo.main_checkout_root", return_value=tmp_path),
        patch(f"{CLEANUP}.git_worktree.list_worktrees", return_value=[]),
        patch(f"{CLEANUP}.git_worktree.remove_worktree") as remove,
    ):
        assert not _cleanup_worktree(tmp_path, tmp_path / "missing", "main")
    remove.assert_not_called()
