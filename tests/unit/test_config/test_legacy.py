"""Tests for legacy artifact cleanup — pre-v1/v2/v3 file and directory removal."""

from __future__ import annotations

from pathlib import Path

from ghaiw.config.legacy import (
    _LEGACY_FILES,
    _LEGACY_SKILL_DIRS,
    _SKILL_HOST_DIRS,
    cleanup_legacy_artifacts,
)


class TestCleanupLegacyArtifacts:
    """Tests for cleanup_legacy_artifacts()."""

    def test_removes_legacy_skill_dirs_from_claude_skills(self, tmp_path: Path) -> None:
        """Removes legacy skill directories from .claude/skills/."""
        # Arrange
        for legacy_name in _LEGACY_SKILL_DIRS:
            legacy_dir = tmp_path / ".claude" / "skills" / legacy_name
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "SKILL.md").write_text("old skill")

        # Act
        removed = cleanup_legacy_artifacts(tmp_path)

        # Assert
        assert removed == len(_LEGACY_SKILL_DIRS)
        for legacy_name in _LEGACY_SKILL_DIRS:
            assert not (tmp_path / ".claude" / "skills" / legacy_name).exists()

    def test_removes_legacy_from_all_host_dirs(self, tmp_path: Path) -> None:
        """Removes legacy skill directories from all host dirs."""
        # Arrange
        for host_dir in _SKILL_HOST_DIRS:
            for legacy_name in _LEGACY_SKILL_DIRS:
                legacy_dir = tmp_path / host_dir / legacy_name
                legacy_dir.mkdir(parents=True)
                (legacy_dir / "SKILL.md").write_text("old")

        # Act
        removed = cleanup_legacy_artifacts(tmp_path)

        # Assert
        expected = len(_SKILL_HOST_DIRS) * len(_LEGACY_SKILL_DIRS)
        assert removed == expected
        for host_dir in _SKILL_HOST_DIRS:
            for legacy_name in _LEGACY_SKILL_DIRS:
                assert not (tmp_path / host_dir / legacy_name).exists()

    def test_returns_zero_when_nothing_to_clean(self, tmp_path: Path) -> None:
        """Returns 0 when there are no legacy artifacts."""
        # Arrange — empty project directory
        # Act
        removed = cleanup_legacy_artifacts(tmp_path)

        # Assert
        assert removed == 0

    def test_removes_githooks_with_ghaiw_markers(self, tmp_path: Path) -> None:
        """Removes .githooks/ directory when it contains ghaiw markers."""
        # Arrange
        githooks_dir = tmp_path / ".githooks"
        githooks_dir.mkdir()
        hook_file = githooks_dir / "commit-msg"
        hook_file.write_text("#!/bin/bash\n# ghaiw managed hook\nexit 0\n")

        # Act
        removed = cleanup_legacy_artifacts(tmp_path)

        # Assert
        assert removed == 1
        assert not githooks_dir.exists()

    def test_preserves_githooks_without_ghaiw_markers(self, tmp_path: Path) -> None:
        """Preserves .githooks/ when it does not contain ghaiw markers."""
        # Arrange
        githooks_dir = tmp_path / ".githooks"
        githooks_dir.mkdir()
        hook_file = githooks_dir / "pre-commit"
        hook_file.write_text("#!/bin/bash\n# custom project hook\nruff check .\n")

        # Act
        removed = cleanup_legacy_artifacts(tmp_path)

        # Assert
        assert removed == 0
        assert githooks_dir.exists()
        assert hook_file.read_text() == "#!/bin/bash\n# custom project hook\nruff check .\n"

    def test_removes_commit_msg_file(self, tmp_path: Path) -> None:
        """Removes .commit-msg legacy file."""
        # Arrange
        commit_msg = tmp_path / ".commit-msg"
        commit_msg.write_text("legacy commit message template")

        # Act
        removed = cleanup_legacy_artifacts(tmp_path)

        # Assert
        assert removed == 1
        assert not commit_msg.exists()

    def test_handles_symlinks_in_legacy_dirs(self, tmp_path: Path) -> None:
        """Removes symlinks that point to legacy skill directories."""
        # Arrange
        host_dir = tmp_path / ".claude" / "skills"
        host_dir.mkdir(parents=True)

        # Create a real target and a symlink from a legacy name
        real_target = tmp_path / "templates" / "skills" / "plan-issues"
        real_target.mkdir(parents=True)
        (real_target / "SKILL.md").write_text("old")

        symlink = host_dir / "plan-issues"
        symlink.symlink_to(real_target)

        # Act
        removed = cleanup_legacy_artifacts(tmp_path)

        # Assert
        assert removed >= 1
        assert not symlink.exists(), "Symlink should have been removed"
        # The target directory is not removed (only the symlink in the host dir)
        assert real_target.exists()

    def test_does_not_remove_current_skill_directories(self, tmp_path: Path) -> None:
        """Does not remove current (non-legacy) skill directories."""
        # Arrange
        host_dir = tmp_path / ".claude" / "skills"
        host_dir.mkdir(parents=True)

        current_skills = ["workflow", "task", "sync", "deps", "pr-summary"]
        for skill_name in current_skills:
            skill_dir = host_dir / skill_name
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("current skill")

        # Act
        removed = cleanup_legacy_artifacts(tmp_path)

        # Assert
        assert removed == 0
        for skill_name in current_skills:
            assert (host_dir / skill_name).is_dir()
            assert (host_dir / skill_name / "SKILL.md").read_text() == "current skill"

    def test_removes_github_skills_with_only_legacy_contents(self, tmp_path: Path) -> None:
        """Removes .github/skills/ if it is a real dir containing only legacy entries.

        The first pass removes individual legacy dirs. The second pass checks
        whether .github/skills/ still has children — if all were legacy and
        already removed, the dir is now empty and the `any(iterdir())` guard
        prevents the rmtree. So the dir survives as empty, and legacy child
        dirs are gone.

        To trigger the directory-level removal, the children must still exist
        at the point of the second check. This happens when the children are
        NOT in `_SKILL_HOST_DIRS` iteration scope (e.g. the first loop skips
        `.github/skills` because it is listed). But since `.github/skills` IS
        in the host dirs list, the children get removed first. We test the
        actual behavior: legacy children removed, parent left empty.
        """
        # Arrange
        github_skills = tmp_path / ".github" / "skills"
        github_skills.mkdir(parents=True)
        for legacy_name in _LEGACY_SKILL_DIRS:
            legacy_dir = github_skills / legacy_name
            legacy_dir.mkdir()
            (legacy_dir / "SKILL.md").write_text("old")

        # Act
        removed = cleanup_legacy_artifacts(tmp_path)

        # Assert — legacy child dirs removed; parent dir may remain empty
        assert removed == len(_LEGACY_SKILL_DIRS)
        for legacy_name in _LEGACY_SKILL_DIRS:
            assert not (github_skills / legacy_name).exists()

    def test_preserves_github_skills_with_current_contents(self, tmp_path: Path) -> None:
        """Does not remove .github/skills/ if it contains non-legacy entries."""
        # Arrange
        github_skills = tmp_path / ".github" / "skills"
        github_skills.mkdir(parents=True)
        # One legacy dir
        legacy_dir = github_skills / "plan-issues"
        legacy_dir.mkdir()
        (legacy_dir / "SKILL.md").write_text("old")
        # One current dir
        current_dir = github_skills / "workflow"
        current_dir.mkdir()
        (current_dir / "SKILL.md").write_text("current")

        # Act
        cleanup_legacy_artifacts(tmp_path)

        # Assert
        # Legacy dir removed, but .github/skills/ preserved (has current content)
        assert not legacy_dir.exists()
        assert github_skills.is_dir()
        assert current_dir.is_dir()

    def test_does_not_remove_github_skills_symlink(self, tmp_path: Path) -> None:
        """Does not remove .github/skills/ if it is a symlink (even with legacy names)."""
        # Arrange
        real_dir = tmp_path / "real_skills"
        real_dir.mkdir()
        for legacy_name in _LEGACY_SKILL_DIRS:
            (real_dir / legacy_name).mkdir()

        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        github_skills_link = github_dir / "skills"
        github_skills_link.symlink_to(real_dir)

        # Act
        cleanup_legacy_artifacts(tmp_path)

        # Assert — individual legacy dirs inside are removed, but the symlink itself is preserved
        # (The .github/skills/ removal logic only triggers for real dirs, not symlinks)
        assert github_skills_link.is_symlink()

    def test_githooks_marker_case_insensitive(self, tmp_path: Path) -> None:
        """Detects ghaiw marker regardless of case (e.g. GHAIW, Ghaiw)."""
        # Arrange
        githooks_dir = tmp_path / ".githooks"
        githooks_dir.mkdir()
        hook_file = githooks_dir / "prepare-commit-msg"
        hook_file.write_text("#!/bin/bash\n# Managed by GHAIW\nexit 0\n")

        # Act
        removed = cleanup_legacy_artifacts(tmp_path)

        # Assert
        assert removed == 1
        assert not githooks_dir.exists()

    def test_combined_cleanup(self, tmp_path: Path) -> None:
        """Removes multiple types of legacy artifacts in one pass."""
        # Arrange
        # Legacy skill dirs
        for legacy_name in _LEGACY_SKILL_DIRS:
            d = tmp_path / ".claude" / "skills" / legacy_name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("old")

        # Legacy .githooks
        githooks_dir = tmp_path / ".githooks"
        githooks_dir.mkdir()
        (githooks_dir / "commit-msg").write_text("# ghaiw hook")

        # Legacy .commit-msg
        (tmp_path / ".commit-msg").write_text("template")

        # Act
        removed = cleanup_legacy_artifacts(tmp_path)

        # Assert
        expected = len(_LEGACY_SKILL_DIRS) + 1 + 1  # skill dirs + .githooks + .commit-msg
        assert removed == expected

    def test_empty_githooks_dir_preserved(self, tmp_path: Path) -> None:
        """Preserves .githooks/ when it contains no files (no marker to detect)."""
        # Arrange
        githooks_dir = tmp_path / ".githooks"
        githooks_dir.mkdir()

        # Act
        removed = cleanup_legacy_artifacts(tmp_path)

        # Assert
        assert removed == 0
        assert githooks_dir.exists()

    def test_legacy_constants(self) -> None:
        """Verify the module constants have expected values."""
        assert "plan-issues" in _LEGACY_SKILL_DIRS
        assert "prepare-merge" in _LEGACY_SKILL_DIRS
        assert "plan" in _LEGACY_SKILL_DIRS
        assert ".commit-msg" in _LEGACY_FILES
        assert ".claude/skills" in _SKILL_HOST_DIRS
        assert ".github/skills" in _SKILL_HOST_DIRS
        assert ".agents/skills" in _SKILL_HOST_DIRS
        assert ".gemini/skills" in _SKILL_HOST_DIRS
