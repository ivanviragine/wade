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

    def test_partial_expansion_in_installed_skill(self, tmp_git_repo: Path) -> None:
        """Partial placeholders are expanded when skills are copied to a project."""
        from wade.skills.installer import install_skills

        install_skills(tmp_git_repo, skills=["plan-session"])

        skill_md = tmp_git_repo / ".claude" / "skills" / "plan-session" / "SKILL.md"
        assert skill_md.is_file()
        content = skill_md.read_text(encoding="utf-8")
        assert "{user_interaction_prompt}" not in content, "Placeholder must be expanded"
        assert "## User interaction" in content, "Partial heading must be injected"
        assert "Key decision points:" in content, "Partial content must be injected"

    def test_review_enforcement_rule_expanded_by_default(self, tmp_git_repo: Path) -> None:
        """review_enforcement_rule partial is included by default (reviews enabled)."""
        from wade.skills.installer import install_skills

        install_skills(tmp_git_repo, skills=["implementation-session"])

        skill_md = tmp_git_repo / ".claude" / "skills" / "implementation-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{review_enforcement_rule}" not in content, "Placeholder must be expanded"
        assert "## Never skip review" in content, "Rule heading must be present by default"

    def test_review_enforcement_rule_suppressed_by_extra_partials(self, tmp_git_repo: Path) -> None:
        """Passing empty string via extra_partials suppresses the review enforcement rule."""
        from wade.skills.installer import install_skills

        install_skills(
            tmp_git_repo,
            skills=["implementation-session"],
            extra_partials={"{review_enforcement_rule}": ""},
        )

        skill_md = tmp_git_repo / ".claude" / "skills" / "implementation-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{review_enforcement_rule}" not in content, "Placeholder must be removed"
        assert "## Never skip review" not in content, "Rule must be absent when suppressed"

    def test_review_enforcement_rule_suppressed_via_config(self, tmp_git_repo: Path) -> None:
        """bootstrap_worktree with review_implementation.enabled=False suppresses the rule."""
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
        from wade.services.implementation_service import bootstrap_worktree

        config = ProjectConfig(ai=AIConfig(review_implementation=AICommandConfig(enabled=False)))
        bootstrap_worktree(tmp_git_repo, config, tmp_git_repo, skills=["implementation-session"])

        skill_md = tmp_git_repo / ".claude" / "skills" / "implementation-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{review_enforcement_rule}" not in content, "Placeholder must be removed"
        assert "## Never skip review" not in content, (
            "Rule must be absent when suppressed via config"
        )

    def test_self_init_inject_skills_are_not_symlinked(self, tmp_git_repo: Path) -> None:
        """In self-init mode, inject skills are processed copies — not directory symlinks."""
        from wade.skills.installer import install_skills

        install_skills(tmp_git_repo, is_self_init=True, skills=["plan-session"])

        skill_dir = tmp_git_repo / ".claude" / "skills" / "plan-session"
        assert not skill_dir.is_symlink(), "plan-session should not be a dir symlink in self-init"
        assert skill_dir.is_dir()
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "{user_interaction_prompt}" not in content


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
        assert not (skills_dir / "review-pr-comments-session").exists()

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
        assert not (skills_dir / "review-pr-comments-session").exists()

    def test_selective_install_review_skills(self, tmp_git_repo: Path) -> None:
        """REVIEW_SKILLS installs only review-pr-comments-session and task."""
        from wade.skills.installer import REVIEW_SKILLS, install_skills

        install_skills(tmp_git_repo, skills=REVIEW_SKILLS)

        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert (skills_dir / "review-pr-comments-session").is_dir()
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
        assert not (skills_dir / "review-pr-comments-session").exists()

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

    def test_selective_install_preserves_user_owned_skills(self, tmp_git_repo: Path) -> None:
        """Pruning only removes Wade-managed skills, not user-owned directories."""
        from wade.skills.installer import install_skills

        # First install all skills
        install_skills(tmp_git_repo, skills=["task", "deps"])
        skills_dir = tmp_git_repo / ".claude" / "skills"

        # Create a user-owned custom skill directory
        custom_dir = skills_dir / "my-custom-skill"
        custom_dir.mkdir(parents=True, exist_ok=True)
        (custom_dir / "SKILL.md").write_text("# My Custom Skill")

        # Re-bootstrap with different skills — user dir must survive
        install_skills(tmp_git_repo, skills=["task"])
        assert not (skills_dir / "deps").exists(), "deps should be pruned"
        assert custom_dir.is_dir(), "user-owned skill dir should be preserved"
        assert (custom_dir / "SKILL.md").read_text() == "# My Custom Skill"

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
        assert (skills_dir / "review-pr-comments-session").is_dir()
        assert (skills_dir / "task").is_dir()
        # Stale skill from first install should be gone
        assert not (skills_dir / "implementation-session").exists(), (
            "implementation-session should be pruned when re-bootstrapping with REVIEW_SKILLS"
        )
