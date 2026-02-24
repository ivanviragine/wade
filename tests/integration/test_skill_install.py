"""Integration tests for skill file installation."""

from __future__ import annotations

from pathlib import Path


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

        for cross_dir in [".github/skills", ".agents/skills", ".gemini/skills"]:
            link = tmp_git_repo / cross_dir
            assert link.is_symlink(), f"{cross_dir} should be a symlink, not a plain dir"

    def test_install_idempotent(self, tmp_git_repo: Path) -> None:
        """Running install twice leaves the same on-disk state."""
        from ghaiw.skills.installer import install_skills

        install_skills(tmp_git_repo)
        skills_dir = tmp_git_repo / ".claude" / "skills"
        files_after_first = {
            str(p.relative_to(tmp_git_repo)) for p in skills_dir.rglob("*") if p.is_file()
        }

        install_skills(tmp_git_repo)
        files_after_second = {
            str(p.relative_to(tmp_git_repo)) for p in skills_dir.rglob("*") if p.is_file()
        }

        assert files_after_first == files_after_second, (
            f"Second install changed on-disk state.\n"
            f"Added:   {files_after_second - files_after_first}\n"
            f"Removed: {files_after_first - files_after_second}"
        )

    def test_uninstall_removes_skills(self, tmp_git_repo: Path) -> None:
        """Uninstall removes skill directories."""
        from ghaiw.skills.installer import install_skills, remove_skills

        install_skills(tmp_git_repo)
        remove_skills(tmp_git_repo)

        # Primary skills dir should be gone
        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert not skills_dir.exists()
