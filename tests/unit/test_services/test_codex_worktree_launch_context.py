"""Codex worktree launch-context integration proof (issue #423).

These tests use a **real** linked git worktree (``git init`` → commit →
``git worktree add``) and the **real** crossby Codex adapter to prove that
threading ``working_dir`` grants the worktree's out-of-root git-metadata dirs as
sandbox writable roots, and that ``network_access`` is always pinned explicitly.
They also prove the filesystem fix is independent of the network policy and that
non-Codex tools are unaffected.

Why a real worktree: crossby's ``outside_root_git_metadata_dirs`` shells out to
``git rev-parse`` to discover the private/common git dirs, so a mock worktree
would never exercise the resolution this issue depends on.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from crossby.ai_tools.claude import ClaudeAdapter
from crossby.ai_tools.codex import CodexAdapter
from crossby.utils.git_worktree import outside_root_git_metadata_dirs


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def linked_worktree(tmp_git_repo: Path, tmp_path: Path) -> Path:
    """Create a real linked worktree off ``tmp_git_repo`` and return its path.

    The private git dir lands at ``<repo>/.git/worktrees/<name>`` and the common
    git dir at ``<repo>/.git`` — both outside the worktree tree, which is exactly
    the sandbox-blocked layout issue #423 addresses.
    """
    wt = tmp_path / "wt-423"
    _git(tmp_git_repo, "worktree", "add", "-b", "feat/423", str(wt))
    return wt


def _expected_metadata_dirs(worktree: Path) -> list[str]:
    dirs = outside_root_git_metadata_dirs(worktree)
    assert dirs, "fixture must be a linked worktree with out-of-root git metadata"
    return [str(d) for d in dirs]


def _add_dir_values(cmd: list[str]) -> list[str]:
    """Return every value following an ``--add-dir`` flag in *cmd*."""
    return [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--add-dir" and i + 1 < len(cmd)]


class TestCodexWorktreeGrant:
    """The Codex builders grant the out-of-root metadata dirs from working_dir."""

    def test_launch_grants_metadata_and_pins_network_off(self, linked_worktree: Path) -> None:
        cmd = CodexAdapter().build_launch_command(
            model="gpt-5",
            working_dir=linked_worktree,
            network_access=False,
        )
        assert "--sandbox" in cmd and "workspace-write" in cmd
        add_dirs = _add_dir_values(cmd)
        for meta in _expected_metadata_dirs(linked_worktree):
            assert meta in add_dirs, f"{meta} must be granted as a sandbox writable root"
        # Network is pinned explicitly to false — never left to ambient config.
        assert "sandbox_workspace_write.network_access=false" in cmd

    def test_launch_network_on_pins_true(self, linked_worktree: Path) -> None:
        cmd = CodexAdapter().build_launch_command(
            working_dir=linked_worktree,
            network_access=True,
        )
        assert "sandbox_workspace_write.network_access=true" in cmd
        # The metadata grant is independent of the network pin.
        add_dirs = _add_dir_values(cmd)
        for meta in _expected_metadata_dirs(linked_worktree):
            assert meta in add_dirs

    def test_resume_grants_metadata_and_stays_approval_neutral(self, linked_worktree: Path) -> None:
        cmd = CodexAdapter().build_resume_command(
            "sess-423",
            working_dir=linked_worktree,
            network_access=False,
        )
        assert cmd is not None
        add_dirs = _add_dir_values(cmd)
        for meta in _expected_metadata_dirs(linked_worktree):
            assert meta in add_dirs
        assert "sandbox_workspace_write.network_access=false" in cmd
        # Resume must not inject an approval flag (approval-neutral).
        assert "-a" not in cmd

    def test_resume_network_on_pins_true(self, linked_worktree: Path) -> None:
        cmd = CodexAdapter().build_resume_command(
            "sess-423",
            working_dir=linked_worktree,
            network_access=True,
        )
        assert cmd is not None
        assert "sandbox_workspace_write.network_access=true" in cmd


class TestNonCodexUnaffected:
    """Non-Codex tools ignore working_dir / network_access (capability-gated)."""

    def test_claude_ignores_worktree_context(self, linked_worktree: Path) -> None:
        cmd = ClaudeAdapter().build_launch_command(
            model="claude-sonnet-4-6",
            working_dir=linked_worktree,
            network_access=True,
        )
        # No Codex sandbox config, and none of the metadata dirs granted.
        assert not any("sandbox_workspace_write" in tok for tok in cmd)
        add_dirs = _add_dir_values(cmd)
        for meta in _expected_metadata_dirs(linked_worktree):
            assert meta not in add_dirs


class TestNetworkOffLocalGitProof:
    """The reported failure-class git ops are local — they need no network.

    Proves the filesystem fix (working_dir grant) is independent of the network
    policy: with network off, a real worktree can stage, commit, stash, and
    update refs. These are the exact operations that fail under a Codex sandbox
    lacking the metadata-dir grant (``index.lock`` / ``could not write index``).
    """

    def test_stage_commit_stash_update_ref_succeed(self, linked_worktree: Path) -> None:
        (linked_worktree / "a.txt").write_text("one\n")
        # Stage + commit (writes index + refs in the private git dir).
        _git(linked_worktree, "add", "a.txt")
        _git(linked_worktree, "commit", "-m", "add a.txt")
        # Stash push + pop (writes the shared common-dir index — the
        # "could not write index" failure class).
        (linked_worktree / "a.txt").write_text("two\n")
        _git(linked_worktree, "stash", "push", "-m", "wip")
        _git(linked_worktree, "stash", "pop")
        # Direct ref update.
        head = _git(linked_worktree, "rev-parse", "HEAD").stdout.strip()
        _git(linked_worktree, "update-ref", "refs/heads/probe-423", head)
        # All succeeded (subprocess check=True would have raised otherwise).
        assert _git(linked_worktree, "rev-parse", "refs/heads/probe-423").stdout.strip() == head
