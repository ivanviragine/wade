"""Tests for worktree gitignore write/strip and done-flow block stripping."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from wade.services.implementation_service import (
    WORKTREE_GITIGNORE_MARKER_END,
    WORKTREE_GITIGNORE_MARKER_START,
    _do_suppress_pointer_artifacts,
    strip_worktree_gitignore,
    write_worktree_gitignore,
)


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """Create a minimal git worktree for testing."""
    wt = tmp_path / "worktree"
    wt.mkdir()
    subprocess.run(["git", "init", "-b", "test"], cwd=wt, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=wt, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=wt, capture_output=True)
    # Create a tracked .gitignore so --skip-worktree can be tested
    (wt / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=wt, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=wt, capture_output=True, check=True)
    return wt


class TestWriteWorktreeGitignore:
    def test_creates_block_with_markers(self, worktree: Path) -> None:
        write_worktree_gitignore(worktree)
        content = (worktree / ".gitignore").read_text()
        assert WORKTREE_GITIGNORE_MARKER_START in content
        assert WORKTREE_GITIGNORE_MARKER_END in content

    def test_preserves_existing_content(self, worktree: Path) -> None:
        write_worktree_gitignore(worktree)
        content = (worktree / ".gitignore").read_text()
        assert "__pycache__/" in content

    def test_includes_specific_skill_files(self, worktree: Path) -> None:
        write_worktree_gitignore(worktree)
        content = (worktree / ".gitignore").read_text()
        # Should list specific files, not directories
        assert ".claude/skills/task/SKILL.md" in content
        assert ".claude/skills/implementation-session/SKILL.md" in content

    def test_includes_session_artifacts(self, worktree: Path) -> None:
        write_worktree_gitignore(worktree)
        content = (worktree / ".gitignore").read_text()
        assert "PLAN.md" in content
        assert "PR-SUMMARY.md" in content
        assert ".wade/" in content

    def test_includes_guard_hook_scripts(self, worktree: Path) -> None:
        write_worktree_gitignore(worktree)
        content = (worktree / ".gitignore").read_text()
        assert ".claude/hooks/plan_write_guard.py" in content
        assert ".claude/hooks/worktree_guard.py" in content

    def test_includes_cross_tool_symlinks_when_present(self, worktree: Path) -> None:
        # Create a cross-tool symlink
        target = worktree / ".claude" / "skills"
        target.mkdir(parents=True)
        (worktree / ".github").mkdir()
        (worktree / ".github" / "skills").symlink_to(target)

        write_worktree_gitignore(worktree)
        content = (worktree / ".gitignore").read_text()
        assert ".github/skills" in content

    def test_excludes_cross_tool_real_dirs(self, worktree: Path) -> None:
        # Create a real directory (not a symlink)
        real_dir = worktree / ".github" / "skills"
        real_dir.mkdir(parents=True)
        (real_dir / "user-skill.md").write_text("user content")

        write_worktree_gitignore(worktree)
        content = (worktree / ".gitignore").read_text()
        # Should NOT include .github/skills since it's a real dir
        lines = content.splitlines()
        # Check only lines inside the marker block
        in_block = False
        block_lines = []
        for line in lines:
            if WORKTREE_GITIGNORE_MARKER_START in line:
                in_block = True
                continue
            if WORKTREE_GITIGNORE_MARKER_END in line:
                break
            if in_block:
                block_lines.append(line)
        assert ".github/skills" not in block_lines

    def test_includes_untracked_pointer_files(self, worktree: Path) -> None:
        # Create an untracked AGENTS.md
        (worktree / "AGENTS.md").write_text("# Agents\n")
        write_worktree_gitignore(worktree)
        content = (worktree / ".gitignore").read_text()
        assert "AGENTS.md" in content

    def test_excludes_tracked_pointer_files(self, worktree: Path) -> None:
        # Create and track AGENTS.md
        (worktree / "AGENTS.md").write_text("# Agents\n")
        subprocess.run(["git", "add", "AGENTS.md"], cwd=worktree, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add agents"], cwd=worktree, capture_output=True)

        write_worktree_gitignore(worktree)
        content = (worktree / ".gitignore").read_text()
        # Should NOT add AGENTS.md to gitignore (it's tracked — skip-worktree handles it)
        lines = content.splitlines()
        in_block = False
        block_lines = []
        for line in lines:
            if WORKTREE_GITIGNORE_MARKER_START in line:
                in_block = True
                continue
            if WORKTREE_GITIGNORE_MARKER_END in line:
                break
            if in_block:
                block_lines.append(line)
        assert "AGENTS.md" not in block_lines

    def test_idempotent(self, worktree: Path) -> None:
        write_worktree_gitignore(worktree)
        content1 = (worktree / ".gitignore").read_text()
        write_worktree_gitignore(worktree)
        content2 = (worktree / ".gitignore").read_text()
        assert content1 == content2

    def test_creates_gitignore_when_missing(self, tmp_path: Path) -> None:
        """When no .gitignore exists, creates one with just the block."""
        wt = tmp_path / "wt"
        wt.mkdir()
        # No git init — write_worktree_gitignore handles missing git gracefully
        write_worktree_gitignore(wt)
        content = (wt / ".gitignore").read_text()
        assert WORKTREE_GITIGNORE_MARKER_START in content
        assert "PLAN.md" in content


class TestStripWorktreeGitignore:
    def test_removes_block(self, worktree: Path) -> None:
        write_worktree_gitignore(worktree)
        strip_worktree_gitignore(worktree)
        content = (worktree / ".gitignore").read_text()
        assert WORKTREE_GITIGNORE_MARKER_START not in content
        assert WORKTREE_GITIGNORE_MARKER_END not in content

    def test_preserves_user_content(self, worktree: Path) -> None:
        write_worktree_gitignore(worktree)
        strip_worktree_gitignore(worktree)
        content = (worktree / ".gitignore").read_text()
        assert "__pycache__/" in content

    def test_no_op_when_no_block(self, worktree: Path) -> None:
        original = (worktree / ".gitignore").read_text()
        strip_worktree_gitignore(worktree)
        assert (worktree / ".gitignore").read_text() == original

    def test_no_op_when_no_file(self, tmp_path: Path) -> None:
        strip_worktree_gitignore(tmp_path)  # must not raise


class TestSuppressPointerArtifacts:
    """Verify _do_suppress_pointer_artifacts no longer writes to info/exclude."""

    def test_does_not_write_info_exclude(self, worktree: Path) -> None:
        """Untracked pointer files should NOT be added to info/exclude."""
        # Create an untracked AGENTS.md
        (worktree / "AGENTS.md").write_text("# Agents\n")

        _do_suppress_pointer_artifacts(worktree)

        # Find the git dir
        result = subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
        )
        gitdir = Path(result.stdout.strip())
        exclude = gitdir / "info" / "exclude"
        if exclude.is_file():
            content = exclude.read_text()
            assert "AGENTS.md" not in content

    def test_skip_worktree_on_tracked_files(self, worktree: Path) -> None:
        """Tracked pointer files should get --skip-worktree."""
        (worktree / "AGENTS.md").write_text("# Agents\n")
        subprocess.run(["git", "add", "AGENTS.md"], cwd=worktree, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add"], cwd=worktree, capture_output=True)
        # Modify the file (simulates pointer injection)
        (worktree / "AGENTS.md").write_text("# Modified\n")

        _do_suppress_pointer_artifacts(worktree)

        # Verify skip-worktree is set
        result = subprocess.run(
            ["git", "ls-files", "-v", "AGENTS.md"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
        )
        # 'S' prefix indicates skip-worktree
        assert result.stdout.startswith("S")
