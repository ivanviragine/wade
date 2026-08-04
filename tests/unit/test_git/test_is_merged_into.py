"""Unit tests for git_branch.is_merged_into (#357).

Routes the ancestry check through the git layer (``git merge-base
--is-ancestor``) with a tri-state result so callers can distinguish "not
merged" (exit 1) from an unresolvable ref (any other exit).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.branch import is_merged_into


def _proc(returncode: int) -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    return m


@patch("wade.git.branch._run_git")
def test_true_when_ancestor(mock_run: MagicMock) -> None:
    mock_run.return_value = _proc(0)
    assert is_merged_into(Path("/repo"), "feat/1-x", "origin/main") is True


@patch("wade.git.branch._run_git")
def test_false_when_not_ancestor(mock_run: MagicMock) -> None:
    mock_run.return_value = _proc(1)
    assert is_merged_into(Path("/repo"), "feat/1-x", "origin/main") is False


@patch("wade.git.branch._run_git")
def test_none_when_ref_unresolvable(mock_run: MagicMock) -> None:
    # exit 128 = a bad/missing ref → indeterminate, not "definitely not merged".
    mock_run.return_value = _proc(128)
    assert is_merged_into(Path("/repo"), "feat/1-x", "origin/main") is None
