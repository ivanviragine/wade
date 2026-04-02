"""Tests for Claude Code .claude/settings.json allowlist management."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from wade.config.claude_allowlist import (
    WADE_ALLOW_PATTERN,
    configure_allowlist,
    configure_plan_hooks,
    configure_worktree_hooks,
    is_allowlist_configured,
)


class TestConfigureAllowlist:
    """Tests for configure_allowlist()."""

    def test_creates_settings_from_scratch(self, tmp_path: Path) -> None:
        """Creates .claude/settings.json when neither dir nor file exist."""
        # Arrange
        project_root = tmp_path / "project"
        project_root.mkdir()
        settings_path = project_root / ".claude" / "settings.json"

        # Act
        configure_allowlist(project_root)

        # Assert
        assert settings_path.is_file()
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert data == {
            "permissions": {
                "allow": [WADE_ALLOW_PATTERN],
            },
        }

    def test_adds_to_existing_settings(self, tmp_path: Path) -> None:
        """Adds pattern to existing settings.json that has other permissions."""
        # Arrange
        project_root = tmp_path / "project"
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        existing = {
            "permissions": {
                "allow": ["Bash(git *)"],
                "deny": ["Bash(rm -rf /)"],
            },
            "theme": "dark",
        }
        settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

        # Act
        configure_allowlist(project_root)

        # Assert
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]
        assert "Bash(git *)" in data["permissions"]["allow"]
        assert data["permissions"]["deny"] == ["Bash(rm -rf /)"]
        assert data["theme"] == "dark"

    def test_idempotent_no_duplicate(self, tmp_path: Path) -> None:
        """Running twice does not duplicate the allow pattern."""
        # Arrange
        project_root = tmp_path / "project"
        project_root.mkdir()

        # Act
        configure_allowlist(project_root)
        configure_allowlist(project_root)

        # Assert
        settings_path = project_root / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        allow_list = data["permissions"]["allow"]
        count = allow_list.count(WADE_ALLOW_PATTERN)
        assert count == 1, f"Expected exactly 1 entry, got {count}"

    def test_handles_corrupted_json(self, tmp_path: Path) -> None:
        """Handles corrupted/invalid JSON gracefully by starting fresh."""
        # Arrange
        project_root = tmp_path / "project"
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        settings_path.write_text("{invalid json!!", encoding="utf-8")

        # Act
        configure_allowlist(project_root)

        # Assert — starts fresh since existing JSON was unparseable
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]

    def test_preserves_other_settings_keys(self, tmp_path: Path) -> None:
        """Preserves all non-permissions keys in existing settings."""
        # Arrange
        project_root = tmp_path / "project"
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        existing = {
            "mcpServers": {
                "memory": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-memory"],
                }
            },
            "customInstructions": "Be concise.",
            "permissions": {
                "allow": ["Read(**)"],
            },
        }
        settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

        # Act
        configure_allowlist(project_root)

        # Assert
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert data["mcpServers"] == existing["mcpServers"]
        assert data["customInstructions"] == "Be concise."
        assert "Read(**)" in data["permissions"]["allow"]
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]

    def test_handles_non_dict_permissions(self, tmp_path: Path) -> None:
        """Handles permissions being a non-dict value (e.g. a string)."""
        # Arrange
        project_root = tmp_path / "project"
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        existing = {"permissions": "invalid", "other": "kept"}
        settings_path.write_text(json.dumps(existing) + "\n", encoding="utf-8")

        # Act
        configure_allowlist(project_root)

        # Assert
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]
        assert data["other"] == "kept"

    def test_handles_non_list_allow(self, tmp_path: Path) -> None:
        """Handles allow being a non-list value (e.g. a string)."""
        # Arrange
        project_root = tmp_path / "project"
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        existing = {"permissions": {"allow": "not-a-list"}}
        settings_path.write_text(json.dumps(existing) + "\n", encoding="utf-8")

        # Act
        configure_allowlist(project_root)

        # Assert
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert isinstance(data["permissions"]["allow"], list)
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]

    def test_handles_non_dict_root(self, tmp_path: Path) -> None:
        """Handles settings.json containing a non-dict root (e.g. a list)."""
        # Arrange
        project_root = tmp_path / "project"
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        settings_path.write_text("[1, 2, 3]\n", encoding="utf-8")

        # Act
        configure_allowlist(project_root)

        # Assert — starts fresh since root was not a dict
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]

    def test_pattern_value(self) -> None:
        """Verify the constant pattern uses the colon format recognised by Claude Code."""
        assert WADE_ALLOW_PATTERN == "Bash(wade:*)"

    def test_migrates_legacy_pattern(self, tmp_path: Path) -> None:
        """Legacy Bash(wade *) entry is replaced by Bash(wade:*) on next write."""
        project_root = tmp_path / "project"
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        settings_path.write_text(
            '{"permissions": {"allow": ["Bash(wade *)"]}}\n',
            encoding="utf-8",
        )

        configure_allowlist(project_root)

        data = json.loads(settings_path.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert "Bash(wade *)" not in allow
        assert WADE_ALLOW_PATTERN in allow


class TestIsAllowlistConfigured:
    """Tests for is_allowlist_configured()."""

    def test_returns_true_when_pattern_present(self, tmp_path: Path) -> None:
        """Returns True when Bash(wade:*) is in the allowlist."""
        project_root = tmp_path / "project"
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        settings_path.write_text(
            '{"permissions": {"allow": ["Bash(wade:*)", "Read(**)"]}}\n',
            encoding="utf-8",
        )

        assert is_allowlist_configured(project_root) is True

    def test_returns_true_for_legacy_pattern(self, tmp_path: Path) -> None:
        """Returns True when the legacy Bash(wade *) pattern is present."""
        project_root = tmp_path / "project"
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        settings_path.write_text(
            '{"permissions": {"allow": ["Bash(wade *)"]}}\n',
            encoding="utf-8",
        )

        assert is_allowlist_configured(project_root) is True

    def test_returns_false_when_file_missing(self, tmp_path: Path) -> None:
        """Returns False when settings.json does not exist."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        assert is_allowlist_configured(project_root) is False

    def test_returns_false_when_pattern_absent(self, tmp_path: Path) -> None:
        """Returns False when settings.json exists but pattern is not in allowlist."""
        project_root = tmp_path / "project"
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True)
        settings_path = claude_dir / "settings.json"
        settings_path.write_text(
            '{"permissions": {"allow": ["Bash(git *)"]}}\n',
            encoding="utf-8",
        )

        assert is_allowlist_configured(project_root) is False

    def test_returns_false_for_corrupted_json(self, tmp_path: Path) -> None:
        """Returns False when settings.json contains invalid JSON."""
        project_root = tmp_path / "project"
        claude_dir = project_root / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.json").write_text("{invalid!!", encoding="utf-8")

        assert is_allowlist_configured(project_root) is False


class TestConfigurePlanHooks:
    """Tests for configure_plan_hooks()."""

    def test_adds_pretooluse_hook(self, tmp_path: Path) -> None:
        guard = tmp_path / "guard.py"
        guard.touch()
        configure_plan_hooks(tmp_path, guard)

        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.is_file()
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        hooks = data["hooks"]["PreToolUse"]
        assert len(hooks) == 1
        assert hooks[0]["matcher"] == "Edit|Write|NotebookEdit"
        # Verify hook is an object with type and command keys
        assert isinstance(hooks[0]["hooks"], list)
        assert len(hooks[0]["hooks"]) == 1
        assert hooks[0]["hooks"][0]["type"] == "command"
        assert hooks[0]["hooks"][0]["command"] == f"{sys.executable} {guard}"

    def test_idempotent(self, tmp_path: Path) -> None:
        guard = tmp_path / "guard.py"
        guard.touch()
        configure_plan_hooks(tmp_path, guard)
        configure_plan_hooks(tmp_path, guard)

        settings_path = tmp_path / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert len(data["hooks"]["PreToolUse"]) == 1

    def test_merges_with_existing_allowlist(self, tmp_path: Path) -> None:
        configure_allowlist(tmp_path)
        guard = tmp_path / "guard.py"
        guard.touch()
        configure_plan_hooks(tmp_path, guard)

        settings_path = tmp_path / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]
        assert len(data["hooks"]["PreToolUse"]) == 1


class TestConfigureWorktreeHooks:
    """Tests for configure_worktree_hooks()."""

    def test_adds_pretooluse_hook(self, tmp_path: Path) -> None:
        guard = tmp_path / "guard.py"
        guard.touch()
        configure_worktree_hooks(tmp_path, guard)

        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.is_file()
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        hooks = data["hooks"]["PreToolUse"]
        assert len(hooks) == 1
        assert hooks[0]["matcher"] == "Edit|Write|NotebookEdit"
        assert isinstance(hooks[0]["hooks"], list)
        assert len(hooks[0]["hooks"]) == 1
        assert hooks[0]["hooks"][0]["type"] == "command"
        assert hooks[0]["hooks"][0]["command"] == f"{sys.executable} {guard}"

    def test_idempotent(self, tmp_path: Path) -> None:
        guard = tmp_path / "guard.py"
        guard.touch()
        configure_worktree_hooks(tmp_path, guard)
        configure_worktree_hooks(tmp_path, guard)

        settings_path = tmp_path / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert len(data["hooks"]["PreToolUse"]) == 1

    def test_merges_with_existing_allowlist(self, tmp_path: Path) -> None:
        configure_allowlist(tmp_path)
        guard = tmp_path / "guard.py"
        guard.touch()
        configure_worktree_hooks(tmp_path, guard)

        settings_path = tmp_path / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert WADE_ALLOW_PATTERN in data["permissions"]["allow"]
        assert len(data["hooks"]["PreToolUse"]) == 1
