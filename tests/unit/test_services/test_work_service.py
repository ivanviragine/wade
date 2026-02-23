"""Tests for work service — start, batch, bootstrap, cd."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghaiw.models.config import (
    HooksConfig,
    ProjectConfig,
    ProjectSettings,
)
from ghaiw.models.deps import DependencyEdge, DependencyGraph
from ghaiw.models.task import Complexity, Task
from ghaiw.services.work_service import (
    _build_graph_from_issues,
    _complexity_to_model,
    _resolve_target,
    _resolve_worktrees_dir,
    bootstrap_worktree,
    build_work_prompt,
    find_worktree_path,
    write_issue_context,
)


# ---------------------------------------------------------------------------
# Bootstrap helper tests
# ---------------------------------------------------------------------------


class TestResolveWorktreesDir:
    def test_relative_dir(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            project=ProjectSettings(worktrees_dir="../.worktrees"),
        )
        result = _resolve_worktrees_dir(config, tmp_path)
        assert result == (tmp_path / "../.worktrees").resolve()

    def test_absolute_dir(self) -> None:
        config = ProjectConfig(
            project=ProjectSettings(worktrees_dir="/tmp/wt"),
        )
        result = _resolve_worktrees_dir(config, Path("/some/repo"))
        assert result == Path("/tmp/wt")


class TestComplexityToModel:
    def test_maps_easy(self) -> None:
        from ghaiw.models.config import ComplexityModelMapping

        config = ProjectConfig(
            models={"claude": ComplexityModelMapping(easy="claude-haiku-4-5")}
        )
        result = _complexity_to_model(config, "claude", "easy")
        assert result == "claude-haiku-4-5"

    def test_maps_complex(self) -> None:
        from ghaiw.models.config import ComplexityModelMapping

        config = ProjectConfig(
            models={"claude": ComplexityModelMapping(complex="claude-sonnet-4-6")}
        )
        result = _complexity_to_model(config, "claude", "complex")
        assert result == "claude-sonnet-4-6"

    def test_no_mapping(self) -> None:
        config = ProjectConfig()
        result = _complexity_to_model(config, "claude", "easy")
        assert result is None

    def test_none_complexity(self) -> None:
        config = ProjectConfig()
        result = _complexity_to_model(config, "claude", None)
        assert result is None


class TestWriteIssueContext:
    def test_writes_context_file(self, tmp_path: Path) -> None:
        task = Task(
            id="42",
            title="Add auth",
            body="## Tasks\n- Login page\n",
            url="https://github.com/owner/repo/issues/42",
        )
        path = write_issue_context(tmp_path, task)
        assert path.is_file()
        content = path.read_text()
        assert "# Issue #42: Add auth" in content
        assert "Login page" in content
        assert "https://github.com/owner/repo/issues/42" in content

    def test_writes_minimal_context(self, tmp_path: Path) -> None:
        task = Task(id="1", title="Minimal")
        path = write_issue_context(tmp_path, task)
        assert path.is_file()
        content = path.read_text()
        assert "# Issue #1: Minimal" in content


class TestBootstrapWorktree:
    def test_copies_configured_files(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / ".env").write_text("SECRET=123\n")

        worktree = tmp_path / "wt"
        worktree.mkdir()

        config = ProjectConfig(
            hooks=HooksConfig(copy_to_worktree=[".env"]),
        )
        bootstrap_worktree(worktree, config, repo_root)
        assert (worktree / ".env").is_file()
        assert (worktree / ".env").read_text() == "SECRET=123\n"

    def test_skips_missing_files(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        worktree = tmp_path / "wt"
        worktree.mkdir()

        config = ProjectConfig(
            hooks=HooksConfig(copy_to_worktree=[".env", "nonexistent.txt"]),
        )
        # Should not raise
        bootstrap_worktree(worktree, config, repo_root)


class TestBuildWorkPrompt:
    def test_includes_issue_info(self) -> None:
        task = Task(id="42", title="Add auth")
        prompt = build_work_prompt(task)
        assert "#42" in prompt
        assert "Add auth" in prompt
        assert ".issue-context.md" in prompt


# ---------------------------------------------------------------------------
# Target resolution tests
# ---------------------------------------------------------------------------


class TestResolveTarget:
    def test_resolves_issue_number(self) -> None:
        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test")
        config = ProjectConfig()

        task = _resolve_target("42", provider, config)
        assert task is not None
        assert task.id == "42"
        provider.read_task.assert_called_once_with("42")

    def test_resolves_plan_file(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        plan.write_text("# New Feature\n\n## Tasks\n- Do stuff\n")

        provider = MagicMock()
        provider.create_task.return_value = Task(id="99", title="New Feature")
        config = ProjectConfig(
            project=ProjectSettings(issue_label="feature-plan"),
        )

        task = _resolve_target(str(plan), provider, config)
        assert task is not None
        assert task.id == "99"

    def test_handles_read_failure(self) -> None:
        provider = MagicMock()
        provider.read_task.side_effect = Exception("Not found")
        config = ProjectConfig()

        task = _resolve_target("999", provider, config)
        assert task is None


# ---------------------------------------------------------------------------
# Graph from issues tests
# ---------------------------------------------------------------------------


class TestBuildGraphFromIssues:
    def test_detects_deps_from_body(self) -> None:
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(
                id="1",
                title="Auth",
                body="## Dependencies\n\n**Depends on:** #2\n",
            ),
            Task(
                id="2",
                title="DB",
                body="## Tasks\n- Schema\n",
            ),
        ]

        with patch(
            "ghaiw.services.work_service.get_provider", return_value=provider
        ):
            config = ProjectConfig()
            graph = _build_graph_from_issues(["1", "2"], config)
            assert graph is not None
            assert len(graph.edges) == 1
            assert graph.edges[0].from_task == "2"
            assert graph.edges[0].to_task == "1"

    def test_no_deps(self) -> None:
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="A", body="No deps here"),
            Task(id="2", title="B", body="Also no deps"),
        ]

        with patch(
            "ghaiw.services.work_service.get_provider", return_value=provider
        ):
            config = ProjectConfig()
            graph = _build_graph_from_issues(["1", "2"], config)
            assert graph is None


# ---------------------------------------------------------------------------
# Find worktree tests
# ---------------------------------------------------------------------------


class TestFindWorktreePath:
    def test_finds_by_issue_number(self, tmp_git_repo: Path) -> None:
        # Create a worktree to find
        from ghaiw.git.worktree import create_worktree

        wt_dir = tmp_git_repo.parent / "wt-42"
        create_worktree(tmp_git_repo, "feat/42-test", wt_dir, "main")

        path = find_worktree_path("42", project_root=tmp_git_repo)
        assert path is not None
        assert path.exists()

    def test_returns_none_for_unknown(self, tmp_git_repo: Path) -> None:
        path = find_worktree_path("999", project_root=tmp_git_repo)
        assert path is None
