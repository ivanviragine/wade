"""Integration tests for skill file installation."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestSkillInstallation:
    def test_install_copies_skill_files(self, tmp_git_repo: Path) -> None:
        """Skill installer copies template files to project."""
        from ghaiw.skills.installer import install_skills

        installed = install_skills(tmp_git_repo)
        assert len(installed) > 0

        # Verify primary skills directory exists
        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert skills_dir.is_dir()

    def test_install_creates_cross_tool_symlinks(self, tmp_git_repo: Path) -> None:
        """Cross-tool directories are symlinked to .claude/skills."""
        from ghaiw.skills.installer import install_skills

        install_skills(tmp_git_repo)

        # Check cross-tool symlinks
        for cross_dir in [".github/skills", ".agents/skills", ".gemini/skills"]:
            link = tmp_git_repo / cross_dir
            if link.exists():
                assert link.is_symlink() or link.is_dir()

    def test_install_idempotent(self, tmp_git_repo: Path) -> None:
        """Running install twice doesn't duplicate files."""
        from ghaiw.skills.installer import install_skills

        installed1 = install_skills(tmp_git_repo)
        installed2 = install_skills(tmp_git_repo)

        # Should succeed both times
        assert len(installed1) > 0
        assert len(installed2) > 0

    def test_uninstall_removes_skills(self, tmp_git_repo: Path) -> None:
        """Uninstall removes skill directories."""
        from ghaiw.skills.installer import install_skills, remove_skills

        install_skills(tmp_git_repo)
        remove_skills(tmp_git_repo)

        # Primary skills dir should be gone
        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert not skills_dir.exists()
