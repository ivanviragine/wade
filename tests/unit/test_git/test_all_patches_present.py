"""Unit tests for git_branch.all_patches_present (#357).

``git cherry <base> <branch>`` marks an already-applied commit with ``-`` and a
genuinely-absent one with ``+``. all_patches_present returns True only when the
command succeeds and no line starts with ``+`` — recognizing a squash/rebase
merge whose tip is not an ancestor of base. Fails closed otherwise.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.branch import all_patches_present


def _proc(returncode: int, stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    return m


@patch("wade.git.branch._run_git")
def test_true_when_all_patches_applied(mock_run: MagicMock) -> None:
    # Every commit already present on base → all "-" lines.
    mock_run.return_value = _proc(0, stdout="- abc123\n- def456\n")
    assert all_patches_present(Path("/repo"), "origin/main", "feat/1-x") is True


@patch("wade.git.branch._run_git")
def test_false_when_a_patch_is_absent(mock_run: MagicMock) -> None:
    # A "+" line means a genuinely-absent commit → not fully applied.
    mock_run.return_value = _proc(0, stdout="- abc123\n+ 999aaa\n")
    assert all_patches_present(Path("/repo"), "origin/main", "feat/1-x") is False


@patch("wade.git.branch._run_git")
def test_false_on_empty_output(mock_run: MagicMock) -> None:
    # No commits to compare → never read as "safe".
    mock_run.return_value = _proc(0, stdout="")
    assert all_patches_present(Path("/repo"), "origin/main", "feat/1-x") is False


@patch("wade.git.branch._run_git")
def test_false_on_git_error(mock_run: MagicMock) -> None:
    # Fail closed on any git error.
    mock_run.return_value = _proc(128, stdout="")
    assert all_patches_present(Path("/repo"), "origin/main", "feat/1-x") is False
