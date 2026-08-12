"""Tests that bootstrap installs the managed git hooks for the right sessions.

Covers the #349 pre-push backstop and the #352 pre-commit / commit-msg quality
gates, all now installed through the single batch call
``install_worktree_git_hooks`` so prior user hooks are captured before wade sets
``core.hooksPath``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wade.models.config import (
    CommitMsgConfig,
    DoneConfig,
    HooksConfig,
    PreCommitConfig,
    ProjectConfig,
    ProjectSettings,
)
from wade.services.implementation_service import bootstrap as bootstrap_mod
from wade.services.implementation_service import bootstrap_worktree


def _run(tmp_path: Path, *, plan_mode: bool, config: ProjectConfig):
    wt = tmp_path / "wt"
    wt.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    with patch("wade.git.hooks.install_worktree_git_hooks", return_value=True) as mock_install:
        bootstrap_worktree(wt, config, repo, plan_mode=plan_mode)
    return mock_install


def _installed_hooks(mock_install) -> set[str]:
    """Return the set of hook names passed to the (single) batch install call."""
    assert mock_install.call_count <= 1
    if mock_install.call_count == 0:
        return set()
    _worktree, hooks = mock_install.call_args.args
    return set(hooks)


class TestBootstrapManagedGitHooks:
    def test_pre_push_installed_for_implementation_session(self, tmp_path: Path) -> None:
        config = ProjectConfig(project=ProjectSettings(), done=DoneConfig(pre_push_backstop=True))
        mock_install = _run(tmp_path, plan_mode=False, config=config)
        # Target the worktree, not the repo root.
        assert mock_install.call_args.args[0] == tmp_path / "wt"
        assert "pre-push" in _installed_hooks(mock_install)

    def test_no_hooks_in_plan_mode(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project=ProjectSettings(),
            done=DoneConfig(pre_push_backstop=True),
            hooks=HooksConfig(commit_msg=CommitMsgConfig(conventional=True)),
        )
        mock_install = _run(tmp_path, plan_mode=True, config=config)
        mock_install.assert_not_called()

    def test_pre_push_absent_when_backstop_disabled(self, tmp_path: Path) -> None:
        config = ProjectConfig(project=ProjectSettings(), done=DoneConfig(pre_push_backstop=False))
        mock_install = _run(tmp_path, plan_mode=False, config=config)
        # No other hook is configured → nothing installed at all.
        mock_install.assert_not_called()

    def test_pre_commit_and_commit_msg_installed_when_configured(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project=ProjectSettings(),
            done=DoneConfig(pre_push_backstop=True),
            hooks=HooksConfig(
                pre_commit=PreCommitConfig(lint="./scripts/check.sh --lint"),
                commit_msg=CommitMsgConfig(conventional=True),
            ),
        )
        mock_install = _run(tmp_path, plan_mode=False, config=config)
        assert _installed_hooks(mock_install) == {"pre-push", "pre-commit", "commit-msg"}

    def test_off_by_default(self, tmp_path: Path) -> None:
        # Default HooksConfig installs no quality gates; with the pre-push
        # backstop also off, nothing is installed.
        config = ProjectConfig(project=ProjectSettings(), done=DoneConfig(pre_push_backstop=False))
        mock_install = _run(tmp_path, plan_mode=False, config=config)
        assert _installed_hooks(mock_install) == set()

    def test_install_failure_does_not_crash_bootstrap(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        config = ProjectConfig(project=ProjectSettings(), done=DoneConfig(pre_push_backstop=True))
        with patch(
            "wade.git.hooks.install_worktree_git_hooks",
            side_effect=RuntimeError("boom"),
        ) as mock_install:
            # A hook-install error must never crash bootstrap.
            bootstrap_worktree(wt, config, repo, plan_mode=False)
        mock_install.assert_called_once()

    def test_missing_gate_template_degrades_without_crashing(self, tmp_path: Path) -> None:
        # build_pre_commit/commit_msg_hook_script load a template that can be
        # absent (packaging gap); a FileNotFoundError there must warn-and-skip
        # that gate, never abort bootstrap — mirroring the pre-push branch. The
        # pre-push backstop still installs, proving reconcile ran regardless.
        config = ProjectConfig(
            project=ProjectSettings(),
            done=DoneConfig(pre_push_backstop=True),
            hooks=HooksConfig(
                pre_commit=PreCommitConfig(lint="./scripts/check.sh --lint"),
                commit_msg=CommitMsgConfig(conventional=True),
            ),
        )
        with (
            patch(
                "wade.git.hooks.build_pre_commit_hook_script",
                side_effect=FileNotFoundError("no pre-commit template"),
            ),
            patch(
                "wade.git.hooks.build_commit_msg_hook_script",
                side_effect=FileNotFoundError("no commit-msg template"),
            ),
            patch.object(bootstrap_mod.logger, "warning") as warning,
        ):
            mock_install = _run(tmp_path, plan_mode=False, config=config)
        # Both gates degraded; only the healthy pre-push hook was reconciled.
        assert _installed_hooks(mock_install) == {"pre-push"}
        warned = {call.args[0] for call in warning.call_args_list}
        assert "implementation.pre_commit_template_missing" in warned
        assert "implementation.commit_msg_template_missing" in warned
