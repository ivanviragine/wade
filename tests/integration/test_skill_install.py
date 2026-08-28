"""Integration contracts for compatibility skill installation and fixed workflows."""

from __future__ import annotations

from pathlib import Path

import pytest

from wade.models.config import AICommandConfig, AIConfig, ProjectConfig
from wade.models.workflow import SessionKind
from wade.services.session_composition_service import compose_session
from wade.skills.installer import (
    DEPS_SKILLS,
    IMPLEMENT_SKILLS,
    PLAN_SKILLS,
    REVIEW_SKILLS,
    SKILL_FILES,
    install_skills,
    remove_skills,
)


class TestCompatibilitySkillInstallation:
    def test_install_copies_registered_compatibility_skills(self, tmp_git_repo: Path) -> None:
        installed = install_skills(tmp_git_repo)

        assert installed
        skills_dir = tmp_git_repo / ".claude/skills"
        assert skills_dir.is_dir()
        for name in SKILL_FILES:
            assert (skills_dir / name / "SKILL.md").is_file()

    def test_install_creates_cross_tool_symlinks(self, tmp_git_repo: Path) -> None:
        install_skills(tmp_git_repo)

        for relative in (".github/skills", ".agents/skills", ".cursor/skills"):
            assert (tmp_git_repo / relative).is_symlink()

    def test_install_is_idempotent(self, tmp_git_repo: Path) -> None:
        install_skills(tmp_git_repo)
        first = {
            path.relative_to(tmp_git_repo).as_posix()
            for path in (tmp_git_repo / ".claude/skills").rglob("*")
            if path.is_file()
        }

        install_skills(tmp_git_repo)
        second = {
            path.relative_to(tmp_git_repo).as_posix()
            for path in (tmp_git_repo / ".claude/skills").rglob("*")
            if path.is_file()
        }

        assert second == first

    def test_remove_cleans_only_wade_compatibility_skills(self, tmp_git_repo: Path) -> None:
        install_skills(tmp_git_repo)
        remove_skills(tmp_git_repo)

        assert not (tmp_git_repo / ".claude/skills").exists()

    @pytest.mark.parametrize(
        "name",
        ("plan-session", "implementation-session", "review-pr-comments-session"),
    )
    def test_phase_skill_is_a_thin_workflow_pointer(self, tmp_git_repo: Path, name: str) -> None:
        install_skills(tmp_git_repo, skills=[name])
        content = (tmp_git_repo / ".claude/skills" / name / "SKILL.md").read_text(encoding="utf-8")

        assert ".wade/session/WORKFLOW.md" in content
        assert "{review_budget_notes}" not in content
        assert "done.max_review_passes" not in content
        assert not (tmp_git_repo / ".claude/skills" / name / "reference").exists()

    def test_self_init_uses_live_native_symlinks_only_as_convenience(
        self, tmp_git_repo: Path
    ) -> None:
        install_skills(
            tmp_git_repo,
            is_self_init=True,
            skills=["implementation-session", "implementation", "code-review"],
        )

        skills_dir = tmp_git_repo / ".claude/skills"
        assert (skills_dir / "implementation-session").is_symlink()
        assert (skills_dir / "implementation").is_symlink()
        assert (skills_dir / "code-review").is_symlink()


class TestSelectiveCompatibilityInstallation:
    @pytest.mark.parametrize(
        ("selection", "present", "absent"),
        (
            (
                IMPLEMENT_SKILLS,
                {"implementation-session", "task", "knowledge"},
                {"plan-session", "deps", "review-pr-comments-session"},
            ),
            (
                REVIEW_SKILLS,
                {"review-pr-comments-session", "task", "knowledge"},
                {"plan-session", "deps", "implementation-session"},
            ),
            (
                PLAN_SKILLS,
                {"plan-session", "task", "knowledge"},
                {"deps", "implementation-session", "review-pr-comments-session"},
            ),
            (DEPS_SKILLS, {"deps"}, {"task", "plan-session", "implementation-session"}),
        ),
    )
    def test_registry_selection(
        self,
        tmp_git_repo: Path,
        selection: list[str],
        present: set[str],
        absent: set[str],
    ) -> None:
        install_skills(tmp_git_repo, skills=selection)
        skills_dir = tmp_git_repo / ".claude/skills"

        assert all((skills_dir / name).is_dir() for name in present)
        assert all(not (skills_dir / name).exists() for name in absent)

    def test_rebootstrap_prunes_only_wade_managed_names(self, tmp_git_repo: Path) -> None:
        install_skills(tmp_git_repo, skills=IMPLEMENT_SKILLS)
        skills_dir = tmp_git_repo / ".claude/skills"
        custom = skills_dir / "my-custom-skill"
        custom.mkdir()
        (custom / "SKILL.md").write_text("# custom\n", encoding="utf-8")

        install_skills(tmp_git_repo, skills=REVIEW_SKILLS)

        assert not (skills_dir / "implementation-session").exists()
        assert (skills_dir / "review-pr-comments-session").is_dir()
        assert (custom / "SKILL.md").read_text(encoding="utf-8") == "# custom\n"

    @pytest.mark.parametrize(
        "generic_name",
        ("planning", "implementation", "review-comments", "code-review"),
    )
    def test_target_project_generic_skill_names_are_never_pruned(
        self, tmp_git_repo: Path, generic_name: str
    ) -> None:
        skills_dir = tmp_git_repo / ".claude/skills"
        custom = skills_dir / generic_name
        custom.mkdir(parents=True)
        (custom / "SKILL.md").write_text("# project-owned\n", encoding="utf-8")

        install_skills(tmp_git_repo, skills=IMPLEMENT_SKILLS)

        assert (custom / "SKILL.md").read_text(encoding="utf-8") == "# project-owned\n"


class TestFixedWorkflowRendering:
    def _compose(
        self,
        root: Path,
        kind: SessionKind,
        *,
        review_enabled: bool = True,
    ) -> str:
        config = ProjectConfig(
            ai=AIConfig(
                review_plan=AICommandConfig(enabled=review_enabled),
                review_implementation=AICommandConfig(enabled=review_enabled),
            )
        )
        compose_session(
            root,
            root,
            config,
            kind=kind,
            task_id="42",
        )
        return (root / ".wade/session/WORKFLOW.md").read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("kind", "review_command", "done_command"),
        (
            (SessionKind.PLAN, "wade review plan", "wade plan-session done"),
            (
                SessionKind.IMPLEMENTATION,
                "wade review implementation",
                "wade implementation-session done",
            ),
            (
                SessionKind.REVIEW_PR_COMMENTS,
                "wade review implementation",
                "wade review-pr-comments-session done",
            ),
        ),
    )
    def test_workflow_owns_review_and_completion_when_defaults_are_used(
        self,
        tmp_git_repo: Path,
        kind: SessionKind,
        review_command: str,
        done_command: str,
    ) -> None:
        workflow = self._compose(tmp_git_repo, kind)

        assert review_command in workflow
        assert done_command in workflow
        assert "{review_step_state}" not in workflow
        assert "methodology skills" in workflow.lower()

    @pytest.mark.parametrize(
        "kind",
        (
            SessionKind.PLAN,
            SessionKind.IMPLEMENTATION,
            SessionKind.REVIEW_PR_COMMENTS,
        ),
    )
    def test_disabled_review_remains_an_explicit_workflow_step(
        self, tmp_git_repo: Path, kind: SessionKind
    ) -> None:
        workflow = self._compose(tmp_git_repo, kind, review_enabled=False)

        assert "Method review" in workflow
        assert "Skipped explicitly by project review configuration" in workflow

    @pytest.mark.parametrize(
        ("kind", "docs_command"),
        (
            (SessionKind.IMPLEMENTATION, "wade implementation-session docs"),
            (
                SessionKind.REVIEW_PR_COMMENTS,
                "wade review-pr-comments-session docs",
            ),
        ),
    )
    def test_documentation_targets_and_receipt_command_are_workflow_owned(
        self,
        tmp_git_repo: Path,
        kind: SessionKind,
        docs_command: str,
    ) -> None:
        (tmp_git_repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
        (tmp_git_repo / "docs").mkdir()
        workflow = self._compose(tmp_git_repo, kind)

        assert "README.md" in workflow
        assert "AGENTS.md" in workflow
        assert "docs/" in workflow
        assert docs_command in workflow
        assert "Documentation [mandatory decision]" in workflow
