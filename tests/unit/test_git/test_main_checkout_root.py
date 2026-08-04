"""Regression tests for ``main_checkout_root`` (issue #357, defect C2).

Main-checkout operations (pulling main, branch deletion, worktree pruning,
``gh pr merge`` bookkeeping) must resolve the main checkout — never a linked
worktree root — even when invoked from inside a worktree.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.repo import main_checkout_root


class TestMainCheckoutRoot:
    @patch("wade.git.repo.get_main_worktree_path")
    def test_returns_main_when_inside_linked_worktree(self, mock_main: MagicMock) -> None:
        main_path = Path("/repo/main")
        mock_main.return_value = main_path

        # Called with a worktree root — must resolve to the main checkout.
        result = main_checkout_root(Path("/repo/.worktrees/feat-42"))
        assert result == main_path

    @patch("wade.git.repo.get_main_worktree_path")
    def test_falls_back_to_path_when_not_a_linked_worktree(self, mock_main: MagicMock) -> None:
        # Main checkout (or detection failed) → get_main_worktree_path returns None.
        mock_main.return_value = None

        repo_root = Path("/repo/main")
        result = main_checkout_root(repo_root)
        assert result == repo_root
