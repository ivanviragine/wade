"""Tests for get_managed_gitignore_patterns in skills/installer.py."""

from __future__ import annotations

from pathlib import Path

from wade.skills.installer import (
    CROSS_TOOL_DIRS,
    MANAGED_SKILL_NAMES,
    get_managed_gitignore_patterns,
)


class TestGetManagedGitignorePatterns:
    def test_includes_all_managed_skill_names(self, tmp_path: Path) -> None:
        patterns = get_managed_gitignore_patterns(tmp_path)
        for name in MANAGED_SKILL_NAMES:
            assert f".claude/skills/{name}/" in patterns

    def test_includes_cross_tool_dirs_when_absent(self, tmp_path: Path) -> None:
        """Cross-tool dirs that don't exist yet should be included."""
        patterns = get_managed_gitignore_patterns(tmp_path)
        for cross_dir in CROSS_TOOL_DIRS:
            assert cross_dir in patterns

    def test_includes_cross_tool_dir_when_symlink(self, tmp_path: Path) -> None:
        """Cross-tool dirs that are symlinks should be included."""
        target = tmp_path / ".claude" / "skills"
        target.mkdir(parents=True)
        for cross_dir in CROSS_TOOL_DIRS:
            cross = tmp_path / cross_dir
            cross.parent.mkdir(parents=True, exist_ok=True)
            cross.symlink_to(target)

        patterns = get_managed_gitignore_patterns(tmp_path)
        for cross_dir in CROSS_TOOL_DIRS:
            assert cross_dir in patterns

    def test_excludes_cross_tool_dir_when_real_directory(self, tmp_path: Path) -> None:
        """Real user directories should NOT be gitignored."""
        for cross_dir in CROSS_TOOL_DIRS:
            real_dir = tmp_path / cross_dir
            real_dir.mkdir(parents=True, exist_ok=True)
            (real_dir / "user-file.md").write_text("user content")

        patterns = get_managed_gitignore_patterns(tmp_path)
        for cross_dir in CROSS_TOOL_DIRS:
            assert cross_dir not in patterns

    def test_skill_patterns_are_sorted(self, tmp_path: Path) -> None:
        patterns = get_managed_gitignore_patterns(tmp_path)
        skill_patterns = [p for p in patterns if p.startswith(".claude/skills/")]
        assert skill_patterns == sorted(skill_patterns)

    def test_adding_skill_to_registry_adds_to_patterns(self, tmp_path: Path) -> None:
        """Verify all MANAGED_SKILL_NAMES (current + legacy) are covered."""
        patterns = get_managed_gitignore_patterns(tmp_path)
        skill_entries = {p for p in patterns if p.startswith(".claude/skills/")}
        expected = {f".claude/skills/{name}/" for name in MANAGED_SKILL_NAMES}
        assert expected == skill_entries
