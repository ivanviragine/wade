"""Tests for init service — init, update, deinit lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ghaiw.services.init_service import (
    GITIGNORE_ENTRIES,
    MANIFEST_FILENAME,
    deinit,
    init,
    update,
)
from ghaiw.skills.installer import SKILL_FILES, get_templates_dir
from ghaiw.skills.pointer import (
    MARKER_END,
    MARKER_START,
    ensure_pointer,
    extract_pointer_content,
    get_pointer_content,
    has_pointer,
    remove_pointer,
    write_pointer,
)


# ---------------------------------------------------------------------------
# Pointer tests
# ---------------------------------------------------------------------------


class TestPointer:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        write_pointer(target)
        assert target.is_file()
        content = target.read_text()
        assert MARKER_START in content
        assert MARKER_END in content
        assert "Git Workflow" in content

    def test_write_appends_to_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text("# My Project\n\nExisting content.\n")
        write_pointer(target)
        content = target.read_text()
        assert "# My Project" in content
        assert MARKER_START in content

    def test_has_pointer(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        assert not has_pointer(target)
        write_pointer(target)
        assert has_pointer(target)

    def test_extract_content(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        write_pointer(target)
        content = extract_pointer_content(target)
        assert content is not None
        assert "Git Workflow" in content

    def test_remove_marker_based(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text("# Project\n\nContent.\n")
        write_pointer(target)
        assert has_pointer(target)

        result = remove_pointer(target)
        assert result is True
        assert not has_pointer(target)
        # Original content preserved
        remaining = target.read_text()
        assert "# Project" in remaining

    def test_remove_old_style(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text(
            "# Project\n\n## Git Workflow\n\nSome workflow rules.\n\n## Other\n\nOther content.\n"
        )
        result = remove_pointer(target)
        assert result is True
        remaining = target.read_text()
        assert "## Other" in remaining
        assert "## Git Workflow" not in remaining

    def test_remove_empties_file(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        write_pointer(target)
        remove_pointer(target)
        assert not target.exists()

    def test_ensure_creates_agents_md(self, tmp_path: Path) -> None:
        result = ensure_pointer(tmp_path)
        assert result is not None
        assert "AGENTS.md" in result
        assert (tmp_path / "AGENTS.md").is_file()

    def test_ensure_prefers_agents_md(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Agents\n")
        (tmp_path / "CLAUDE.md").write_text("# Claude\n")
        result = ensure_pointer(tmp_path)
        assert "AGENTS.md" in result

    def test_ensure_refreshes_stale(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        # Write an old pointer
        target.write_text(
            f"{MARKER_START}\nOld content\n{MARKER_END}\n"
        )
        result = ensure_pointer(tmp_path)
        assert result is not None
        # Should have been refreshed
        content = target.read_text()
        assert "Git Workflow" in content


# ---------------------------------------------------------------------------
# Skill installer tests
# ---------------------------------------------------------------------------


class TestSkillInstaller:
    def test_get_templates_dir(self) -> None:
        tdir = get_templates_dir()
        # Should exist (we created templates/ in the repo)
        assert tdir.is_dir()

    def test_install_copies_files(self, tmp_git_repo: Path) -> None:
        from ghaiw.skills.installer import install_skills

        installed = install_skills(tmp_git_repo)
        assert len(installed) > 0

        # Check that cross-tool symlinks were created
        assert (tmp_git_repo / ".github" / "skills").exists()
        assert (tmp_git_repo / ".agents" / "skills").exists()
        assert (tmp_git_repo / ".gemini" / "skills").exists()

    def test_remove_skills(self, tmp_git_repo: Path) -> None:
        from ghaiw.skills.installer import install_skills, remove_skills

        install_skills(tmp_git_repo)
        removed = remove_skills(tmp_git_repo)
        assert len(removed) > 0

        # Cross-tool symlinks should be gone
        assert not (tmp_git_repo / ".github" / "skills").exists()


# ---------------------------------------------------------------------------
# Init service tests
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_creates_config(self, tmp_git_repo: Path) -> None:
        success = init(project_root=tmp_git_repo, non_interactive=True)
        assert success

        config_path = tmp_git_repo / ".ghaiw.yml"
        assert config_path.is_file()

        config = yaml.safe_load(config_path.read_text())
        assert config["version"] == 2
        assert config["project"]["merge_strategy"] == "PR"
        assert config["provider"]["name"] == "github"

    def test_init_creates_manifest(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        manifest = tmp_git_repo / MANIFEST_FILENAME
        assert manifest.is_file()
        content = manifest.read_text()
        assert ".ghaiw.yml" in content

    def test_init_creates_gitignore_entries(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        gitignore = tmp_git_repo / ".gitignore"
        assert gitignore.is_file()
        content = gitignore.read_text()
        assert ".ghaiw-managed" in content

    def test_init_creates_agents_pointer(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        assert (tmp_git_repo / "AGENTS.md").is_file()
        content = (tmp_git_repo / "AGENTS.md").read_text()
        assert "Git Workflow" in content

    def test_init_with_ai_tool(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, ai_tool="claude", non_interactive=True)
        config = yaml.safe_load(
            (tmp_git_repo / ".ghaiw.yml").read_text()
        )
        assert config["ai"]["default_tool"] == "claude"

    def test_init_patches_existing_config(self, tmp_git_repo: Path) -> None:
        config_path = tmp_git_repo / ".ghaiw.yml"
        config_path.write_text(
            "version: 2\n"
            "project:\n"
            "  issue_label: custom-label\n"
        )

        init(project_root=tmp_git_repo, ai_tool="claude", non_interactive=True)

        config = yaml.safe_load(config_path.read_text())
        # Should preserve existing value
        assert config["project"]["issue_label"] == "custom-label"
        # Should add missing AI tool
        assert config["ai"]["default_tool"] == "claude"

    def test_init_not_in_git_repo(self, tmp_path: Path) -> None:
        success = init(project_root=tmp_path, non_interactive=True)
        assert not success


# ---------------------------------------------------------------------------
# Update service tests
# ---------------------------------------------------------------------------


class TestUpdate:
    def test_update_refreshes_skills(self, tmp_git_repo: Path) -> None:
        # First init
        init(project_root=tmp_git_repo, non_interactive=True)

        # Then update
        success = update(project_root=tmp_git_repo)
        assert success

    def test_update_without_config_fails(self, tmp_git_repo: Path) -> None:
        success = update(project_root=tmp_git_repo)
        assert not success


# ---------------------------------------------------------------------------
# Deinit service tests
# ---------------------------------------------------------------------------


class TestDeinit:
    def test_deinit_removes_config(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        assert (tmp_git_repo / ".ghaiw.yml").is_file()

        deinit(project_root=tmp_git_repo)
        assert not (tmp_git_repo / ".ghaiw.yml").is_file()

    def test_deinit_removes_manifest(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        deinit(project_root=tmp_git_repo)
        assert not (tmp_git_repo / MANIFEST_FILENAME).is_file()

    def test_deinit_removes_pointer(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        assert (tmp_git_repo / "AGENTS.md").is_file()

        deinit(project_root=tmp_git_repo)
        # AGENTS.md should be removed (was only pointer content)
        assert not (tmp_git_repo / "AGENTS.md").is_file()

    def test_deinit_preserves_agents_content(self, tmp_git_repo: Path) -> None:
        # Create AGENTS.md with project-specific content
        agents = tmp_git_repo / "AGENTS.md"
        agents.write_text("# My Project\n\nProject-specific rules.\n")

        init(project_root=tmp_git_repo, non_interactive=True)
        deinit(project_root=tmp_git_repo)

        # AGENTS.md should still exist with project content
        assert agents.is_file()
        content = agents.read_text()
        assert "# My Project" in content
        assert MARKER_START not in content

    def test_deinit_cleans_gitignore(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        deinit(project_root=tmp_git_repo)
        gitignore = tmp_git_repo / ".gitignore"
        if gitignore.is_file():
            content = gitignore.read_text()
            assert ".ghaiw-managed" not in content

    def test_full_lifecycle(self, tmp_git_repo: Path) -> None:
        """Test init → update → deinit full lifecycle."""
        # Init
        assert init(project_root=tmp_git_repo, ai_tool="claude", non_interactive=True)
        assert (tmp_git_repo / ".ghaiw.yml").is_file()
        assert (tmp_git_repo / MANIFEST_FILENAME).is_file()
        assert (tmp_git_repo / "AGENTS.md").is_file()

        # Update
        assert update(project_root=tmp_git_repo)

        # Deinit
        assert deinit(project_root=tmp_git_repo)
        assert not (tmp_git_repo / ".ghaiw.yml").is_file()
        assert not (tmp_git_repo / MANIFEST_FILENAME).is_file()
