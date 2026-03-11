"""Integration tests for the work session lifecycle.

Tests the full flow:
  task create → implement → implementation-session sync → implementation-session done.
These tests use real git repos but mock the gh CLI and AI tools.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from wade.models.config import ProjectConfig, ProjectSettings
from wade.models.session import WorktreeState
from wade.models.task import Task


class TestImplementationLifecycle:
    """Test work session lifecycle with real git, mocked gh/AI."""

    def _setup_feature_branch(self, repo: Path, branch: str) -> None:
        """Create and checkout a feature branch."""
        subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=repo,
            capture_output=True,
            check=True,
        )

    def _add_commit(self, repo: Path, filename: str, content: str, message: str) -> None:
        """Add a file and commit it."""
        (repo / filename).write_text(content)
        subprocess.run(["git", "add", filename], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo,
            capture_output=True,
            check=True,
        )

    def test_sync_after_main_advances(self, tmp_git_repo: Path) -> None:
        """Full sync flow: feature branch syncs with main after main advances."""
        from wade.services.implementation_service import sync

        # Create feature branch
        self._setup_feature_branch(tmp_git_repo, "feat/42-add-auth")

        # Add a commit on feature
        self._add_commit(tmp_git_repo, "feature.txt", "feature\n", "Feature work")

        # Switch to main and add a commit
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )
        self._add_commit(tmp_git_repo, "main_update.txt", "update\n", "Main update")

        # Switch back to feature
        subprocess.run(
            ["git", "checkout", "feat/42-add-auth"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        # Sync
        with patch(
            "wade.services.implementation_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = sync(project_root=tmp_git_repo)

        assert result.success
        assert any(e.event == "merged" for e in result.events)

        # Verify the main commit is now on feature
        assert (tmp_git_repo / "main_update.txt").exists()

    def test_sync_with_conflict(self, tmp_git_repo: Path) -> None:
        """Sync detects merge conflicts and reports them."""
        from wade.services.implementation_service import sync

        # Create feature branch
        self._setup_feature_branch(tmp_git_repo, "feat/42-conflict")

        # Modify a file on feature
        self._add_commit(
            tmp_git_repo,
            "README.md",
            "Feature version\n",
            "Feature change",
        )

        # Switch to main and modify the same file
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )
        self._add_commit(
            tmp_git_repo,
            "README.md",
            "Main version\n",
            "Main change",
        )

        # Switch back to feature
        subprocess.run(
            ["git", "checkout", "feat/42-conflict"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        # Sync should detect conflict
        with patch(
            "wade.services.implementation_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = sync(project_root=tmp_git_repo)

        assert not result.success
        assert len(result.conflicts) > 0
        assert "README.md" in result.conflicts

        # Abort the merge for cleanup
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=tmp_git_repo,
            capture_output=True,
        )

    def test_worktree_bootstrap_and_context(self, tmp_git_repo: Path) -> None:
        """Bootstrap creates PLAN.md in worktree."""
        from wade.git.worktree import create_worktree
        from wade.services.implementation_service import write_plan_md

        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, "feat/42-test", wt_dir, "main")

        task = Task(
            id="42",
            title="Add authentication",
            body="## Tasks\n- Login page\n- OAuth\n",
            url="https://github.com/test/repo/issues/42",
        )
        plan_path = write_plan_md(wt_dir, task)

        assert plan_path.is_file()
        content = plan_path.read_text()
        assert "# Issue #42: Add authentication" in content
        assert "Login page" in content
        assert "https://github.com/test/repo/issues/42" in content

    def test_extract_issue_and_staleness_flow(self, tmp_git_repo: Path) -> None:
        """Extract issue number and classify worktree staleness."""
        from wade.git.worktree import create_worktree
        from wade.services.implementation_service import (
            classify_staleness,
            extract_issue_from_branch,
        )

        branch = "feat/42-add-auth"
        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, branch, wt_dir, "main")

        # Extract issue
        issue = extract_issue_from_branch(branch)
        assert issue == "42"

        # New worktree with no commits is stale_empty
        staleness = classify_staleness(
            repo_root=tmp_git_repo,
            branch=branch,
            main_branch="main",
        )
        assert staleness == WorktreeState.STALE_EMPTY

        # Add a commit → active
        self._add_commit(wt_dir, "work.txt", "work\n", "Do work")
        staleness = classify_staleness(
            repo_root=tmp_git_repo,
            branch=branch,
            main_branch="main",
        )
        assert staleness == WorktreeState.ACTIVE

    def test_list_and_remove_flow(self, tmp_git_repo: Path) -> None:
        """List worktrees and remove them."""
        from wade.git.worktree import create_worktree
        from wade.services.implementation_service import list_sessions, remove

        # Create two worktrees
        wt1 = tmp_git_repo.parent / "wt-42"
        wt2 = tmp_git_repo.parent / "wt-43"
        create_worktree(tmp_git_repo, "feat/42-auth", wt1, "main")
        create_worktree(tmp_git_repo, "feat/43-db", wt2, "main")

        with patch(
            "wade.services.implementation_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            # List should show both
            sessions = list_sessions(project_root=tmp_git_repo)
            assert len(sessions) == 2
            issues = {s["issue"] for s in sessions}
            assert issues == {"42", "43"}

            # Remove one
            result = remove(target="42", force=True, project_root=tmp_git_repo)
            assert result

            # List should now show one
            sessions = list_sessions(project_root=tmp_git_repo)
            assert len(sessions) == 1
            assert sessions[0]["issue"] == "43"

    def test_pr_body_composition(self, tmp_path: Path) -> None:
        """PR body includes Part of, Closes, and Summary — but NOT plan summary."""
        from wade.services.implementation_service import _build_pr_body
        from wade.services.task_service import (
            PLAN_SUMMARY_MARKER_END,
            PLAN_SUMMARY_MARKER_START,
        )

        # Create PR summary file
        pr_summary = tmp_path / "PR-SUMMARY-42.md"
        pr_summary.write_text("Implemented OAuth login with Google and GitHub providers.\n")

        # Task with plan summary in body
        task = Task(
            id="42",
            title="Add OAuth",
            body=(
                "## Tasks\n- Login\n\n"
                f"{PLAN_SUMMARY_MARKER_START}\n"
                "## Plan Summary\n**Tokens:** 5000\n"
                f"{PLAN_SUMMARY_MARKER_END}\n"
            ),
        )

        body = _build_pr_body(
            task,
            pr_summary_path=pr_summary,
            close_issue=True,
            parent_issue="10",
        )

        # Verify order: Part of, Closes, ## Summary
        closes_pos = body.find("Closes #42")
        part_of_pos = body.find("Part of #10")
        summary_pos = body.find("## Summary")

        assert part_of_pos < closes_pos
        assert closes_pos < summary_pos
        assert "OAuth login" in body
        # Plan summary must NOT appear in PR body
        assert "## Plan Summary" not in body
        assert "Tokens" not in body
