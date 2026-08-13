"""Regression tests: retargeting an existing draft PR re-roots a scaffold branch.

Editing a PR's base does not rewrite its head branch's ancestry. A scaffold branch
cut from the old base (e.g. ``develop``) keeps that base's commits, which would then
merge into the new base (``main``) once the PR is retargeted. ``bootstrap_draft_pr``
re-roots a scaffold-only branch on the new base first — loss-free — before retargeting
(#376 review). Branches carrying real work (a worktree, or commits past the scaffold)
are left untouched.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.pr import PRLookup, PRRef
from wade.services.implementation_service.draft_pr import (
    bootstrap_draft_pr,
    reroot_scaffold_branch_for_retarget,
)

_D = "wade.services.implementation_service.draft_pr"


def _cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.project.main_branch = "main"
    cfg.project.branch_prefix = "feat"
    return cfg


def _open_pr(base: str) -> PRLookup:
    return PRLookup(
        found=True,
        pr=PRRef(number=5, url="http://x/5", state="OPEN", baseRefName=base),
    )


class TestRerootScaffoldBranch:
    @patch(f"{_D}.git_repo.push_branch")
    @patch(f"{_D}.git_branch.create_scaffold_commit")
    @patch(f"{_D}.git_branch.reset_branch")
    @patch(f"{_D}.git_branch.commits_ahead", return_value=1)
    @patch(f"{_D}.git_branch.resolve_start_point")
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    def test_reroots_scaffold_only_branch(
        self,
        _wt: MagicMock,
        mock_resolve: MagicMock,
        _ahead: MagicMock,
        mock_reset: MagicMock,
        mock_scaffold: MagicMock,
        mock_push: MagicMock,
    ) -> None:
        # resolve_start_point is called for the OLD base (commits check) then the
        # NEW base (recreate). Both resolve locally.
        mock_resolve.side_effect = ["develop", "main"]

        ok = reroot_scaffold_branch_for_retarget(
            Path("/repo"), "feat/42-x", "develop", "main", "42"
        )

        assert ok is True
        mock_reset.assert_called_once_with(Path("/repo"), "feat/42-x", "main")
        mock_scaffold.assert_called_once()
        mock_push.assert_called_once_with(Path("/repo"), "feat/42-x", force=True)

    @patch(f"{_D}.git_branch.reset_branch")
    @patch(
        "wade.git.worktree.list_worktrees",
        return_value=[{"path": "/wt", "branch": "feat/42-x"}],
    )
    def test_active_worktree_leaves_branch_untouched(
        self, _wt: MagicMock, mock_reset: MagicMock
    ) -> None:
        # In-flight work must never be rewritten — proceed with the PR-base edit only.
        ok = reroot_scaffold_branch_for_retarget(
            Path("/repo"), "feat/42-x", "develop", "main", "42"
        )
        assert ok is True
        mock_reset.assert_not_called()

    @patch(f"{_D}.git_branch.reset_branch")
    @patch(f"{_D}.git_branch.commits_ahead", return_value=3)
    @patch(f"{_D}.git_branch.resolve_start_point", return_value="develop")
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    def test_real_work_leaves_branch_untouched(
        self,
        _wt: MagicMock,
        _resolve: MagicMock,
        _ahead: MagicMock,
        mock_reset: MagicMock,
    ) -> None:
        # More than the scaffold commit beyond the old base → real work, leave as-is.
        ok = reroot_scaffold_branch_for_retarget(
            Path("/repo"), "feat/42-x", "develop", "main", "42"
        )
        assert ok is True
        mock_reset.assert_not_called()

    @patch(f"{_D}.git_branch.reset_branch")
    @patch(f"{_D}.git_repo.has_remote", return_value=False)
    @patch(f"{_D}.git_branch.commits_ahead", return_value=1)
    @patch(f"{_D}.git_branch.resolve_start_point")
    @patch("wade.git.worktree.list_worktrees", return_value=[])
    @patch(f"{_D}.console")
    def test_unresolvable_new_base_aborts(
        self,
        _console: MagicMock,
        _wt: MagicMock,
        mock_resolve: MagicMock,
        _ahead: MagicMock,
        _has_remote: MagicMock,
        mock_reset: MagicMock,
    ) -> None:
        # Old base resolves; the new base resolves nowhere and there is no remote to
        # fetch from → abort rather than retarget onto a branch we could not rebuild.
        mock_resolve.side_effect = ["develop", None]

        ok = reroot_scaffold_branch_for_retarget(
            Path("/repo"), "feat/42-x", "develop", "main", "42"
        )

        assert ok is False
        mock_reset.assert_not_called()


class TestBootstrapRetargetReroots:
    @patch(f"{_D}.reroot_scaffold_branch_for_retarget", return_value=True)
    @patch(f"{_D}.git_pr.update_pr_base", return_value=True)
    @patch(f"{_D}.git_branch.make_branch_name", return_value="feat/42-x")
    def test_reroots_before_update_pr_base(
        self,
        _mk: MagicMock,
        mock_update: MagicMock,
        mock_reroot: MagicMock,
    ) -> None:
        with patch(f"{_D}.git_pr.get_pr_for_branch", return_value=_open_pr("develop")):
            result = bootstrap_draft_pr(
                "42", "Title", "plan body", _cfg(), Path("/repo"), base_branch="main"
            )

        assert result == {"number": 5, "url": "http://x/5"}
        mock_reroot.assert_called_once_with(Path("/repo"), "feat/42-x", "develop", "main", "42")
        mock_update.assert_called_once()

    @patch(f"{_D}.reroot_scaffold_branch_for_retarget", return_value=False)
    @patch(f"{_D}.git_pr.update_pr_base")
    @patch(f"{_D}.git_branch.make_branch_name", return_value="feat/42-x")
    def test_aborts_when_reroot_fails(
        self,
        _mk: MagicMock,
        mock_update: MagicMock,
        _reroot: MagicMock,
    ) -> None:
        with patch(f"{_D}.git_pr.get_pr_for_branch", return_value=_open_pr("develop")):
            result = bootstrap_draft_pr(
                "42", "Title", "plan body", _cfg(), Path("/repo"), base_branch="main"
            )

        assert result is None  # a failed re-root aborts before the PR base is edited
        mock_update.assert_not_called()
