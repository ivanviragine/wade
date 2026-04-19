"""Tests for bootstrap_worktree() hook timeout handling."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.models.config import HooksConfig, ProjectConfig, ProjectSettings
from wade.services.implementation_service import bootstrap_worktree


class TestBootstrapHookTimeout:
    """Tests for post_worktree_create hook timeout behavior."""

    def test_bootstrap_hook_timeout_raises_runtime_error(self, tmp_path: Path) -> None:
        """Timeout should raise RuntimeError with 60-second message."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        hook_path = repo_root / "scripts" / "setup.sh"
        hook_path.parent.mkdir(parents=True)
        hook_path.write_text("#!/bin/bash\necho test\n")
        hook_path.chmod(0o755)

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(post_worktree_create="scripts/setup.sh"),
        )

        with patch("wade.services.implementation_service.bootstrap.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 60)

            with pytest.raises(RuntimeError) as exc_info:
                bootstrap_worktree(worktree_path, config, repo_root)

            assert "Bootstrap hook timed out after 60 seconds" in str(exc_info.value)
            assert str(hook_path) in str(exc_info.value)

    def test_bootstrap_hook_success_unaffected(self, tmp_path: Path) -> None:
        """Successful hook execution should not raise any exception."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        hook_path = repo_root / "scripts" / "setup.sh"
        hook_path.parent.mkdir(parents=True)
        hook_path.write_text("#!/bin/bash\necho test\n")
        hook_path.chmod(0o755)

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(post_worktree_create="scripts/setup.sh"),
        )

        with patch("wade.services.implementation_service.bootstrap.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Should not raise
            bootstrap_worktree(worktree_path, config, repo_root)

            # Verify the hook was called with timeout=60.
            # bootstrap_worktree may make additional subprocess calls (e.g. git
            # commands for pointer artifact suppression), so we locate the hook
            # call by its timeout kwarg rather than asserting called_once.
            hook_calls = [c for c in mock_run.call_args_list if c.kwargs.get("timeout") == 60]
            assert len(hook_calls) == 1
            assert hook_calls[0].kwargs["timeout"] == 60

    def test_bootstrap_hook_called_process_error_is_caught(self, tmp_path: Path) -> None:
        """CalledProcessError from the hook is caught and logged as a warning, not re-raised."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        hook_path = repo_root / "scripts" / "setup.sh"
        hook_path.parent.mkdir(parents=True)
        hook_path.write_text("#!/bin/bash\nexit 1\n")
        hook_path.chmod(0o755)

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(post_worktree_create="scripts/setup.sh"),
        )

        def _hook_only_error(*args: object, **kwargs: object) -> MagicMock:
            if kwargs.get("timeout") == 60:
                raise subprocess.CalledProcessError(1, "cmd", stderr=b"error")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("wade.services.implementation_service.bootstrap.subprocess.run") as mock_run:
            mock_run.side_effect = _hook_only_error

            # Should not raise — CalledProcessError is suppressed and logged.
            bootstrap_worktree(worktree_path, config, repo_root)

            hook_calls = [c for c in mock_run.call_args_list if c.kwargs.get("timeout") == 60]
            assert len(hook_calls) == 1

    def test_bootstrap_hook_timeout_includes_hook_path(self, tmp_path: Path) -> None:
        """RuntimeError message should include the hook path for debugging."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        hook_path = repo_root / "custom" / "hook.sh"
        hook_path.parent.mkdir(parents=True)
        hook_path.write_text("#!/bin/bash\n")
        hook_path.chmod(0o755)

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(post_worktree_create="custom/hook.sh"),
        )

        with patch("wade.services.implementation_service.bootstrap.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 60)

            with pytest.raises(RuntimeError) as exc_info:
                bootstrap_worktree(worktree_path, config, repo_root)

            error_msg = str(exc_info.value)
            assert "custom/hook.sh" in error_msg
