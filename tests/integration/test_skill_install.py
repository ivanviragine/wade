"""Integration tests for skill file installation."""

from __future__ import annotations

from pathlib import Path


class TestSkillInstallation:
    def test_install_copies_skill_files(self, tmp_git_repo: Path) -> None:
        """Skill installer copies template files to project."""
        from wade.skills.installer import install_skills

        installed = install_skills(tmp_git_repo)
        assert len(installed) > 0

        # Verify primary skills directory exists
        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert skills_dir.is_dir()

    def test_install_creates_cross_tool_symlinks(self, tmp_git_repo: Path) -> None:
        """Cross-tool directories are symlinked to .claude/skills."""
        from wade.skills.installer import install_skills

        install_skills(tmp_git_repo)

        for cross_dir in [".github/skills", ".agents/skills", ".gemini/skills"]:
            link = tmp_git_repo / cross_dir
            assert link.is_symlink(), f"{cross_dir} should be a symlink, not a plain dir"

    def test_install_idempotent(self, tmp_git_repo: Path) -> None:
        """Running install twice leaves the same on-disk state."""
        from wade.skills.installer import install_skills

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
        from wade.skills.installer import install_skills, remove_skills

        install_skills(tmp_git_repo)
        remove_skills(tmp_git_repo)

        # Primary skills dir should be gone
        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert not skills_dir.exists()


class TestSelectiveSkillInstallation:
    """Tests for selective per-command skill installation (skills parameter)."""

    def test_selective_install_only_listed_skills(self, tmp_git_repo: Path) -> None:
        """When skills parameter is provided, only those skills are installed."""
        from wade.skills.installer import install_skills

        installed = install_skills(tmp_git_repo, skills=["task", "deps"])

        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert (skills_dir / "task").is_dir()
        assert (skills_dir / "deps").is_dir()
        assert not (skills_dir / "implementation-session").exists()
        assert not (skills_dir / "plan-session").exists()
        assert not (skills_dir / "address-reviews-session").exists()

        # Installed list should only contain task and deps entries (plus cross-tool)
        skill_entries = [e for e in installed if "skills/" in e and "cross" not in e.lower()]
        for entry in skill_entries:
            assert "task" in entry or "deps" in entry

    def test_selective_install_implement_skills(self, tmp_git_repo: Path) -> None:
        """IMPLEMENT_SKILLS installs only implementation-session and task."""
        from wade.skills.installer import IMPLEMENT_SKILLS, install_skills

        install_skills(tmp_git_repo, skills=IMPLEMENT_SKILLS)

        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert (skills_dir / "implementation-session").is_dir()
        assert (skills_dir / "task").is_dir()
        assert not (skills_dir / "plan-session").exists()
        assert not (skills_dir / "deps").exists()
        assert not (skills_dir / "address-reviews-session").exists()

    def test_selective_install_review_skills(self, tmp_git_repo: Path) -> None:
        """REVIEW_SKILLS installs only address-reviews-session and task."""
        from wade.skills.installer import REVIEW_SKILLS, install_skills

        install_skills(tmp_git_repo, skills=REVIEW_SKILLS)

        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert (skills_dir / "address-reviews-session").is_dir()
        assert (skills_dir / "task").is_dir()
        assert not (skills_dir / "plan-session").exists()
        assert not (skills_dir / "deps").exists()
        assert not (skills_dir / "implementation-session").exists()

    def test_selective_install_plan_skills(self, tmp_git_repo: Path) -> None:
        """PLAN_SKILLS installs plan-session, task, and deps."""
        from wade.skills.installer import PLAN_SKILLS, install_skills

        install_skills(tmp_git_repo, skills=PLAN_SKILLS)

        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert (skills_dir / "plan-session").is_dir()
        assert (skills_dir / "task").is_dir()
        assert (skills_dir / "deps").is_dir()
        assert not (skills_dir / "implementation-session").exists()
        assert not (skills_dir / "address-reviews-session").exists()

    def test_selective_install_deps_skills(self, tmp_git_repo: Path) -> None:
        """DEPS_SKILLS installs only deps."""
        from wade.skills.installer import DEPS_SKILLS, install_skills

        install_skills(tmp_git_repo, skills=DEPS_SKILLS)

        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert (skills_dir / "deps").is_dir()
        assert not (skills_dir / "task").exists()
        assert not (skills_dir / "plan-session").exists()

    def test_none_skills_installs_all(self, tmp_git_repo: Path) -> None:
        """When skills=None (default), all skills are installed."""
        from wade.skills.installer import SKILL_FILES, install_skills

        install_skills(tmp_git_repo, skills=None)

        skills_dir = tmp_git_repo / ".claude" / "skills"
        for skill_name in SKILL_FILES:
            assert (skills_dir / skill_name).is_dir(), f"{skill_name} should be installed"

    def test_cross_tool_symlinks_always_created(self, tmp_git_repo: Path) -> None:
        """Cross-tool symlinks are created even with selective install."""
        from wade.skills.installer import install_skills

        install_skills(tmp_git_repo, skills=["deps"])

        for cross_dir in [".github/skills", ".agents/skills", ".gemini/skills", ".cursor/skills"]:
            link = tmp_git_repo / cross_dir
            assert link.is_symlink(), f"{cross_dir} should be a symlink"

    def test_selective_install_prunes_stale_skills(self, tmp_git_repo: Path) -> None:
        """Re-bootstrapping with different skills removes previously installed ones."""
        from wade.skills.installer import IMPLEMENT_SKILLS, REVIEW_SKILLS, install_skills

        # First install: implementation skills
        install_skills(tmp_git_repo, skills=IMPLEMENT_SKILLS)
        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert (skills_dir / "implementation-session").is_dir()
        assert (skills_dir / "task").is_dir()

        # Second install: review skills (simulates worktree reuse)
        install_skills(tmp_git_repo, skills=REVIEW_SKILLS)
        assert (skills_dir / "address-reviews-session").is_dir()
        assert (skills_dir / "task").is_dir()
        # Stale skill from first install should be gone
        assert not (skills_dir / "implementation-session").exists(), (
            "implementation-session should be pruned when re-bootstrapping with REVIEW_SKILLS"
        )
