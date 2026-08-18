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

    def test_expands_untracked_directory_instead_of_collapsing(self, tmp_git_repo: Path) -> None:
        """An untracked dir with mixed content must not collapse to one porcelain line.

        Plain ``git status --porcelain`` (default ``--untracked-files=normal``)
        reports an entirely-untracked directory as a single ``?? dir/`` line once
        it holds any file that isn't otherwise ignored — which defeats
        ``_identify_session_dirty_files()``'s per-file matching (#453/#454).
        """
        skill_dir = tmp_git_repo / ".claude" / "skills" / "task"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("skill content")
        # A second, non-wade file under the same untracked top-level dir is what
        # triggers git's directory-collapse behavior.
        (tmp_git_repo / ".claude" / "notes.txt").write_text("user notes")

        paths = _get_dirty_file_paths(tmp_git_repo)

        assert ".claude/" not in paths
        assert ".claude/skills/task/SKILL.md" in paths
        assert ".claude/notes.txt" in paths


class TestIdentifySessionDirtyFiles:
    def test_identifies_plan_md(self, tmp_git_repo: Path) -> None:
        result = _identify_session_dirty_files(["PLAN.md", "src/app.py"], tmp_git_repo)
        assert "PLAN.md" in result
        assert "src/app.py" not in result

    def test_identifies_pr_summary(self, tmp_git_repo: Path) -> None:
        result = _identify_session_dirty_files(["PR-SUMMARY.md"], tmp_git_repo)
        assert "PR-SUMMARY.md" in result

    def test_identifies_claude_settings(self, tmp_git_repo: Path) -> None:
        result = _identify_session_dirty_files([".claude/settings.json"], tmp_git_repo)
        assert ".claude/settings.json" in result

    def test_identifies_wade_directory_files(self, tmp_git_repo: Path) -> None:
        result = _identify_session_dirty_files([".wade/base_branch", ".wade/state"], tmp_git_repo)
        assert ".wade/base_branch" in result
        assert ".wade/state" in result

    def test_identifies_skill_file(self, tmp_git_repo: Path) -> None:
        result = _identify_session_dirty_files(
            [".claude/skills/implementation-session/SKILL.md"], tmp_git_repo
        )
        assert ".claude/skills/implementation-session/SKILL.md" in result

    def test_ignores_user_files(self, tmp_git_repo: Path) -> None:
        result = _identify_session_dirty_files(["src/main.py", "README.md"], tmp_git_repo)
        assert result == []

    def test_mixed_dirty_files(self, tmp_git_repo: Path) -> None:
        """Session artifacts are identified among normal dirty files."""
        dirty = ["src/app.py", ".claude/settings.json", "PLAN.md", "tests/test_foo.py"]
        result = _identify_session_dirty_files(dirty, tmp_git_repo)
        assert ".claude/settings.json" in result
        assert "PLAN.md" in result
        assert "src/app.py" not in result
        assert "tests/test_foo.py" not in result

    def test_identifies_untracked_pointer_file(self, tmp_git_repo: Path) -> None:
        """An untracked AGENTS.md pointer is a session artifact, not user content."""
        (tmp_git_repo / "AGENTS.md").write_text("## Git Workflow\n")
        result = _identify_session_dirty_files(["AGENTS.md"], tmp_git_repo)
        assert "AGENTS.md" in result

    def test_ignores_tracked_pointer_file(self, tmp_git_repo: Path) -> None:
        """A tracked AGENTS.md is real project content, never a session artifact."""
        agents = tmp_git_repo / "AGENTS.md"
        agents.write_text("# Project agents doc\n")
        subprocess.run(
            ["git", "add", "AGENTS.md"], cwd=tmp_git_repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "add agents doc"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )
        agents.write_text("# Project agents doc\nedited\n")

        result = _identify_session_dirty_files(["AGENTS.md"], tmp_git_repo)
        assert "AGENTS.md" not in result

    def test_identifies_cross_tool_symlink(self, tmp_git_repo: Path) -> None:
        """A wade-created cross-tool skill symlink is a session artifact."""
        target = tmp_git_repo / ".claude" / "skills"
        target.mkdir(parents=True)
        cross_link = tmp_git_repo / ".github" / "skills"
        cross_link.parent.mkdir(parents=True)
        cross_link.symlink_to(target)

        result = _identify_session_dirty_files([".github/skills"], tmp_git_repo)
        assert ".github/skills" in result

    def test_ignores_non_symlink_cross_tool_dir(self, tmp_git_repo: Path) -> None:
        """A real (non-symlink) .github/skills dir is user content, not wade's."""
        real_dir = tmp_git_repo / ".github" / "skills"
        real_dir.mkdir(parents=True)
        (real_dir / "custom.md").write_text("custom")

        result = _identify_session_dirty_files([".github/skills"], tmp_git_repo)
        assert ".github/skills" not in result
