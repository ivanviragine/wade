"""Tests for bootstrap_worktree() expanded allowlist propagation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from wade.config.claude_allowlist import WADE_ALLOW_PATTERN
from wade.models.config import HooksConfig, PermissionsConfig, ProjectConfig, ProjectSettings
from wade.services.work_service import bootstrap_worktree


class TestBootstrapAllowlistPropagation:
    """Tests that bootstrap_worktree() propagates expanded allowlist patterns."""

    def test_propagates_extra_patterns_from_config(self, tmp_path: Path) -> None:
        """bootstrap_worktree propagates config.permissions.allowed_commands."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Set up existing allowlist at repo root
        repo_claude_dir = repo_root / ".claude"
        repo_claude_dir.mkdir()
        (repo_claude_dir / "settings.json").write_text(
            json.dumps({"permissions": {"allow": [WADE_ALLOW_PATTERN]}}),
            encoding="utf-8",
        )

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(
                allowed_commands=["wade *", "./scripts/check.sh *", "./scripts/fmt.sh *"]
            ),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # Check the worktree settings.json has expanded patterns
        wt_settings = worktree_path / ".claude" / "settings.json"
        assert wt_settings.is_file()
        data = json.loads(wt_settings.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert WADE_ALLOW_PATTERN in allow
        assert "Bash(./scripts/check.sh *)" in allow
        assert "Bash(./scripts/fmt.sh *)" in allow

    def test_skips_when_repo_root_not_configured(self, tmp_path: Path) -> None:
        """bootstrap_worktree skips allowlist if repo root has no allowlist."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(allowed_commands=["wade *", "./scripts/check.sh *"]),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # No settings.json should be created (allowlist wasn't configured at root)
        wt_settings = worktree_path / ".claude" / "settings.json"
        assert not wt_settings.is_file()
