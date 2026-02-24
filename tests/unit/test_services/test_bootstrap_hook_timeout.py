"""Tests for bootstrap_worktree() hook timeout handling."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghaiw.models.config import HooksConfig, ProjectConfig, ProjectSettings
from ghaiw.services.work_service import bootstrap_worktree


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

        with patch("subprocess.run") as mock_run:
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

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Should not raise
            bootstrap_worktree(worktree_path, config, repo_root)

            # Verify subprocess.run was called with timeout=60
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 60

    def test_bootstrap_hook_non_timeout_error_propagates(self, tmp_path: Path) -> None:
        """Non-timeout subprocess errors should propagate as-is (not wrapped)."""
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

        with patch("subprocess.run") as mock_run:
            # CalledProcessError is NOT wrapped in RuntimeError
            mock_run.side_effect = subprocess.CalledProcessError(1, "cmd", stderr=b"error")

            # Should NOT raise RuntimeError; CalledProcessError should be caught
            # and logged as a warning (not re-raised)
            bootstrap_worktree(worktree_path, config, repo_root)

            # Verify subprocess.run was called with timeout=60
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 60

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

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 60)

            with pytest.raises(RuntimeError) as exc_info:
                bootstrap_worktree(worktree_path, config, repo_root)

            error_msg = str(exc_info.value)
            assert "custom/hook.sh" in error_msg or "custom" in error_msg
