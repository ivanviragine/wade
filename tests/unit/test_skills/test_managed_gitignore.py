"""Tests for get_worktree_gitignore_entries in skills/installer.py."""

from __future__ import annotations

from wade.skills.installer import (
    HOOK_CONFIG_FILES,
    PLAN_GUARD_HOOK_FILES,
    SKILL_FILES,
    WORKTREE_GUARD_HOOK_FILES,
    get_worktree_gitignore_entries,
)


class TestGetWorktreeGitignoreEntries:
    def test_includes_specific_skill_files_not_directories(self) -> None:
        entries = get_worktree_gitignore_entries()
        # Must list specific files, never whole directories like ".claude/skills/task/"
        for name, files in SKILL_FILES.items():
            for filename in files:
                assert f".claude/skills/{name}/{filename}" in entries
            # Directory pattern must NOT be present
            assert f".claude/skills/{name}/" not in entries

    def test_includes_plan_guard_hook_files(self) -> None:
        entries = get_worktree_gitignore_entries()
        for hook_file in PLAN_GUARD_HOOK_FILES:
            assert hook_file in entries

    def test_includes_worktree_guard_hook_files(self) -> None:
        entries = get_worktree_gitignore_entries()
        for hook_file in WORKTREE_GUARD_HOOK_FILES:
            assert hook_file in entries

    def test_includes_hook_config_files(self) -> None:
        entries = get_worktree_gitignore_entries()
        for config_file in HOOK_CONFIG_FILES:
            assert config_file in entries

    def test_includes_session_settings_files(self) -> None:
        entries = get_worktree_gitignore_entries()
        assert ".claude/settings.json" in entries
        assert ".cursor/cli.json" in entries

    def test_includes_session_artifacts(self) -> None:
        entries = get_worktree_gitignore_entries()
        assert "PLAN.md" in entries
        assert "PR-SUMMARY.md" in entries
        assert ".commit-msg" in entries
        assert ".wade/" in entries
        assert ".wade-managed" in entries

    def test_does_not_include_cross_tool_dirs(self) -> None:
        """Cross-tool dirs are added conditionally by write_worktree_gitignore."""
        entries = get_worktree_gitignore_entries()
        assert ".github/skills" not in entries
        assert ".agents/skills" not in entries

    def test_does_not_include_pointer_files(self) -> None:
        """Pointer files are added conditionally by write_worktree_gitignore."""
        entries = get_worktree_gitignore_entries()
        assert "AGENTS.md" not in entries
        assert "CLAUDE.md" not in entries

    def test_skill_entries_are_sorted_by_skill_name(self) -> None:
        entries = get_worktree_gitignore_entries()
        skill_entries = [e for e in entries if e.startswith(".claude/skills/")]
        # Entries should be grouped and sorted by skill name
        skill_names = []
        for e in skill_entries:
            parts = e.split("/")
            name = parts[2]
            if not skill_names or skill_names[-1] != name:
                skill_names.append(name)
        assert skill_names == sorted(skill_names)
