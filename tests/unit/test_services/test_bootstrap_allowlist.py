"""Tests for bootstrap_worktree() expanded allowlist propagation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from wade.models.config import HooksConfig, PermissionsConfig, ProjectConfig, ProjectSettings
from wade.services.implementation_service import bootstrap_worktree

WADE_ALLOW_PATTERN = "Bash(wade *)"
CURSOR_WADE_ALLOW_PATTERN = "Shell(wade *)"


@pytest.fixture(autouse=True)
def _isolate_cursor_global_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Redirect crossby's global Cursor config to a tmp path so tests don't see ~/.cursor."""
    fake_global = tmp_path_factory.mktemp("cursor-home") / "cli-config.json"
    monkeypatch.setattr("crossby.config.cursor_allowlist._GLOBAL_CONFIG_PATH", fake_global)


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
            patch("crossby.config.cursor_allowlist._GLOBAL_CONFIG_PATH", global_config),
            patch(
                "crossby.config.cursor_allowlist.is_allowlist_configured",
                side_effect=lambda root=None, patterns=None: root is None,
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


class TestBootstrapPlanMode:
    """Tests that bootstrap_worktree(plan_mode=True) installs guard hooks."""

    def test_plan_mode_installs_guard_hooks(self, tmp_path: Path) -> None:
        """plan_mode=True installs guard script and configs for all tools."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root, plan_mode=True)

        # Guard script should be copied to all tool hook dirs
        for tool_dir in [".claude/hooks", ".cursor/hooks", ".copilot/hooks", ".gemini/hooks"]:
            guard = worktree_path / tool_dir / "plan_write_guard.py"
            assert guard.is_file(), f"Guard script missing in {tool_dir}"

        # Hook configs should exist
        claude_settings = worktree_path / ".claude" / "settings.json"
        assert claude_settings.is_file()
        data = json.loads(claude_settings.read_text(encoding="utf-8"))
        assert "hooks" in data
        assert "PreToolUse" in data["hooks"]

        # Verify hook format is correct: array of objects with type and command
        pre_tool_use_hooks = data["hooks"]["PreToolUse"]
        assert isinstance(pre_tool_use_hooks, list)
        assert len(pre_tool_use_hooks) > 0
        for hook_entry in pre_tool_use_hooks:
            assert isinstance(hook_entry, dict)
            assert "hooks" in hook_entry
            assert isinstance(hook_entry["hooks"], list)
            for hook in hook_entry["hooks"]:
                assert isinstance(hook, dict), "Hook must be an object, not a string"
                assert "type" in hook
                assert hook["type"] == "command"
                assert "command" in hook
                assert "plan_write_guard.py" in hook["command"]

        cursor_hooks = worktree_path / ".cursor" / "hooks.json"
        assert cursor_hooks.is_file()

        copilot_hooks = worktree_path / ".github" / "hooks" / "hooks.json"
        assert copilot_hooks.is_file()

        gemini_settings = worktree_path / ".gemini" / "settings.json"
        assert gemini_settings.is_file()

    def test_plan_mode_false_installs_worktree_guard_hooks(self, tmp_path: Path) -> None:
        """plan_mode=False (default) installs worktree guard hooks, not plan guard hooks."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # Plan guard script should NOT be created
        plan_guard = worktree_path / ".claude" / "hooks" / "plan_write_guard.py"
        assert not plan_guard.exists()

        # Worktree guard script SHOULD be created
        worktree_guard = worktree_path / ".claude" / "hooks" / "worktree_guard.py"
        assert worktree_guard.is_file()

        # Cursor hooks.json should exist (worktree guard)
        cursor_hooks = worktree_path / ".cursor" / "hooks.json"
        assert cursor_hooks.is_file()

        # Copilot hooks.json should exist (worktree guard)
        copilot_hooks = worktree_path / ".github" / "hooks" / "hooks.json"
        assert copilot_hooks.is_file()

        # Gemini settings.json should exist (worktree guard)
        gemini_settings = worktree_path / ".gemini" / "settings.json"
        assert gemini_settings.is_file()


class TestBootstrapPointerInjection:
    """Tests that bootstrap_worktree() injects the AGENTS.md pointer into worktrees."""

    def test_pointer_written_to_worktree(self, tmp_path: Path) -> None:
        """bootstrap_worktree writes the AGENTS.md pointer to the worktree."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # AGENTS.md should be created in the worktree with the pointer block
        agents_md = worktree_path / "AGENTS.md"
        assert agents_md.is_file(), "AGENTS.md pointer should be written to worktree"
        content = agents_md.read_text()
        assert "<!-- wade:pointer:start -->" in content

    def test_pointer_not_written_to_main(self, tmp_path: Path) -> None:
        """bootstrap_worktree does not touch repo_root AGENTS.md."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        # repo_root should not have AGENTS.md
        assert not (repo_root / "AGENTS.md").is_file()

    def test_pointer_follows_existing_agents_content(self, tmp_path: Path) -> None:
        """bootstrap_worktree appends pointer after existing AGENTS.md content."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        # Pre-populate AGENTS.md with project content
        (worktree_path / "AGENTS.md").write_text("# Project Guide\n\nExisting content.\n")

        config = ProjectConfig(
            project=ProjectSettings(),
            hooks=HooksConfig(),
            permissions=PermissionsConfig(),
        )

        with patch("subprocess.run"):
            bootstrap_worktree(worktree_path, config, repo_root)

        content = (worktree_path / "AGENTS.md").read_text()
        assert "# Project Guide" in content
        assert "<!-- wade:pointer:start -->" in content
