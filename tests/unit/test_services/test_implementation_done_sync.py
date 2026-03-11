"""Tests for implementation service — done, sync, list, remove, and helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from wade.git.repo import GitError
from wade.models.ai import TokenUsage
from wade.models.config import ProjectConfig, ProjectSettings
from wade.models.session import WorktreeState
from wade.models.task import Task, TaskState
from wade.services.implementation_service import (
    IMPL_USAGE_MARKER_END,
    IMPL_USAGE_MARKER_START,
    _apply_pr_refs,
    _build_pr_body,
    _strip_impl_usage_block,
    _strip_summary_section,
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
        from wade.git.worktree import create_worktree

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
        from wade.git.worktree import create_worktree

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
        from wade.git.worktree import create_worktree

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

        from wade.git.worktree import create_worktree

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
        assert "| Tool | `claude` |" in block
        assert "| Model | `claude-sonnet-4-6` |" in block
        assert "### Session 1" in block

    def test_with_token_usage(self) -> None:
        usage = TokenUsage(
            total_tokens=10000,
            input_tokens=8000,
            output_tokens=2000,
        )
        block = build_impl_usage_block(ai_tool="claude", token_usage=usage)
        assert "| Total tokens | **10,000** |" in block
        assert "| Input tokens | **8,000** |" in block
        assert "| Output tokens | **2,000** |" in block

    def test_with_model_breakdown(self) -> None:
        from wade.models.ai import ModelBreakdown

        usage = TokenUsage(
            total_tokens=5000,
            input_tokens=3400,
            output_tokens=1100,
            model_breakdown=[
                ModelBreakdown(
                    model="claude-opus-4-6",
                    input_tokens=3000,
                    output_tokens=1000,
                    cached_tokens=500,
                ),
                ModelBreakdown(
                    model="claude-haiku-4-5", input_tokens=400, output_tokens=100, cached_tokens=0
                ),
            ],
        )
        block = build_impl_usage_block(ai_tool="claude", token_usage=usage)
        assert "### Model Breakdown" not in block
        # Multi-model: names appear as column headers, not row starters
        assert "| Metric | Total | `claude-opus-4-6` | `claude-haiku-4-5` |" in block
        assert "| Input tokens | **3,400** | **3,000** | **400** |" in block
        assert "**400**" in block
        # No standalone model-name rows (rows that start with the model name)
        assert "\n| `claude-opus-4-6`" not in block
        assert "\n| `claude-haiku-4-5`" not in block

    def test_single_model_no_extra_row(self) -> None:
        from wade.models.ai import ModelBreakdown

        usage = TokenUsage(
            total_tokens=1000,
            input_tokens=800,
            output_tokens=200,
            model_breakdown=[
                ModelBreakdown(model="claude-sonnet-4-6", input_tokens=800, output_tokens=200),
            ],
        )
        block = build_impl_usage_block(
            ai_tool="claude", model="claude-sonnet-4-6", token_usage=usage
        )
        # Single model stays 2-column
        assert "| Metric | Value |" in block
        assert "| Model | `claude-sonnet-4-6` |" in block
        # No model-name column header
        assert "| Metric | Total |" not in block
        # No standalone breakdown row (rows that start with the model name)
        assert "\n| `claude-sonnet-4-6`" not in block

    def test_with_premium_requests(self) -> None:
        usage = TokenUsage(
            total_tokens=87_586,
            input_tokens=53_700,
            output_tokens=86,
            cached_tokens=33_800,
            premium_requests=5,
        )
        block = build_impl_usage_block(ai_tool="copilot", token_usage=usage)
        assert "| Premium requests (est.) | **5** |" in block
        assert "| Total tokens | **87,586** |" in block

    def test_unavailable_when_no_tokens(self) -> None:
        block = build_impl_usage_block(ai_tool="opencode")
        assert "| Total tokens | *unavailable* |" in block

    def test_unavailable_when_empty_token_usage(self) -> None:
        usage = TokenUsage()
        block = build_impl_usage_block(ai_tool="opencode", token_usage=usage)
        assert "| Total tokens | *unavailable* |" in block

    def test_minimal(self) -> None:
        block = build_impl_usage_block()
        assert IMPL_USAGE_MARKER_START in block
        assert IMPL_USAGE_MARKER_END in block
        assert "## Token Usage (Implementation)" in block
        assert "| Metric | Value |" in block


class TestStripImplUsageBlock:
    def test_strips_block(self) -> None:
        body = (
            "Some content\n\n"
            f"{IMPL_USAGE_MARKER_START}\n"
            "## Token Usage (Implementation)\n"
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
        assert body.find("Part of #10") < body.find("Closes #42")

    def test_no_close(self) -> None:
        task = Task(id="42", title="Add auth")
        body = _build_pr_body(task, close_issue=False)
        assert "Closes" not in body

    def test_with_pr_summary_file(self, tmp_path: Path) -> None:
        pr_summary = tmp_path / "PR-SUMMARY.md"
        pr_summary.write_text("Added login page with OAuth support.\n")

        task = Task(id="42", title="Add auth")
        body = _build_pr_body(task, pr_summary_path=pr_summary)
        assert "Closes #42" in body
        assert "## Summary" in body
        assert "OAuth support" in body

    def test_plan_summary_excluded_from_pr(self) -> None:
        """Plan summary in the issue body must NOT leak into the PR body."""
        from wade.services.task_service import (
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
        assert "## Plan Summary" not in body
        assert "Tokens" not in body
        assert "Closes #42" in body


# ---------------------------------------------------------------------------
# Existing-PR body update idempotency
# ---------------------------------------------------------------------------


class TestExistingPrBodyUpdate:
    """Regression tests for _apply_pr_refs — exercises real production code."""

    def test_closes_ref_not_duplicated_on_retry(self) -> None:
        """Calling _apply_pr_refs twice must not duplicate 'Closes #42'."""
        body = "Closes #42\n\n## Tasks\n- Login\n"
        result = _apply_pr_refs(body, "42", close_issue=True, parent_issue=None)
        assert result.count("Closes #42") == 1

    def test_parent_ref_added_with_no_close(self) -> None:
        """--no-close should still insert 'Part of #parent'."""
        body = "Implements #42\n\n## Tasks\n- Login\n"
        result = _apply_pr_refs(body, "42", close_issue=False, parent_issue="10")
        assert "Part of #10" in result
        assert "Implements #42" in result  # Should not be stripped with --no-close

    def test_parent_ref_not_duplicated_on_retry(self) -> None:
        """Repeated updates must not duplicate 'Part of #10'."""
        body = "Part of #10\nCloses #42\n\n## Tasks\n- Login\n"
        result = _apply_pr_refs(body, "42", close_issue=True, parent_issue="10")
        assert result.count("Part of #10") == 1

    def test_implements_upgraded_to_closes(self) -> None:
        """'Implements #42' should be replaced with 'Closes #42'."""
        body = "Implements #42\n\n## Tasks\n- Login\n"
        result = _apply_pr_refs(body, "42", close_issue=True, parent_issue=None)
        assert "Closes #42" in result
        assert "Implements #42" not in result


# ---------------------------------------------------------------------------
# Sync tests (unit level — mocked git)
# ---------------------------------------------------------------------------


class TestSync:
    def test_not_in_git_repo(self) -> None:
        from wade.services.implementation_service import sync

        with (
            patch(
                "wade.services.implementation_service.load_config",
                return_value=ProjectConfig(),
            ),
            patch(
                "wade.services.implementation_service.git_repo.get_repo_root",
                side_effect=GitError("not a repo"),
            ),
        ):
            result = sync(project_root=Path("/tmp/nonexistent"))
            assert not result.success
            assert any(e.event == "error" for e in result.events)

    def test_on_main_branch(self, tmp_git_repo: Path) -> None:
        from wade.services.implementation_service import sync

        with patch(
            "wade.services.implementation_service.load_config",
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

        from wade.services.implementation_service import sync

        # Create and checkout feature branch
        subprocess.run(
            ["git", "checkout", "-b", "feat/42-test"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        with patch(
            "wade.services.implementation_service.load_config",
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

        from wade.services.implementation_service import sync

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
            "wade.services.implementation_service.load_config",
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

        from wade.services.implementation_service import sync

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
            "wade.services.implementation_service.load_config",
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

        from wade.services.implementation_service import sync

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
            "wade.services.implementation_service.load_config",
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

        from wade.services.implementation_service import sync

        subprocess.run(
            ["git", "checkout", "-b", "feat/42-test"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        with patch(
            "wade.services.implementation_service.load_config",
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
        from wade.services.implementation_service import done

        with (
            patch(
                "wade.services.implementation_service.load_config",
                return_value=ProjectConfig(),
            ),
            patch(
                "wade.services.implementation_service.git_repo.get_repo_root",
                side_effect=GitError("not a repo"),
            ),
        ):
            result = done(project_root=Path("/tmp/nonexistent"))
            assert not result

    def test_no_issue_in_branch(self, tmp_git_repo: Path) -> None:
        """Branch without issue number fails."""
        from wade.services.implementation_service import done

        # We're on 'main' which has no issue number
        with patch(
            "wade.services.implementation_service.load_config",
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
        from wade.git.worktree import create_worktree

        # Create a worktree
        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, "feat/42-test", wt_dir, "main")

        with patch(
            "wade.services.implementation_service.load_config",
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
            "wade.services.implementation_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            sessions = list_sessions(project_root=tmp_git_repo)
            assert len(sessions) == 0

    def test_json_output(self, tmp_git_repo: Path) -> None:
        from wade.git.worktree import create_worktree

        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, "feat/42-test", wt_dir, "main")

        with patch(
            "wade.services.implementation_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            sessions = list_sessions(json_output=True, project_root=tmp_git_repo)
            assert len(sessions) == 1

    def test_show_all_includes_main(self, tmp_git_repo: Path) -> None:
        with patch(
            "wade.services.implementation_service.load_config",
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
        from wade.git.worktree import create_worktree
        from wade.services.implementation_service import remove

        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, "feat/42-test", wt_dir, "main")

        with patch(
            "wade.services.implementation_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = remove(target="42", force=True, project_root=tmp_git_repo)
            assert result
            assert not wt_dir.exists()

    def test_remove_unknown_target(self, tmp_git_repo: Path) -> None:
        from wade.services.implementation_service import remove

        with patch(
            "wade.services.implementation_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = remove(target="999", project_root=tmp_git_repo)
            assert not result

    def test_remove_stale_no_force(self, tmp_git_repo: Path) -> None:
        """Without --force, stale removal just previews."""
        from wade.git.worktree import create_worktree
        from wade.services.implementation_service import remove

        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, "feat/42-test", wt_dir, "main")

        with patch(
            "wade.services.implementation_service.load_config",
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
        from wade.git.worktree import create_worktree
        from wade.services.implementation_service import remove

        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, "feat/42-test", wt_dir, "main")

        with patch(
            "wade.services.implementation_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = remove(stale=True, force=True, project_root=tmp_git_repo)
            assert result
            assert not wt_dir.exists()

    def test_remove_no_args(self, tmp_git_repo: Path) -> None:
        from wade.services.implementation_service import remove

        with patch(
            "wade.services.implementation_service.load_config",
            return_value=ProjectConfig(
                project=ProjectSettings(main_branch="main"),
            ),
        ):
            result = remove(project_root=tmp_git_repo)
            assert not result


# ---------------------------------------------------------------------------
# Summary section stripping and ordering
# ---------------------------------------------------------------------------


class TestStripSummarySection:
    def test_removes_summary_at_end(self) -> None:
        body = "Some content\n\n## Summary\n\nSummary text here.\n"
        result = _strip_summary_section(body)
        assert "## Summary" not in result
        assert "Some content" in result

    def test_preserves_impl_usage_block(self) -> None:
        body = (
            "Some content\n\n"
            "## Summary\n\nOld summary.\n\n"
            f"{IMPL_USAGE_MARKER_START}\nusage data\n{IMPL_USAGE_MARKER_END}\n"
        )
        result = _strip_summary_section(body)
        assert "## Summary" not in result
        assert IMPL_USAGE_MARKER_START in result
        assert "usage data" in result

    def test_no_summary_returns_unchanged(self) -> None:
        body = "Just some content.\n"
        assert _strip_summary_section(body) == body

    def test_adjacent_heading_after_summary_preserved(self) -> None:
        """Heading immediately after ## Summary (no blank line) must not be dropped."""
        body = "Closes #1\n\n## Summary\n## Tasks\n- a\n"
        result = _strip_summary_section(body)
        assert "## Tasks" in result
        assert "- a" in result
        assert "## Summary" not in result

    def test_summary_with_subheadings_fully_removed(self) -> None:
        body = (
            "PR description\n\n"
            "## Summary\n\nIntro.\n\n## Details\n\nMore detail.\n\n"
            f"{IMPL_USAGE_MARKER_START}\ndata\n{IMPL_USAGE_MARKER_END}\n"
        )
        result = _strip_summary_section(body)
        assert "Intro" not in result
        assert "## Details" not in result
        assert "More detail" not in result
        assert IMPL_USAGE_MARKER_START in result


class TestSummaryOrdering:
    """Summary must appear before impl-usage block in the final PR body."""

    def test_summary_inserted_before_impl_usage(self) -> None:
        """When existing body has an impl-usage block, new summary goes before it."""
        existing_body = (
            "Closes #42\n\n"
            "## Tasks\n- Login\n\n"
            f"{IMPL_USAGE_MARKER_START}\nusage\n{IMPL_USAGE_MARKER_END}\n"
        )
        summary_section = "\n\n## Summary\n\nNew summary content."

        # Simulate the logic from _done_via_pr
        updated_body = _strip_summary_section(existing_body)
        marker_pos = updated_body.find(IMPL_USAGE_MARKER_START)
        assert marker_pos != -1, "impl-usage block should be preserved"

        before = updated_body[:marker_pos].rstrip("\n")
        after = updated_body[marker_pos:]
        final = before + summary_section + "\n\n" + after + "\n"

        summary_pos = final.find("## Summary")
        impl_pos = final.find(IMPL_USAGE_MARKER_START)
        assert summary_pos < impl_pos, (
            f"Summary (at {summary_pos}) must come before impl-usage (at {impl_pos})"
        )

    def test_summary_appended_when_no_impl_usage(self) -> None:
        """When no impl-usage block exists, summary goes at the end."""
        existing_body = "Closes #42\n\n## Tasks\n- Login\n"
        summary_section = "\n\n## Summary\n\nNew summary content."

        updated_body = _strip_summary_section(existing_body)
        final = updated_body.rstrip("\n") + summary_section + "\n"

        assert final.endswith("New summary content.\n")

    def test_repeated_done_replaces_summary_idempotently(self) -> None:
        """Running implementation-session done twice replaces the old summary, keeps ordering."""
        body_with_summary = (
            "Closes #42\n\n"
            "## Tasks\n- Login\n\n"
            "## Summary\n\nFirst summary.\n\n"
            f"{IMPL_USAGE_MARKER_START}\nusage\n{IMPL_USAGE_MARKER_END}\n"
        )
        new_summary = "\n\n## Summary\n\nSecond summary."

        stripped = _strip_summary_section(body_with_summary)
        assert "First summary" not in stripped

        marker_pos = stripped.find(IMPL_USAGE_MARKER_START)
        before = stripped[:marker_pos].rstrip("\n")
        after = stripped[marker_pos:]
        final = before + new_summary + "\n\n" + after + "\n"

        assert "Second summary" in final
        assert "First summary" not in final
        assert final.find("## Summary") < final.find(IMPL_USAGE_MARKER_START)
