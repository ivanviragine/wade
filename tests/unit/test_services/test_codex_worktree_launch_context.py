"""Codex worktree launch-context integration proof (issues #423, #478).

These tests use a **real** linked git worktree (``git init`` → commit →
``git worktree add``) and the **real** crossby adapters to prove that threading
``working_dir`` grants the worktree's out-of-root git-metadata dirs as sandbox
writable roots, that ``network_access`` is always pinned explicitly, and that the
``sandbox`` profile maps to the right per-tool flag. They also prove the
filesystem fix is independent of the network policy and that tools without a
sandbox toggle are unaffected.

Why a real worktree: crossby's ``outside_root_git_metadata_dirs`` shells out to
``git rev-parse`` to discover the private/common git dirs, so a mock worktree
would never exercise the resolution this issue depends on.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar

import pytest
from crossby.ai_tools.claude import ClaudeAdapter
from crossby.ai_tools.codex import CodexAdapter
from crossby.ai_tools.cursor import CursorAdapter
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


def _flag_value(cmd: list[str], flag: str) -> str | None:
    """Return the single value following *flag*, asserting it appears at most once."""
    hits = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == flag and i + 1 < len(cmd)]
    assert len(hits) <= 1, f"{flag} must be emitted at most once, got {hits}"
    return hits[0] if hits else None


def _autonomy_tokens(cmd: list[str]) -> list[str]:
    """Codex's approval-policy argv: the ``-a``/``--ask-for-approval`` pair plus bypass flags."""
    out: list[str] = []
    for i, tok in enumerate(cmd):
        if tok in ("-a", "--ask-for-approval"):
            out.append(tok)
            if i + 1 < len(cmd):
                out.append(cmd[i + 1])
        elif tok.startswith("--dangerously"):
            out.append(tok)
    return out


class TestSandboxProfileMapping:
    """The resolved profile maps to the right flag on every tool (#478)."""

    def test_codex_unrestricted_emits_danger_full_access(self, linked_worktree: Path) -> None:
        cmd = CodexAdapter().build_launch_command(
            model="gpt-5",
            working_dir=linked_worktree,
            network_access=True,
            sandbox=False,
        )
        assert _flag_value(cmd, "--sandbox") == "danger-full-access"
        # With no sandbox boundary there is nothing to widen or pin: the
        # --add-dir metadata grants and the network pin are both meaningless.
        assert _add_dir_values(cmd) == []
        assert not any("sandbox_workspace_write" in tok for tok in cmd)

    def test_codex_sandboxed_emits_workspace_write_with_network_on(
        self, linked_worktree: Path
    ) -> None:
        cmd = CodexAdapter().build_launch_command(
            model="gpt-5",
            working_dir=linked_worktree,
            network_access=True,
            sandbox=True,
        )
        assert _flag_value(cmd, "--sandbox") == "workspace-write"
        # Under confinement the grants and the network pin both matter again —
        # wade's lifecycle still needs fetch/push from inside the sandbox.
        add_dirs = _add_dir_values(cmd)
        for meta in _expected_metadata_dirs(linked_worktree):
            assert meta in add_dirs
        assert "sandbox_workspace_write.network_access=true" in cmd

    def test_codex_resume_carries_the_profile(self, linked_worktree: Path) -> None:
        cmd = CodexAdapter().build_resume_command(
            "sess-478",
            working_dir=linked_worktree,
            network_access=True,
            sandbox=False,
        )
        assert cmd is not None
        assert _flag_value(cmd, "--sandbox") == "danger-full-access"

    def test_cursor_maps_to_enabled_and_disabled(self) -> None:
        disabled = CursorAdapter().build_launch_command(sandbox=False)
        enabled = CursorAdapter().build_launch_command(sandbox=True)
        assert _flag_value(disabled, "--sandbox") == "disabled"
        assert _flag_value(enabled, "--sandbox") == "enabled"

    @pytest.mark.parametrize("profile", [True, False])
    def test_tool_without_the_capability_gets_no_invented_flag(self, profile: bool) -> None:
        # Claude has no sandbox concept; it must never receive a --sandbox flag
        # in either direction, invented or otherwise.
        cmd = ClaudeAdapter().build_launch_command(model="claude-sonnet-5", sandbox=profile)
        assert "--sandbox" not in cmd


class TestSandboxIsOrthogonalToAutonomy:
    """``unrestricted`` is not ``yolo`` — the two axes never touch (#478).

    ``--sandbox danger-full-access`` is a distinct flag from
    ``--dangerously-bypass-approvals-and-sandbox``, so disabling the sandbox must
    not move the approval tier. Each tier is built under both profiles and the
    autonomy argv compared directly.
    """

    _TIERS: ClassVar[list[dict[str, bool]]] = [
        {},
        {"yolo": True},
        {"accept_edits": True},
        {"auto": True},
    ]
    _TIER_IDS: ClassVar[list[str]] = ["default", "yolo", "accept-edits", "auto"]

    @staticmethod
    def _without_sandbox(cmd: list[str]) -> list[str]:
        out: list[str] = []
        skip = False
        for tok in cmd:
            if skip:
                skip = False
                continue
            if tok == "--sandbox":
                skip = True
                continue
            out.append(tok)
        return out

    @pytest.mark.parametrize("autonomy", _TIERS, ids=_TIER_IDS)
    def test_codex_autonomy_argv_identical_across_profiles(
        self, autonomy: dict[str, bool], linked_worktree: Path
    ) -> None:
        sandboxed = CodexAdapter().build_launch_command(
            model="gpt-5",
            working_dir=linked_worktree,
            network_access=True,
            sandbox=True,
            **autonomy,
        )
        unrestricted = CodexAdapter().build_launch_command(
            model="gpt-5",
            working_dir=linked_worktree,
            network_access=True,
            sandbox=False,
            **autonomy,
        )
        # The approval tier is byte-identical: the profile moved the OS boundary,
        # not the autonomy ladder.
        assert _autonomy_tokens(sandboxed) == _autonomy_tokens(unrestricted)
        # And an unrestricted launch never acquires the approval-bypass flag.
        assert "--dangerously-bypass-approvals-and-sandbox" not in unrestricted

    @pytest.mark.parametrize("autonomy", _TIERS, ids=_TIER_IDS)
    def test_cursor_argv_differs_only_by_the_sandbox_value(self, autonomy: dict[str, bool]) -> None:
        # Cursor emits no sandbox-conditional grants, so the whole argv minus the
        # --sandbox pair must be byte-identical — the strictest form of the claim.
        sandboxed = CursorAdapter().build_launch_command(sandbox=True, **autonomy)
        unrestricted = CursorAdapter().build_launch_command(sandbox=False, **autonomy)
        assert self._without_sandbox(sandboxed) == self._without_sandbox(unrestricted)
