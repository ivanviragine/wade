"""Tests for _check_tracked_managed_files and dirty-check session guidance."""

from __future__ import annotations

import subprocess
from pathlib import Path

from wade.services.implementation_service import (
    _check_tracked_managed_files,
    _get_dirty_file_paths,
    _identify_session_dirty_files,
)


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

    def test_detects_tracked_cross_tool_symlink(self, tmp_git_repo: Path) -> None:
        target = tmp_git_repo / ".claude" / "skills"
        target.mkdir(parents=True)
        cross_link = tmp_git_repo / ".github" / "skills"
        cross_link.parent.mkdir(parents=True)
        cross_link.symlink_to(target)
        subprocess.run(
            ["git", "add", ".github/skills"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add cross-tool symlink"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert ".github/skills" in tracked

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

    def test_detects_tracked_worktree_guard_hook(self, tmp_git_repo: Path) -> None:
        hook_dir = tmp_git_repo / ".claude" / "hooks"
        hook_dir.mkdir(parents=True)
        (hook_dir / "worktree_guard.py").write_text("hook content")
        subprocess.run(
            ["git", "add", ".claude/hooks/worktree_guard.py"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add worktree guard"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert ".claude/hooks/worktree_guard.py" in tracked

    def test_detects_tracked_plan_md(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "PLAN.md").write_text("# Plan")
        subprocess.run(["git", "add", "PLAN.md"], cwd=tmp_git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add plan"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert "PLAN.md" in tracked

    def test_detects_tracked_pr_summary(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "PR-SUMMARY.md").write_text("summary")
        subprocess.run(
            ["git", "add", "PR-SUMMARY.md"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add pr summary"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert "PR-SUMMARY.md" in tracked

    def test_detects_tracked_commit_msg(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / ".commit-msg").write_text("msg")
        subprocess.run(
            ["git", "add", ".commit-msg"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add commit msg"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert ".commit-msg" in tracked

    def test_detects_tracked_wade_managed(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / ".wade-managed").write_text("marker")
        subprocess.run(
            ["git", "add", ".wade-managed"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add wade-managed"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert ".wade-managed" in tracked

    def test_detects_tracked_wade_directory(self, tmp_git_repo: Path) -> None:
        wade_dir = tmp_git_repo / ".wade"
        wade_dir.mkdir(parents=True)
        (wade_dir / "base_branch").write_text("main")
        subprocess.run(
            ["git", "add", ".wade/base_branch"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add wade metadata"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert ".wade/base_branch" in tracked

    def test_ignores_claude_settings_json(self, tmp_git_repo: Path) -> None:
        """User-owned .claude/settings.json must NOT be flagged."""
        settings_dir = tmp_git_repo / ".claude"
        settings_dir.mkdir(parents=True)
        (settings_dir / "settings.json").write_text("{}")
        subprocess.run(
            ["git", "add", ".claude/settings.json"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add settings"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        tracked = _check_tracked_managed_files(tmp_git_repo)
        assert ".claude/settings.json" not in tracked
        assert len(tracked) == 0


class TestGetDirtyFilePaths:
    def test_returns_modified_file(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "README.md").write_text("changed")
        paths = _get_dirty_file_paths(tmp_git_repo)
        assert "README.md" in paths

    def test_returns_untracked_file(self, tmp_git_repo: Path) -> None:
        (tmp_git_repo / "new.txt").write_text("new")
        paths = _get_dirty_file_paths(tmp_git_repo)
        assert "new.txt" in paths

    def test_clean_repo_returns_empty(self, tmp_git_repo: Path) -> None:
        paths = _get_dirty_file_paths(tmp_git_repo)
        assert paths == []


class TestIdentifySessionDirtyFiles:
    def test_identifies_plan_md(self) -> None:
        result = _identify_session_dirty_files(["PLAN.md", "src/app.py"])
        assert "PLAN.md" in result
        assert "src/app.py" not in result

    def test_identifies_pr_summary(self) -> None:
        result = _identify_session_dirty_files(["PR-SUMMARY.md"])
        assert "PR-SUMMARY.md" in result

    def test_identifies_claude_settings(self) -> None:
        result = _identify_session_dirty_files([".claude/settings.json"])
        assert ".claude/settings.json" in result

    def test_identifies_wade_directory_files(self) -> None:
        result = _identify_session_dirty_files([".wade/base_branch", ".wade/state"])
        assert ".wade/base_branch" in result
        assert ".wade/state" in result

    def test_identifies_skill_file(self) -> None:
        result = _identify_session_dirty_files([".claude/skills/implementation-session/SKILL.md"])
        assert ".claude/skills/implementation-session/SKILL.md" in result

    def test_ignores_user_files(self) -> None:
        result = _identify_session_dirty_files(["src/main.py", "README.md"])
        assert result == []

    def test_mixed_dirty_files(self) -> None:
        """Session artifacts are identified among normal dirty files."""
        dirty = ["src/app.py", ".claude/settings.json", "PLAN.md", "tests/test_foo.py"]
        result = _identify_session_dirty_files(dirty)
        assert ".claude/settings.json" in result
        assert "PLAN.md" in result
        assert "src/app.py" not in result
        assert "tests/test_foo.py" not in result
