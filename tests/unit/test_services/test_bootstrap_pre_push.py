"""Tests that bootstrap installs the pre-push backstop for the right sessions (#349)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wade.models.config import DoneConfig, ProjectConfig, ProjectSettings
from wade.services.implementation_service import bootstrap_worktree


def _run(tmp_path: Path, *, plan_mode: bool, backstop: bool):
    wt = tmp_path / "wt"
    wt.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    config = ProjectConfig(project=ProjectSettings(), done=DoneConfig(pre_push_backstop=backstop))
    with patch(
        "wade.skills.installer.install_pre_push_backstop", return_value=True
    ) as mock_install:
        bootstrap_worktree(wt, config, repo, plan_mode=plan_mode)
    return mock_install


class TestBootstrapPrePushBackstop:
    def test_installed_for_implementation_session(self, tmp_path: Path) -> None:
        mock_install = _run(tmp_path, plan_mode=False, backstop=True)
        # Assert the target too: a refactor must not pass the repo root instead
        # of the worktree path.
        mock_install.assert_called_once_with(tmp_path / "wt")

    def test_not_installed_in_plan_mode(self, tmp_path: Path) -> None:
        mock_install = _run(tmp_path, plan_mode=True, backstop=True)
        mock_install.assert_not_called()

    def test_not_installed_when_config_disabled(self, tmp_path: Path) -> None:
        mock_install = _run(tmp_path, plan_mode=False, backstop=False)
        mock_install.assert_not_called()

    def test_install_failure_does_not_crash_bootstrap(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        # Enable the backstop explicitly so this exercises the failure path
        # regardless of the DoneConfig default.
        config = ProjectConfig(project=ProjectSettings(), done=DoneConfig(pre_push_backstop=True))
        with patch(
            "wade.skills.installer.install_pre_push_backstop",
            side_effect=RuntimeError("boom"),
        ) as mock_install:
            # A backstop install error must never crash bootstrap.
            bootstrap_worktree(wt, config, repo, plan_mode=False)
        mock_install.assert_called_once_with(wt)
