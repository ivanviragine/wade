"""Tests for Claude Code .claude/settings.json allowlist management."""

from __future__ import annotations

import json
from pathlib import Path

from ghaiw.config.claude_allowlist import GHAIWPY_ALLOW_PATTERN, configure_allowlist


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
                "allow": [GHAIWPY_ALLOW_PATTERN],
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
        assert GHAIWPY_ALLOW_PATTERN in data["permissions"]["allow"]
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
        count = allow_list.count(GHAIWPY_ALLOW_PATTERN)
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
        assert GHAIWPY_ALLOW_PATTERN in data["permissions"]["allow"]

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
        assert GHAIWPY_ALLOW_PATTERN in data["permissions"]["allow"]

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
        assert GHAIWPY_ALLOW_PATTERN in data["permissions"]["allow"]
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
        assert GHAIWPY_ALLOW_PATTERN in data["permissions"]["allow"]

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
        assert GHAIWPY_ALLOW_PATTERN in data["permissions"]["allow"]

    def test_pattern_value(self) -> None:
        """Verify the constant pattern has the expected value."""
        assert GHAIWPY_ALLOW_PATTERN == "Bash(ghaiwpy *)"
