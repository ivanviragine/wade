"""Tests for _check_tracked_managed_files in implementation_service.py."""

from __future__ import annotations

import subprocess
from pathlib import Path

from wade.services.implementation_service import _check_tracked_managed_files


class TestCheckTrackedManagedFiles:
    def test_detects_tracked_skill_file(self, tmp_git_repo: Path) -> None:
        skill_dir = tmp_git_repo / ".claude" / "skills" / "implementation-session"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("skill content")
        subprocess.run(
            ["git", "add", ".claude/skills/implementation-session/SKILL.md"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add skill"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert ".claude/skills/implementation-session/SKILL.md" in tracked

    def test_detects_tracked_cross_tool_file(self, tmp_git_repo: Path) -> None:
        cross_dir = tmp_git_repo / ".github" / "skills"
        cross_dir.mkdir(parents=True)
        (cross_dir / "something.md").write_text("content")
        subprocess.run(
            ["git", "add", ".github/skills/something.md"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add cross-tool"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert ".github/skills/something.md" in tracked

    def test_detects_tracked_plan_write_guard(self, tmp_git_repo: Path) -> None:
        hook_dir = tmp_git_repo / ".claude" / "hooks"
        hook_dir.mkdir(parents=True)
        (hook_dir / "plan_write_guard.py").write_text("hook content")
        subprocess.run(
            ["git", "add", ".claude/hooks/plan_write_guard.py"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add hook"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert ".claude/hooks/plan_write_guard.py" in tracked

    def test_ignores_user_skill(self, tmp_git_repo: Path) -> None:
        """User-owned skills should not be flagged."""
        skill_dir = tmp_git_repo / ".claude" / "skills" / "my-custom-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("custom")
        subprocess.run(
            ["git", "add", ".claude/skills/my-custom-skill/SKILL.md"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add custom skill"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert len(tracked) == 0

    def test_ignores_user_hooks(self, tmp_git_repo: Path) -> None:
        """User-owned hooks (not plan_write_guard.py) should not be flagged."""
        hook_dir = tmp_git_repo / ".claude" / "hooks"
        hook_dir.mkdir(parents=True)
        (hook_dir / "my_hook.py").write_text("hook")
        subprocess.run(
            ["git", "add", ".claude/hooks/my_hook.py"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add user hook"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert len(tracked) == 0

    def test_clean_repo_returns_empty(self, tmp_git_repo: Path) -> None:
        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert tracked == []

    def test_returns_sorted_results(self, tmp_git_repo: Path) -> None:
        """Multiple tracked files should be returned sorted."""
        for name in ["implementation-session", "plan-session"]:
            d = tmp_git_repo / ".claude" / "skills" / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("content")
        subprocess.run(
            ["git", "add", ".claude/"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add skills"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert tracked == sorted(tracked)
        assert len(tracked) == 2

    def test_detects_legacy_skill(self, tmp_git_repo: Path) -> None:
        """Legacy skill names should also be detected."""
        d = tmp_git_repo / ".claude" / "skills" / "workflow"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("legacy")
        subprocess.run(
            ["git", "add", ".claude/skills/workflow/SKILL.md"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add legacy"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert ".claude/skills/workflow/SKILL.md" in tracked
