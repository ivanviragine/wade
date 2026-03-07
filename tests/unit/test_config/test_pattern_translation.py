"""Tests for canonical pattern translation and expanded configure_allowlist."""

from __future__ import annotations

import json
from pathlib import Path

from wade.config.claude_allowlist import (
    WADE_ALLOW_PATTERN,
    canonical_to_claude,
    configure_allowlist,
)
from wade.config.cursor_allowlist import (
    WADE_ALLOW_PATTERN as CURSOR_WADE_ALLOW_PATTERN,
)
from wade.config.cursor_allowlist import (
    canonical_to_cursor,
)
from wade.config.cursor_allowlist import (
    configure_allowlist as configure_cursor_allowlist,
)


class TestCanonicalToClaude:
    """Tests for the canonical_to_claude() helper."""

    def test_wade_wildcard(self) -> None:
        assert canonical_to_claude("wade *") == "Bash(wade *)"

    def test_script_with_wildcard(self) -> None:
        assert canonical_to_claude("./scripts/check.sh *") == "Bash(./scripts/check.sh *)"

    def test_script_without_args(self) -> None:
        assert canonical_to_claude("./scripts/fmt.sh") == "Bash(./scripts/fmt.sh)"

    def test_command_with_multi_word_args(self) -> None:
        assert (
            canonical_to_claude("wade implementation-session done")
            == "Bash(wade implementation-session done)"
        )

    def test_bare_command(self) -> None:
        assert canonical_to_claude("git") == "Bash(git)"


class TestConfigureAllowlistWithExtraPatterns:
    """Tests for configure_allowlist() with extra_patterns parameter."""

    def test_adds_extra_patterns(self, tmp_path: Path) -> None:
        """Extra patterns are translated and added to the allowlist."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        configure_allowlist(project_root, extra_patterns=["./scripts/check.sh *"])

        settings_path = project_root / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert WADE_ALLOW_PATTERN in allow
        assert "Bash(./scripts/check.sh *)" in allow

    def test_extra_patterns_idempotent(self, tmp_path: Path) -> None:
        """Running twice with same extra patterns does not duplicate."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        configure_allowlist(project_root, extra_patterns=["./scripts/check.sh *"])
        configure_allowlist(project_root, extra_patterns=["./scripts/check.sh *"])

        settings_path = project_root / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert allow.count("Bash(./scripts/check.sh *)") == 1

    def test_extra_patterns_none_uses_default_only(self, tmp_path: Path) -> None:
        """No extra_patterns adds only the default wade pattern."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        configure_allowlist(project_root)

        settings_path = project_root / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert allow == [WADE_ALLOW_PATTERN]

    def test_multiple_extra_patterns(self, tmp_path: Path) -> None:
        """Multiple extra patterns all get added."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        configure_allowlist(
            project_root,
            extra_patterns=[
                "./scripts/check.sh *",
                "./scripts/fmt.sh *",
                "./scripts/test.sh *",
            ],
        )

        settings_path = project_root / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert len(allow) == 4  # wade + 3 scripts
        assert WADE_ALLOW_PATTERN in allow
        assert "Bash(./scripts/check.sh *)" in allow
        assert "Bash(./scripts/fmt.sh *)" in allow
        assert "Bash(./scripts/test.sh *)" in allow

    def test_wade_star_in_extra_not_duplicated(self, tmp_path: Path) -> None:
        """wade * in extra_patterns does not create a duplicate."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        configure_allowlist(project_root, extra_patterns=["wade *"])

        settings_path = project_root / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert allow.count(WADE_ALLOW_PATTERN) == 1


class TestCanonicalToCursor:
    """Tests for the canonical_to_cursor() helper."""

    def test_wade_wildcard(self) -> None:
        assert canonical_to_cursor("wade *") == "Shell(wade *)"

    def test_script_with_wildcard(self) -> None:
        assert canonical_to_cursor("./scripts/check.sh *") == "Shell(./scripts/check.sh *)"

    def test_script_without_args(self) -> None:
        assert canonical_to_cursor("./scripts/fmt.sh") == "Shell(./scripts/fmt.sh)"

    def test_command_with_multi_word_args(self) -> None:
        assert (
            canonical_to_cursor("wade implementation-session done")
            == "Shell(wade implementation-session done)"
        )

    def test_bare_command(self) -> None:
        assert canonical_to_cursor("git") == "Shell(git)"


class TestCursorConfigureAllowlistWithExtraPatterns:
    """Tests for cursor configure_allowlist() with extra_patterns parameter."""

    def test_adds_extra_patterns(self, tmp_path: Path) -> None:
        """Extra patterns are translated and added to the cursor allowlist."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        configure_cursor_allowlist(project_root, extra_patterns=["./scripts/check.sh *"])

        config_path = project_root / ".cursor" / "cli.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert CURSOR_WADE_ALLOW_PATTERN in allow
        assert "Shell(./scripts/check.sh *)" in allow

    def test_extra_patterns_idempotent(self, tmp_path: Path) -> None:
        """Running twice with same extra patterns does not duplicate."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        configure_cursor_allowlist(project_root, extra_patterns=["./scripts/check.sh *"])
        configure_cursor_allowlist(project_root, extra_patterns=["./scripts/check.sh *"])

        config_path = project_root / ".cursor" / "cli.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert allow.count("Shell(./scripts/check.sh *)") == 1

    def test_extra_patterns_none_uses_default_only(self, tmp_path: Path) -> None:
        """No extra_patterns adds only the default wade pattern."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        configure_cursor_allowlist(project_root)

        config_path = project_root / ".cursor" / "cli.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert allow == [CURSOR_WADE_ALLOW_PATTERN]

    def test_multiple_extra_patterns(self, tmp_path: Path) -> None:
        """Multiple extra patterns all get added."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        configure_cursor_allowlist(
            project_root,
            extra_patterns=[
                "./scripts/check.sh *",
                "./scripts/fmt.sh *",
                "./scripts/test.sh *",
            ],
        )

        config_path = project_root / ".cursor" / "cli.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert len(allow) == 4  # wade + 3 scripts
        assert CURSOR_WADE_ALLOW_PATTERN in allow
        assert "Shell(./scripts/check.sh *)" in allow
        assert "Shell(./scripts/fmt.sh *)" in allow
        assert "Shell(./scripts/test.sh *)" in allow

    def test_wade_star_in_extra_not_duplicated(self, tmp_path: Path) -> None:
        """wade * in extra_patterns does not create a duplicate."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        configure_cursor_allowlist(project_root, extra_patterns=["wade *"])

        config_path = project_root / ".cursor" / "cli.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        allow = data["permissions"]["allow"]
        assert allow.count(CURSOR_WADE_ALLOW_PATTERN) == 1
