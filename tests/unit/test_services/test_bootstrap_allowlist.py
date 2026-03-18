"""Tests for bootstrap_worktree() expanded allowlist propagation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from wade.config.claude_allowlist import WADE_ALLOW_PATTERN
from wade.config.cursor_allowlist import WADE_ALLOW_PATTERN as CURSOR_WADE_ALLOW_PATTERN
from wade.models.config import HooksConfig, PermissionsConfig, ProjectConfig, ProjectSettings
from wade.services.implementation_service import bootstrap_worktree


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

    def test_always_propagates_even_without_repo_root_settings(self, tmp_path: Path) -> None:
        """bootstrap_worktree always writes allowlist regardless of repo root state."""
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

        # settings.json should always be created, even without repo root config
        wt_settings = worktree_path / ".claude" / "settings.json"
        assert wt_settings.is_file()
        data = json.loads(wt_settings.read_text(encoding="utf-8"))
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]


class TestBootstrapCursorAllowlistPropagation:
    """Tests that bootstrap_worktree() propagates Cursor allowlist to worktree."""

    def test_propagates_cursor_patterns_from_global(self, tmp_path: Path) -> None:
        """bootstrap_worktree propagates Cursor allowlist when global config exists."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(allowed_commands=["wade *", "./scripts/check.sh *"]),
        )

        # Set up global Cursor config with wade pattern
        global_config = Path.home() / ".cursor" / "cli-config.json"
        with (
            patch("wade.config.cursor_allowlist._GLOBAL_CONFIG_PATH", global_config),
            patch(
                "wade.config.cursor_allowlist.is_allowlist_configured",
                side_effect=lambda root=None: root is None,
            ),
            patch("subprocess.run"),
        ):
            bootstrap_worktree(worktree_path, config, repo_root)

        # Check the worktree .cursor/cli.json has expanded patterns
        wt_cursor_config = worktree_path / ".cursor" / "cli.json"
        assert wt_cursor_config.is_file()
        data = json.loads(wt_cursor_config.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert CURSOR_WADE_ALLOW_PATTERN in allow
        assert "Shell(./scripts/check.sh *)" in allow

    def test_propagates_cursor_patterns_from_project(self, tmp_path: Path) -> None:
        """bootstrap_worktree propagates Cursor allowlist when project config exists."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Set up per-project Cursor config at repo root
        repo_cursor_dir = repo_root / ".cursor"
        repo_cursor_dir.mkdir()
        (repo_cursor_dir / "cli.json").write_text(
            json.dumps({"permissions": {"allow": [CURSOR_WADE_ALLOW_PATTERN]}}),
            encoding="utf-8",
        )

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(allowed_commands=["wade *", "./scripts/fmt.sh *"]),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # Check the worktree .cursor/cli.json has expanded patterns
        wt_cursor_config = worktree_path / ".cursor" / "cli.json"
        assert wt_cursor_config.is_file()
        data = json.loads(wt_cursor_config.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert CURSOR_WADE_ALLOW_PATTERN in allow
        assert "Shell(./scripts/fmt.sh *)" in allow

    def test_skips_cursor_when_not_configured(self, tmp_path: Path) -> None:
        """bootstrap_worktree skips Cursor allowlist if not configured anywhere."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(allowed_commands=["wade *"]),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # No .cursor/cli.json should be created
        wt_cursor_config = worktree_path / ".cursor" / "cli.json"
        assert not wt_cursor_config.is_file()
