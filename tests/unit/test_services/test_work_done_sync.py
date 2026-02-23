"""Tests for work service — done, sync, list, remove, and helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ghaiw.git.repo import GitError
from ghaiw.models.ai import TokenUsage
from ghaiw.models.config import ProjectConfig, ProjectSettings
from ghaiw.models.task import Task, TaskState
from ghaiw.models.work import WorktreeState
from ghaiw.services.work_service import (
    IMPL_USAGE_MARKER_END,
    IMPL_USAGE_MARKER_START,
    _build_pr_body,
    _extract_plan_summary,
    _strip_impl_usage_block,
    build_impl_usage_block,
    classify_staleness,
    extract_issue_from_branch,
    list_sessions,
)

# ---------------------------------------------------------------------------
# Branch/issue extraction
# ---------------------------------------------------------------------------


class TestExtractIssueFromBranch:
    def test_standard_branch(self) -> None:
        assert extract_issue_from_branch("feat/42-add-auth") == "42"

    def test_nested_prefix(self) -> None:
        assert extract_issue_from_branch("fix/123-bug-fix") == "123"

    def test_no_issue_number(self) -> None:
        assert extract_issue_from_branch("main") is None

    def test_no_slash(self) -> None:
        assert extract_issue_from_branch("feature-branch") is None

    def test_multiple_numbers(self) -> None:
        # Should match the first number after a slash
        assert extract_issue_from_branch("feat/42-add-auth-99") == "42"


# ---------------------------------------------------------------------------
# Staleness classification
# ---------------------------------------------------------------------------


class TestClassifyStaleness:
    def test_active_when_issue_open(self, tmp_git_repo: Path) -> None:
        from ghaiw.git.worktree import create_worktree

        branch = "feat/42-test"
        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, branch, wt_dir, "main")

        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test", state=TaskState.OPEN)

        result = classify_staleness(
            repo_root=tmp_git_repo,
            branch=branch,
            main_branch="main",
            issue_number="42",
            provider=provider,
        )
        assert result == WorktreeState.ACTIVE

    def test_active_when_provider_fails(self, tmp_git_repo: Path) -> None:
        """Fail-safe: if we can't read the issue, treat as active."""
        from ghaiw.git.worktree import create_worktree

        branch = "feat/42-test"
        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, branch, wt_dir, "main")

        provider = MagicMock()
        provider.read_task.side_effect = Exception("API error")

        result = classify_staleness(
            repo_root=tmp_git_repo,
            branch=branch,
            main_branch="main",
            issue_number="42",
            provider=provider,
        )
        assert result == WorktreeState.ACTIVE

    def test_stale_empty_no_commits(self, tmp_git_repo: Path) -> None:
        """Branch with no commits ahead of main is stale_empty."""
        from ghaiw.git.worktree import create_worktree

        branch = "feat/42-test"
        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, branch, wt_dir, "main")

        # No provider check (no issue_number)
        result = classify_staleness(
            repo_root=tmp_git_repo,
            branch=branch,
            main_branch="main",
        )
        assert result == WorktreeState.STALE_EMPTY

    def test_active_with_commits(self, tmp_git_repo: Path) -> None:
        """Branch with commits ahead of main is active."""
        import subprocess

        from ghaiw.git.worktree import create_worktree

        branch = "feat/42-test"
        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, branch, wt_dir, "main")

        # Add a commit in the worktree
        test_file = wt_dir / "test.txt"
        test_file.write_text("test\n")
        subprocess.run(["git", "add", "."], cwd=wt_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "test commit"],
            cwd=wt_dir,
            capture_output=True,
            check=True,
        )

        result = classify_staleness(
            repo_root=tmp_git_repo,
            branch=branch,
            main_branch="main",
        )
        assert result == WorktreeState.ACTIVE


# ---------------------------------------------------------------------------
# Implementation usage block
# ---------------------------------------------------------------------------


class TestBuildImplUsageBlock:
    def test_with_tool_and_model(self) -> None:
        block = build_impl_usage_block(ai_tool="claude", model="claude-sonnet-4-6")
        assert IMPL_USAGE_MARKER_START in block
        assert IMPL_USAGE_MARKER_END in block
        assert "claude" in block
        assert "claude-sonnet-4-6" in block

    def test_with_token_usage(self) -> None:
        usage = TokenUsage(
            total_tokens=10000,
            input_tokens=8000,
            output_tokens=2000,
        )
        block = build_impl_usage_block(ai_tool="claude", token_usage=usage)
        assert "10,000" in block
        assert "8,000" in block
        assert "2,000" in block

    def test_minimal(self) -> None:
        block = build_impl_usage_block()
        assert IMPL_USAGE_MARKER_START in block
        assert IMPL_USAGE_MARKER_END in block
        assert "## Implementation Usage" in block


class TestStripImplUsageBlock:
    def test_strips_block(self) -> None:
        body = (
            "Some content\n\n"
            f"{IMPL_USAGE_MARKER_START}\n"
            "## Implementation Usage\n"
            f"{IMPL_USAGE_MARKER_END}\n\n"
            "More content\n"
        )
        result = _strip_impl_usage_block(body)
        assert IMPL_USAGE_MARKER_START not in result
        assert "Some content" in result
        assert "More content" in result

    def test_no_block(self) -> None:
        body = "Just regular content\n"
        result = _strip_impl_usage_block(body)
        assert result == body

    def test_block_at_end(self) -> None:
        body = f"Content\n\n{IMPL_USAGE_MARKER_START}\n## Usage\n{IMPL_USAGE_MARKER_END}\n"
        result = _strip_impl_usage_block(body)
        assert IMPL_USAGE_MARKER_START not in result
        assert "Content" in result


# ---------------------------------------------------------------------------
# Plan summary extraction
# ---------------------------------------------------------------------------


class TestExtractPlanSummary:
    def test_extracts_summary(self) -> None:
        from ghaiw.services.task_service import (
            PLAN_SUMMARY_MARKER_END,
            PLAN_SUMMARY_MARKER_START,
        )

        body = (
            "## Tasks\n- Do stuff\n\n"
            f"{PLAN_SUMMARY_MARKER_START}\n"
            "## Plan Summary\nTokens: 1000\n"
            f"{PLAN_SUMMARY_MARKER_END}\n"
        )
        result = _extract_plan_summary(body)
        assert "## Plan Summary" in result
        assert "Tokens: 1000" in result

    def test_no_summary(self) -> None:
        body = "## Tasks\n- Do stuff\n"
        result = _extract_plan_summary(body)
        assert result == ""


# ---------------------------------------------------------------------------
# PR body composition
# ---------------------------------------------------------------------------


class TestBuildPrBody:
    def test_basic_pr_body(self) -> None:
        task = Task(id="42", title="Add auth", body="## Tasks\n- Login\n")
        body = _build_pr_body(task)
        assert "Closes #42" in body

    def test_with_parent_issue(self) -> None:
        task = Task(id="42", title="Add auth")
        body = _build_pr_body(task, parent_issue="10")
        assert "Closes #42" in body
        assert "Part of #10" in body

    def test_no_close(self) -> None:
        task = Task(id="42", title="Add auth")
        body = _build_pr_body(task, close_issue=False)
        assert "Closes" not in body

    def test_with_pr_summary_file(self, tmp_path: Path) -> None:
        pr_summary = tmp_path / "PR-SUMMARY-42.md"
        pr_summary.write_text("Added login page with OAuth support.\n")

        task = Task(id="42", title="Add auth")
        body = _build_pr_body(task, pr_summary_path=pr_summary)
        assert "Closes #42" in body
        assert "## Summary" in body
        assert "OAuth support" in body

    def test_with_plan_summary_in_body(self) -> None:
        from ghaiw.services.task_service import (
            PLAN_SUMMARY_MARKER_END,
            PLAN_SUMMARY_MARKER_START,
        )

        issue_body = (
            "## Tasks\n- Login\n\n"
            f"{PLAN_SUMMARY_MARKER_START}\n"
            "## Plan Summary\n**Tokens:** 5000\n"
            f"{PLAN_SUMMARY_MARKER_END}\n"
        )
        task = Task(id="42", title="Add auth", body=issue_body)
        body = _build_pr_body(task)
        assert "## Plan Summary" in body
        assert "Tokens" in body


# ---------------------------------------------------------------------------
# Sync tests (unit level — mocked git)
# ---------------------------------------------------------------------------


class TestSync:
    def test_not_in_git_repo(self) -> None:
        from ghaiw.services.work_service import sync

        with (
            patch(
                "ghaiw.services.work_service.load_config",
                return_value=ProjectConfig(),
            ),
            patch(
                "ghaiw.services.work_service.git_repo.get_repo_root",
                side_effect=GitError("not a repo"),
            ),
        ):
            result = sync(project_root=Path("/tmp/nonexistent"))
            assert not result.success
            assert any(e.event == "error" for e in result.events)

    def test_on_main_branch(self, tmp_git_repo: Path) -> None:
        from ghaiw.services.work_service import sync

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            # We're on main branch
            result = sync(project_root=tmp_git_repo)
            assert not result.success
            assert any(
                e.data.get("reason") == "on_main_branch"
                for e in result.events
                if e.event == "error"
            )

    def test_up_to_date(self, tmp_git_repo: Path) -> None:
        """Feature branch that's already up to date with main."""
        import subprocess

        from ghaiw.services.work_service import sync

        # Create and checkout feature branch
        subprocess.run(
            ["git", "checkout", "-b", "feat/42-test"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = sync(project_root=tmp_git_repo)
            assert result.success
            assert any(e.event == "up_to_date" for e in result.events)

    def test_dry_run(self, tmp_git_repo: Path) -> None:
        """Dry run mode reports commits behind without merging."""
        import subprocess

        from ghaiw.services.work_service import sync

        # Create feature branch
        subprocess.run(
            ["git", "checkout", "-b", "feat/42-test"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        # Go back to main and add a commit
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )
        (tmp_git_repo / "new_file.txt").write_text("new content\n")
        subprocess.run(
            ["git", "add", "new_file.txt"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "New commit on main"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        # Switch back to feature branch
        subprocess.run(
            ["git", "checkout", "feat/42-test"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = sync(dry_run=True, project_root=tmp_git_repo)
            assert result.success
            assert any(e.event == "dry_run" for e in result.events)

    def test_merge_succeeds(self, tmp_git_repo: Path) -> None:
        """Successful merge of main into feature branch."""
        import subprocess

        from ghaiw.services.work_service import sync

        # Create feature branch
        subprocess.run(
            ["git", "checkout", "-b", "feat/42-test"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        # Go back to main and add a commit (non-conflicting)
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )
        (tmp_git_repo / "main_file.txt").write_text("main content\n")
        subprocess.run(
            ["git", "add", "main_file.txt"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Main commit"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        # Switch back to feature branch
        subprocess.run(
            ["git", "checkout", "feat/42-test"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = sync(project_root=tmp_git_repo)
            assert result.success
            assert any(e.event == "merged" for e in result.events)

    def test_dirty_worktree(self, tmp_git_repo: Path) -> None:
        """Sync fails with dirty worktree."""
        import subprocess

        from ghaiw.services.work_service import sync

        # Create and checkout feature branch
        subprocess.run(
            ["git", "checkout", "-b", "feat/42-test"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        # Create dirty state
        (tmp_git_repo / "dirty.txt").write_text("dirty\n")

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = sync(project_root=tmp_git_repo)
            assert not result.success
            assert any(
                e.data.get("reason") == "dirty_worktree"
                for e in result.events
                if e.event == "error"
            )

    def test_json_output(self, tmp_git_repo: Path) -> None:
        """JSON output mode emits structured events."""
        import subprocess

        from ghaiw.services.work_service import sync

        subprocess.run(
            ["git", "checkout", "-b", "feat/42-test"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = sync(json_output=True, project_root=tmp_git_repo)
            assert result.success
            # Events should include preflight_ok
            assert any(e.event == "preflight_ok" for e in result.events)


# ---------------------------------------------------------------------------
# Done tests (mocked git + gh)
# ---------------------------------------------------------------------------


class TestDone:
    def test_not_in_git_repo(self) -> None:
        from ghaiw.services.work_service import done

        with (
            patch(
                "ghaiw.services.work_service.load_config",
                return_value=ProjectConfig(),
            ),
            patch(
                "ghaiw.services.work_service.git_repo.get_repo_root",
                side_effect=GitError("not a repo"),
            ),
        ):
            result = done(project_root=Path("/tmp/nonexistent"))
            assert not result

    def test_no_issue_in_branch(self, tmp_git_repo: Path) -> None:
        """Branch without issue number fails."""
        from ghaiw.services.work_service import done

        # We're on 'main' which has no issue number
        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = done(project_root=tmp_git_repo)
            assert not result


# ---------------------------------------------------------------------------
# List sessions tests
# ---------------------------------------------------------------------------


class TestListSessions:
    def test_lists_worktrees(self, tmp_git_repo: Path) -> None:
        from ghaiw.git.worktree import create_worktree

        # Create a worktree
        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, "feat/42-test", wt_dir, "main")

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            sessions = list_sessions(project_root=tmp_git_repo)
            assert len(sessions) == 1
            assert sessions[0]["issue"] == "42"
            assert sessions[0]["branch"] == "feat/42-test"

    def test_empty_when_no_worktrees(self, tmp_git_repo: Path) -> None:
        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            sessions = list_sessions(project_root=tmp_git_repo)
            assert len(sessions) == 0

    def test_json_output(self, tmp_git_repo: Path) -> None:
        from ghaiw.git.worktree import create_worktree

        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, "feat/42-test", wt_dir, "main")

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            sessions = list_sessions(json_output=True, project_root=tmp_git_repo)
            assert len(sessions) == 1

    def test_show_all_includes_main(self, tmp_git_repo: Path) -> None:
        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            sessions = list_sessions(show_all=True, project_root=tmp_git_repo)
            # Main checkout should be included
            assert len(sessions) >= 1


# ---------------------------------------------------------------------------
# Remove tests
# ---------------------------------------------------------------------------


class TestRemove:
    def test_remove_by_target(self, tmp_git_repo: Path) -> None:
        from ghaiw.git.worktree import create_worktree
        from ghaiw.services.work_service import remove

        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, "feat/42-test", wt_dir, "main")

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = remove(target="42", project_root=tmp_git_repo)
            assert result
            assert not wt_dir.exists()

    def test_remove_unknown_target(self, tmp_git_repo: Path) -> None:
        from ghaiw.services.work_service import remove

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = remove(target="999", project_root=tmp_git_repo)
            assert not result

    def test_remove_stale_no_force(self, tmp_git_repo: Path) -> None:
        """Without --force, stale removal just previews."""
        from ghaiw.git.worktree import create_worktree
        from ghaiw.services.work_service import remove

        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, "feat/42-test", wt_dir, "main")

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            # Stale but no force — should still return True (preview mode)
            result = remove(stale=True, project_root=tmp_git_repo)
            assert result
            # Worktree should still exist (not removed without force)
            assert wt_dir.exists()

    def test_remove_stale_with_force(self, tmp_git_repo: Path) -> None:
        from ghaiw.git.worktree import create_worktree
        from ghaiw.services.work_service import remove

        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, "feat/42-test", wt_dir, "main")

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = remove(stale=True, force=True, project_root=tmp_git_repo)
            assert result
            assert not wt_dir.exists()

    def test_remove_no_args(self, tmp_git_repo: Path) -> None:
        from ghaiw.services.work_service import remove

        with patch(
            "ghaiw.services.work_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = remove(project_root=tmp_git_repo)
            assert not result
