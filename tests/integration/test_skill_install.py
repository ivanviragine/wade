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

        for cross_dir in [".github/skills", ".agents/skills", ".cursor/skills"]:
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

    def test_review_plan_step_expanded_by_default(self, tmp_git_repo: Path) -> None:
        """review_plan_step partial is included by default (plan review enabled)."""
        from wade.skills.installer import install_skills

        install_skills(tmp_git_repo, skills=["plan-session"])

        skill_md = tmp_git_repo / ".claude" / "skills" / "plan-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{review_plan_step}" not in content, "Placeholder must be expanded"
        assert "wade review plan <plan_file>" in content, "Full plan review step must be present"

    def test_review_plan_step_suppressed_by_extra_partials(self, tmp_git_repo: Path) -> None:
        """Passing disabled one-liner via extra_partials suppresses the plan review step."""
        from wade.skills.installer import install_skills

        disabled = "5. ~~**Review**~~ — skipped (`review_plan.enabled: false` in `.wade.yml`)."
        install_skills(
            tmp_git_repo,
            skills=["plan-session"],
            extra_partials={"{review_plan_step}": disabled},
        )

        skill_md = tmp_git_repo / ".claude" / "skills" / "plan-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{review_plan_step}" not in content, "Placeholder must be removed"
        assert "wade review plan <plan_file>" not in content, "Full step must be absent"
        assert "~~**Review**~~" in content, "Disabled one-liner must be present"

    def test_review_plan_step_suppressed_via_config(self, tmp_git_repo: Path) -> None:
        """bootstrap_worktree with review_plan.enabled=False shows disabled one-liner."""
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
        from wade.services.implementation_service import bootstrap_worktree

        config = ProjectConfig(ai=AIConfig(review_plan=AICommandConfig(enabled=False)))
        bootstrap_worktree(tmp_git_repo, config, tmp_git_repo, skills=["plan-session"])

        skill_md = tmp_git_repo / ".claude" / "skills" / "plan-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{review_plan_step}" not in content, "Placeholder must be removed"
        assert "wade review plan <plan_file>" not in content, "Full step must be absent"
        assert "~~**Review**~~" in content, "Disabled one-liner must appear"

    def test_review_implementation_closing_step_expanded_by_default(
        self, tmp_git_repo: Path
    ) -> None:
        """review_implementation_closing_step partial is included by default."""
        from wade.skills.installer import install_skills

        install_skills(tmp_git_repo, skills=["implementation-session"])

        skill_md = tmp_git_repo / ".claude" / "skills" / "implementation-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{review_implementation_closing_step}" not in content, "Placeholder must be expanded"
        assert "Step 1 — Review [MANDATORY]" in content, "Full closing step must be present"

    def test_review_implementation_closing_step_suppressed_by_extra_partials(
        self, tmp_git_repo: Path
    ) -> None:
        """Passing disabled one-liner via extra_partials suppresses the closing review step."""
        from wade.skills.installer import install_skills

        disabled = (
            "**Step 1 — ~~Review~~** — skipped"
            " (`review_implementation.enabled: false` in `.wade.yml`)."
        )
        install_skills(
            tmp_git_repo,
            skills=["implementation-session"],
            extra_partials={"{review_implementation_closing_step}": disabled},
        )

        skill_md = tmp_git_repo / ".claude" / "skills" / "implementation-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{review_implementation_closing_step}" not in content, "Placeholder must be removed"
        assert "Step 1 — Review [MANDATORY]" not in content, "Full step must be absent"
        assert "~~Review~~" in content, "Disabled one-liner must be present"

    def test_review_implementation_closing_step_suppressed_via_config(
        self, tmp_git_repo: Path
    ) -> None:
        """bootstrap_worktree with review_implementation.enabled=False suppresses closing step."""
        from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
        from wade.services.implementation_service import bootstrap_worktree

        config = ProjectConfig(ai=AIConfig(review_implementation=AICommandConfig(enabled=False)))
        bootstrap_worktree(tmp_git_repo, config, tmp_git_repo, skills=["implementation-session"])

        skill_md = tmp_git_repo / ".claude" / "skills" / "implementation-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{review_implementation_closing_step}" not in content, "Placeholder must be removed"
        assert "Step 1 — Review [MANDATORY]" not in content, "Full step must be absent"
        assert "~~Review~~" in content, "Disabled one-liner must appear"

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
        """IMPLEMENT_SKILLS installs implementation-session, task, and knowledge."""
        from wade.skills.installer import IMPLEMENT_SKILLS, install_skills

        install_skills(tmp_git_repo, skills=IMPLEMENT_SKILLS)

        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert (skills_dir / "implementation-session").is_dir()
        assert (skills_dir / "task").is_dir()
        assert (skills_dir / "knowledge").is_dir()
        assert not (skills_dir / "plan-session").exists()
        assert not (skills_dir / "deps").exists()
        assert not (skills_dir / "review-pr-comments-session").exists()

    def test_selective_install_review_skills(self, tmp_git_repo: Path) -> None:
        """REVIEW_SKILLS installs review-pr-comments-session, task, and knowledge."""
        from wade.skills.installer import REVIEW_SKILLS, install_skills

        install_skills(tmp_git_repo, skills=REVIEW_SKILLS)

        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert (skills_dir / "review-pr-comments-session").is_dir()
        assert (skills_dir / "task").is_dir()
        assert (skills_dir / "knowledge").is_dir()
        assert not (skills_dir / "plan-session").exists()
        assert not (skills_dir / "deps").exists()
        assert not (skills_dir / "implementation-session").exists()

    def test_selective_install_plan_skills(self, tmp_git_repo: Path) -> None:
        """PLAN_SKILLS installs plan-session, task, deps, and knowledge."""
        from wade.skills.installer import PLAN_SKILLS, install_skills

        install_skills(tmp_git_repo, skills=PLAN_SKILLS)

        skills_dir = tmp_git_repo / ".claude" / "skills"
        assert (skills_dir / "plan-session").is_dir()
        assert (skills_dir / "task").is_dir()
        assert (skills_dir / "deps").is_dir()
        assert (skills_dir / "knowledge").is_dir()
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

        for cross_dir in [".github/skills", ".agents/skills", ".cursor/skills"]:
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


class TestDocUpdateStep:
    """Tests for the {doc_update_step}/{doc_targets} documentation pass (#360)."""

    def test_expanded_in_implementation_session(self, tmp_git_repo: Path) -> None:
        """doc_update_step and doc_targets both expand with a concrete file list."""
        from wade.skills.installer import install_skills

        install_skills(tmp_git_repo, skills=["implementation-session"])

        skill_md = tmp_git_repo / ".claude" / "skills" / "implementation-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{doc_update_step}" not in content, "Placeholder must be expanded"
        assert "{doc_targets}" not in content, "Nested placeholder must be expanded"
        assert "**Step 2 — Documentation pass [MANDATORY]:**" in content
        assert "State the outcome before moving on" in content
        assert "`README.md`" in content, "tmp_git_repo's README.md must be detected"

    def test_expanded_in_review_pr_comments_session(self, tmp_git_repo: Path) -> None:
        """doc_update_step and doc_targets both expand in the review-pr-comments skill."""
        from wade.skills.installer import install_skills

        install_skills(tmp_git_repo, skills=["review-pr-comments-session"])

        skill_md = tmp_git_repo / ".claude" / "skills" / "review-pr-comments-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{doc_update_step}" not in content, "Placeholder must be expanded"
        assert "{doc_targets}" not in content, "Nested placeholder must be expanded"
        assert "**Step 1 — Documentation pass [MANDATORY]:**" in content
        assert "State the outcome before moving on" in content
        assert "`README.md`" in content, "tmp_git_repo's README.md must be detected"

    def test_no_placeholder_survives_in_self_init_mode(self, tmp_git_repo: Path) -> None:
        """Self-init mode processes INJECT_SKILLS as copies, so placeholders must expand too."""
        from wade.skills.installer import install_skills

        install_skills(tmp_git_repo, is_self_init=True, skills=["implementation-session"])

        skill_md = tmp_git_repo / ".claude" / "skills" / "implementation-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{doc_update_step}" not in content
        assert "{doc_targets}" not in content

    def test_doc_targets_detects_multiple_files_and_docs_dir(self, tmp_git_repo: Path) -> None:
        """All detected root files plus docs/ appear in the expanded step."""
        from wade.skills.installer import install_skills

        (tmp_git_repo / "AGENTS.md").write_text("# Agents\n")
        (tmp_git_repo / "docs").mkdir()
        (tmp_git_repo / "docs" / "guide.md").write_text("# Guide\n")

        install_skills(tmp_git_repo, skills=["implementation-session"])

        skill_md = tmp_git_repo / ".claude" / "skills" / "implementation-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "`README.md`, `AGENTS.md`, `docs/`" in content

    def test_doc_targets_empty_project_uses_fallback_wording(self, tmp_path: Path) -> None:
        """A project with no detected docs gets generic fallback wording, not an empty list."""
        from wade.skills.installer import install_skills

        project_root = tmp_path / "empty_project"
        project_root.mkdir()

        install_skills(project_root, skills=["implementation-session"])

        skill_md = project_root / ".claude" / "skills" / "implementation-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "{doc_targets}" not in content
        assert "the project's documentation, if it has any" in content

    def test_caller_supplied_doc_targets_wins_over_computed(self, tmp_git_repo: Path) -> None:
        """extra_partials overrides for {doc_targets} take precedence over detection."""
        from wade.skills.installer import install_skills

        install_skills(
            tmp_git_repo,
            skills=["implementation-session"],
            extra_partials={"{doc_targets}": "`CUSTOM.md`"},
        )

        skill_md = tmp_git_repo / ".claude" / "skills" / "implementation-session" / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        assert "`CUSTOM.md`" in content
        assert "`README.md`" not in content, "Computed value must not leak through override"

    def test_expand_partials_two_pass_resolves_nested_placeholder(self, tmp_git_repo: Path) -> None:
        """_expand_partials re-applies extra_partials after file partials expand.

        {doc_update_step} is a file partial whose content contains {doc_targets} —
        a placeholder that only exists in the string *after* the file partial is
        substituted in. Without the second extra_partials pass this would leak
        into the installed skill verbatim.
        """
        from wade.skills.installer import _expand_partials, get_skills_templates_dir

        expanded = _expand_partials(
            "before {doc_update_step} after",
            get_skills_templates_dir(),
            extra_partials={"{doc_targets}": "`README.md`"},
        )
        assert "{doc_update_step}" not in expanded
        assert "{doc_targets}" not in expanded
        assert "`README.md`" in expanded
