"""Tests for work service — start, batch, bootstrap, cd."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghaiw.ai_tools.base import AbstractAITool
from ghaiw.ai_tools.claude import ClaudeAdapter
from ghaiw.ai_tools.codex import CodexAdapter
from ghaiw.ai_tools.copilot import CopilotAdapter
from ghaiw.ai_tools.gemini import GeminiAdapter
from ghaiw.git.repo import GitError
from ghaiw.models.config import (
    HooksConfig,
    ProjectConfig,
    ProjectSettings,
)
from ghaiw.models.task import Task
from ghaiw.services.work_service import (
    _build_graph_from_issues,
    _complexity_to_model,
    _resolve_target,
    _resolve_worktrees_dir,
    batch,
    bootstrap_worktree,
    build_work_prompt,
    find_worktree_path,
    start,
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

        config = ProjectConfig(models={"claude": ComplexityModelMapping(easy="claude-haiku-4-5")})
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

    def test_propagates_allowlist_when_configured(self, tmp_path: Path) -> None:
        """Allowlist is copied to worktree when project root has Bash(ghaiw *) configured."""
        import json

        from ghaiw.config.claude_allowlist import GHAIWPY_ALLOW_PATTERN

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        claude_dir = repo_root / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(
            json.dumps({"permissions": {"allow": [GHAIWPY_ALLOW_PATTERN]}}) + "\n",
            encoding="utf-8",
        )

        worktree = tmp_path / "wt"
        worktree.mkdir()

        config = ProjectConfig()
        bootstrap_worktree(worktree, config, repo_root)

        wt_settings = worktree / ".claude" / "settings.json"
        assert wt_settings.is_file()
        data = json.loads(wt_settings.read_text(encoding="utf-8"))
        assert GHAIWPY_ALLOW_PATTERN in data["permissions"]["allow"]

    def test_no_allowlist_propagation_when_not_configured(self, tmp_path: Path) -> None:
        """Allowlist is NOT written to worktree when project root has no settings."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        worktree = tmp_path / "wt"
        worktree.mkdir()

        config = ProjectConfig()
        bootstrap_worktree(worktree, config, repo_root)

        assert not (worktree / ".claude" / "settings.json").is_file()


class TestBuildWorkPrompt:
    def test_includes_issue_info(self) -> None:
        task = Task(id="42", title="Add auth")
        prompt = build_work_prompt(task)
        assert "#42" in prompt
        assert "Add auth" in prompt
        assert "PLAN.md" in prompt


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
        plan = tmp_path / "PLAN.md"
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

        with patch("ghaiw.services.work_service.get_provider", return_value=provider):
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

        with patch("ghaiw.services.work_service.get_provider", return_value=provider):
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

    def test_does_not_match_substring_of_issue_number(self, tmp_git_repo: Path) -> None:
        """target="1" must NOT match a worktree for issue 10."""
        from ghaiw.git.worktree import create_worktree

        wt_dir = tmp_git_repo.parent / "feat-10-something"
        create_worktree(tmp_git_repo, "feat/10-something", wt_dir, "main")

        path = find_worktree_path("1", project_root=tmp_git_repo)
        assert path is None


# ---------------------------------------------------------------------------
# Command assembly tests — verify exact subprocess.run cmd lists
# ---------------------------------------------------------------------------


class TestWorkLaunchCommandAssembly:
    """Verify each adapter builds the correct command for work sessions."""

    def test_claude_launch_with_transcript(self, tmp_path: Path) -> None:
        """Claude launch must NOT include --output-file (flag does not exist in Claude CLI)."""
        adapter = ClaudeAdapter()
        transcript = tmp_path / "transcript.jsonl"

        with (
            patch("ghaiw.utils.process.shutil.which", return_value=None),
            patch("ghaiw.utils.process.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            adapter.launch(
                worktree_path=tmp_path,
                model="claude-sonnet-4-6",
                transcript_path=transcript,
            )
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "claude"
            assert "--model" in cmd
            assert "claude-sonnet-4-6" in cmd
            assert "--output-file" not in cmd
            assert mock_run.call_args[1]["cwd"] == tmp_path

    def test_claude_launch_no_transcript(self, tmp_path: Path) -> None:
        """Claude launch without transcript_path should NOT include --output-file."""
        adapter = ClaudeAdapter()

        with patch("ghaiw.utils.process.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            adapter.launch(
                worktree_path=tmp_path,
                model="claude-haiku-4-5",
            )
            cmd = mock_run.call_args[0][0]
            assert "--output-file" not in cmd

    def test_copilot_launch_no_transcript_support(self, tmp_path: Path) -> None:
        """Copilot launch should NOT include --output-file (no transcript support)."""
        adapter = CopilotAdapter()

        with (
            patch("ghaiw.utils.process.shutil.which", return_value=None),
            patch("ghaiw.utils.process.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            adapter.launch(
                worktree_path=tmp_path,
                model="claude-sonnet-4.6",
                transcript_path=tmp_path / "transcript.jsonl",
            )
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "copilot"
            assert "--model" in cmd
            assert "claude-sonnet-4.6" in cmd
            assert "--output-file" not in cmd
            assert mock_run.call_args[1]["cwd"] == tmp_path

    def test_gemini_launch_command(self, tmp_path: Path) -> None:
        """Gemini launch should use 'gemini' binary with --model."""
        adapter = GeminiAdapter()

        with patch("ghaiw.utils.process.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            adapter.launch(
                worktree_path=tmp_path,
                model="gemini-2.5-pro",
            )
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "gemini"
            assert "--model" in cmd
            assert "gemini-2.5-pro" in cmd
            assert mock_run.call_args[1]["cwd"] == tmp_path

    def test_codex_launch_command(self, tmp_path: Path) -> None:
        """Codex launch should use 'codex' binary with --model."""
        adapter = CodexAdapter()

        with (
            patch("ghaiw.utils.process.shutil.which", return_value=None),
            patch("ghaiw.utils.process.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            adapter.launch(
                worktree_path=tmp_path,
                model="o4-mini",
            )
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "codex"
            assert "--model" in cmd
            assert "o4-mini" in cmd
            assert mock_run.call_args[1]["cwd"] == tmp_path

    def test_no_plan_mode_in_work_session(self, tmp_path: Path) -> None:
        """Work session launches should NOT include plan/approval mode flags."""
        adapters: list[AbstractAITool] = [
            ClaudeAdapter(),
            CopilotAdapter(),
            GeminiAdapter(),
            CodexAdapter(),
        ]
        for adapter in adapters:
            with (
                patch("ghaiw.utils.process.shutil.which", return_value=None),
                patch("ghaiw.utils.process.subprocess.run") as mock_run,
            ):
                mock_run.return_value = MagicMock(returncode=0)
                adapter.launch(
                    worktree_path=tmp_path,
                    model="test-model",
                )
                cmd = mock_run.call_args[0][0]
                tool = adapter.TOOL_ID
                assert "--permission-mode" not in cmd, f"{tool}: leaked --permission-mode"
                assert "--approval-mode" not in cmd, f"{tool}: leaked --approval-mode"


# ---------------------------------------------------------------------------
# Work start tests
# ---------------------------------------------------------------------------


class TestWorkStart:
    """Tests for work_service.start() — exercises the full start() orchestration."""

    def _make_task(self) -> Task:
        return Task(id="42", title="Test task")

    def _make_config(self) -> ProjectConfig:
        """ProjectConfig with main_branch set to avoid detect_main_branch subprocess call."""
        return ProjectConfig(project=ProjectSettings(main_branch="main"))

    def test_creates_worktree(self, tmp_path: Path) -> None:
        """Happy path: no existing worktree, no draft PR → create_worktree called, returns True."""
        task = self._make_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch("ghaiw.services.work_service.load_config", return_value=self._make_config()),
            patch("ghaiw.services.work_service.get_provider", return_value=mock_provider),
            patch("ghaiw.git.repo.get_repo_root", return_value=tmp_path),
            patch("ghaiw.git.worktree.list_worktrees", return_value=[]),
            patch("ghaiw.git.worktree.create_worktree") as mock_create,
            patch("ghaiw.services.work_service.write_plan_md"),
            patch("ghaiw.services.work_service.bootstrap_worktree"),
            patch("ghaiw.ai_tools.base.AbstractAITool.detect_installed", return_value=[]),
            patch("ghaiw.services.work_service._is_inside_ai_cli", return_value=False),
            patch("ghaiw.git.pr.get_pr_for_branch", return_value=None),
            patch(
                "ghaiw.services.work_service.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ),
            patch("ghaiw.services.work_service.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)
            assert result is True
            mock_create.assert_called_once()

    def test_reuses_existing_worktree(self, tmp_path: Path) -> None:
        """Idempotency: list_worktrees returns matching branch → create_worktree NOT called."""
        from ghaiw.git.branch import make_branch_name

        task = self._make_task()
        branch_name = make_branch_name("feat", int(task.id), task.title)
        existing_wt = tmp_path / "existing-wt"
        existing_wt.mkdir()

        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch("ghaiw.services.work_service.load_config", return_value=self._make_config()),
            patch("ghaiw.services.work_service.get_provider", return_value=mock_provider),
            patch("ghaiw.git.repo.get_repo_root", return_value=tmp_path),
            patch(
                "ghaiw.git.worktree.list_worktrees",
                return_value=[{"path": str(existing_wt), "branch": branch_name}],
            ),
            patch("ghaiw.git.worktree.create_worktree") as mock_create,
            patch("ghaiw.services.work_service.write_plan_md"),
            patch("ghaiw.services.work_service.bootstrap_worktree"),
            patch("ghaiw.ai_tools.base.AbstractAITool.detect_installed", return_value=[]),
            patch("ghaiw.services.work_service._is_inside_ai_cli", return_value=False),
            patch("ghaiw.git.pr.get_pr_for_branch", return_value=None),
            patch(
                "ghaiw.services.work_service.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ),
            patch("ghaiw.services.work_service.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)
            assert result is True
            mock_create.assert_not_called()

    def test_returns_false_on_creation_failure(self, tmp_path: Path) -> None:
        """create_worktree raises GitError → start() returns False."""
        task = self._make_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch("ghaiw.services.work_service.load_config", return_value=self._make_config()),
            patch("ghaiw.services.work_service.get_provider", return_value=mock_provider),
            patch("ghaiw.git.repo.get_repo_root", return_value=tmp_path),
            patch("ghaiw.git.worktree.list_worktrees", return_value=[]),
            patch(
                "ghaiw.git.worktree.create_worktree",
                side_effect=GitError("Branch already exists"),
            ),
            patch("ghaiw.git.pr.get_pr_for_branch", return_value=None),
            patch(
                "ghaiw.services.work_service.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ),
            patch("ghaiw.services.work_service.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)

        assert result is False

    def test_cd_only_prints_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cd_only=True → worktree path printed to stdout, no AI launched, returns True."""
        task = self._make_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch("ghaiw.services.work_service.load_config", return_value=self._make_config()),
            patch("ghaiw.services.work_service.get_provider", return_value=mock_provider),
            patch("ghaiw.git.repo.get_repo_root", return_value=tmp_path),
            patch("ghaiw.git.worktree.list_worktrees", return_value=[]),
            patch("ghaiw.git.worktree.create_worktree"),
            patch("ghaiw.services.work_service.write_plan_md"),
            patch("ghaiw.services.work_service.bootstrap_worktree"),
            patch("ghaiw.services.work_service._is_inside_ai_cli", return_value=False),
            patch("ghaiw.ai_tools.base.AbstractAITool.get") as mock_get,
            patch("ghaiw.git.pr.get_pr_for_branch", return_value=None),
            patch(
                "ghaiw.services.work_service.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ),
            patch("ghaiw.services.work_service.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path, cd_only=True)
            assert result is True
            mock_get.assert_not_called()

        captured = capsys.readouterr()
        assert "42" in captured.out  # Worktree path containing issue ID was printed

    def test_inside_ai_cli_skips_launch(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """_is_inside_ai_cli()=True → AI tool .get()/.launch() not called, path printed."""
        task = self._make_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch("ghaiw.services.work_service.load_config", return_value=self._make_config()),
            patch("ghaiw.services.work_service.get_provider", return_value=mock_provider),
            patch("ghaiw.git.repo.get_repo_root", return_value=tmp_path),
            patch("ghaiw.git.worktree.list_worktrees", return_value=[]),
            patch("ghaiw.git.worktree.create_worktree"),
            patch("ghaiw.services.work_service.write_plan_md"),
            patch("ghaiw.services.work_service.bootstrap_worktree"),
            patch("ghaiw.services.work_service._is_inside_ai_cli", return_value=True),
            patch("ghaiw.ai_tools.base.AbstractAITool.get") as mock_get,
            patch("ghaiw.git.pr.get_pr_for_branch", return_value=None),
            patch(
                "ghaiw.services.work_service.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ),
            patch("ghaiw.services.work_service.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)
            assert result is True
            mock_get.assert_not_called()

        captured = capsys.readouterr()
        assert "42" in captured.out  # Worktree path containing issue ID was printed


# ---------------------------------------------------------------------------
# Work batch tests
# ---------------------------------------------------------------------------


class TestWorkBatch:
    """Tests for work_service.batch() — exercises topology and launch dispatch."""

    def test_launches_independent_issues(self, tmp_path: Path) -> None:
        """No deps graph → all issues launched in separate terminals."""
        with (
            patch("ghaiw.services.work_service.load_config", return_value=ProjectConfig()),
            patch("ghaiw.git.repo.get_repo_root", return_value=tmp_path),
            patch("ghaiw.services.work_service._build_graph_from_issues", return_value=None),
            patch(
                "ghaiw.services.work_service.launch_in_new_terminal", return_value=True
            ) as mock_launch,
        ):
            result = batch(["1", "2", "3"], project_root=tmp_path)

        assert result is True
        assert mock_launch.call_count == 3

    def test_launches_only_first_in_chain(self, tmp_path: Path) -> None:
        """Dependency chain → only the first issue launched, rest printed."""
        mock_graph = MagicMock()
        mock_graph.edges = [MagicMock()]  # non-empty → triggers partition
        mock_graph.partition.return_value = ([], [["1", "2", "3"]])

        with (
            patch("ghaiw.services.work_service.load_config", return_value=ProjectConfig()),
            patch("ghaiw.git.repo.get_repo_root", return_value=tmp_path),
            patch(
                "ghaiw.services.work_service._build_graph_from_issues",
                return_value=mock_graph,
            ),
            patch(
                "ghaiw.services.work_service.launch_in_new_terminal", return_value=True
            ) as mock_launch,
        ):
            result = batch(["1", "2", "3"], project_root=tmp_path)

        assert result is True
        assert mock_launch.call_count == 1  # Only the first in the chain
        launched_cmd = mock_launch.call_args[0][0]
        assert launched_cmd[:3] == ["ghaiw", "implement-task", "1"]

    def test_warns_on_terminal_failure(self, tmp_path: Path) -> None:
        """One terminal fails → warns but continues and counts successful launches."""
        with (
            patch("ghaiw.services.work_service.load_config", return_value=ProjectConfig()),
            patch("ghaiw.git.repo.get_repo_root", return_value=tmp_path),
            patch("ghaiw.services.work_service._build_graph_from_issues", return_value=None),
            patch(
                "ghaiw.services.work_service.launch_in_new_terminal",
                side_effect=[False, True],
            ) as mock_launch,
        ):
            result = batch(["1", "2"], project_root=tmp_path)

        assert result is True  # One succeeded
        assert mock_launch.call_count == 2  # Both attempted (no abort on failure)

    def test_returns_false_when_none_launched(self, tmp_path: Path) -> None:
        """All launch_in_new_terminal calls fail → batch() returns False."""
        with (
            patch("ghaiw.services.work_service.load_config", return_value=ProjectConfig()),
            patch("ghaiw.git.repo.get_repo_root", return_value=tmp_path),
            patch("ghaiw.services.work_service._build_graph_from_issues", return_value=None),
            patch("ghaiw.services.work_service.launch_in_new_terminal", return_value=False),
        ):
            result = batch(["1", "2"], project_root=tmp_path)

        assert result is False
