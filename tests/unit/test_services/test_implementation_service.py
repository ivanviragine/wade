"""Tests for implementation service — start, batch, bootstrap, cd."""

from __future__ import annotations

import contextlib
import json
import shlex
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from crossby.ai_tools import AbstractAITool
from crossby.ai_tools.antigravity import AntigravityAdapter
from crossby.ai_tools.antigravity_cli import AntigravityCLIAdapter
from crossby.ai_tools.claude import ClaudeAdapter
from crossby.ai_tools.codex import CodexAdapter
from crossby.ai_tools.copilot import CopilotAdapter
from crossby.models.ai import ModelBreakdown, TokenUsage

from wade.git.pr import PRLookup, PRRef, PRSummary
from wade.git.repo import GitError
from wade.models.config import (
    HooksConfig,
    KnowledgeConfig,
    ProjectConfig,
    ProjectSettings,
)
from wade.models.session import MergeStatus
from wade.models.task import Task
from wade.models.worktree import Worktree
from wade.services.implementation_service import (
    _BATCH_STATUS_DONE,
    _BATCH_STATUS_IN_PROGRESS,
    _BATCH_STATUS_MERGED,
    _BATCH_STATUS_NOT_STARTED,
    _BATCH_STATUS_UNKNOWN,
    MAX_RESOLVE_ATTEMPTS,
    ImplementResult,
    _build_graph_from_issues,
    _build_implementation_issue_context_header,
    _build_pr_index,
    _capture_post_session_usage,
    _classify_issue_status,
    _effective_copy_files,
    _find_tracking_issue,
    _get_remote_branches,
    _parse_overwrite_paths,
    _post_implementation_lifecycle_pr,
    _pull_main_after_merge,
    _query_branches,
    _resolve_task_target,
    _resolve_worktrees_dir,
    batch,
    bootstrap_worktree,
    build_implementation_prompt,
    find_worktree_path,
    poll_batch_completion,
    start,
)
from wade.services.implementation_service.core import _detect_ai_cli_env

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


class TestEffectiveCopyFiles:
    def test_always_includes_wade_yml(self) -> None:
        config = ProjectConfig(hooks=HooksConfig(copy_to_worktree=[".env"]))
        files = _effective_copy_files(config)
        assert ".wade.yml" in files
        assert ".env" in files

    def test_no_duplicate_wade_yml(self) -> None:
        config = ProjectConfig(hooks=HooksConfig(copy_to_worktree=[".wade.yml", ".env"]))
        files = _effective_copy_files(config)
        assert files.count(".wade.yml") == 1

    def test_excludes_knowledge_path_when_enabled(self) -> None:
        # #358: knowledge file + ratings are NEVER copied (they are tracked; copying
        # manufactures a stale snapshot). Only .wade.yml is internal.
        config = ProjectConfig(
            hooks=HooksConfig(copy_to_worktree=[".env"]),
            knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"),
        )
        files = _effective_copy_files(config)
        assert "KNOWLEDGE.md" not in files
        assert "KNOWLEDGE.ratings.jsonl" not in files
        assert "KNOWLEDGE.ratings.yml" not in files
        assert ".wade.yml" in files
        assert ".env" in files

    def test_strips_lingering_knowledge_entries_from_copy_list(self) -> None:
        # A pre-#358 config may still list the knowledge files; they must be filtered.
        config = ProjectConfig(
            hooks=HooksConfig(
                copy_to_worktree=[
                    ".env",
                    "docs/LEARNINGS.md",
                    "docs/LEARNINGS.ratings.yml",
                    "docs/LEARNINGS.ratings.jsonl",
                ]
            ),
            knowledge=KnowledgeConfig(enabled=True, path="docs/LEARNINGS.md"),
        )
        files = _effective_copy_files(config)
        assert files == [".env", ".wade.yml"]

    def test_excludes_knowledge_path_when_disabled(self) -> None:
        config = ProjectConfig(
            hooks=HooksConfig(copy_to_worktree=[".env"]),
            knowledge=KnowledgeConfig(enabled=False),
        )
        files = _effective_copy_files(config)
        assert "KNOWLEDGE.md" not in files

    def test_rejects_absolute_knowledge_path(self) -> None:
        config = ProjectConfig(
            knowledge=KnowledgeConfig(enabled=True, path="/etc/secrets"),
        )
        files = _effective_copy_files(config)
        assert "/etc/secrets" not in files

    def test_rejects_escaping_knowledge_path(self) -> None:
        config = ProjectConfig(
            knowledge=KnowledgeConfig(enabled=True, path="../outside.md"),
        )
        files = _effective_copy_files(config)
        assert "../outside.md" not in files

    def test_folds_contained_dotdot_in_knowledge_path(self) -> None:
        # #358 review: a contained ``..`` (``docs/../KNOWLEDGE.md``) must canonicalize to
        # ``KNOWLEDGE.md`` so a plainly-spelled copy entry is still excluded — otherwise the
        # stale-snapshot copy this lifecycle removes could sneak back in.
        config = ProjectConfig(
            hooks=HooksConfig(copy_to_worktree=[".env", "KNOWLEDGE.md", "KNOWLEDGE.ratings.jsonl"]),
            knowledge=KnowledgeConfig(enabled=True, path="docs/../KNOWLEDGE.md"),
        )
        files = _effective_copy_files(config)
        assert files == [".env", ".wade.yml"]

    def test_empty_user_config(self) -> None:
        config = ProjectConfig()
        files = _effective_copy_files(config)
        assert ".wade.yml" in files


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

    def test_does_not_copy_knowledge_or_ratings_when_enabled(self, tmp_path: Path) -> None:
        # #358: the knowledge file + ratings are tracked, so the worktree checkout
        # already has them — bootstrap must NOT copy main's copy over them.
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        knowledge_dir = repo_root / "docs"
        knowledge_dir.mkdir()
        (knowledge_dir / "LEARNINGS.md").write_text("# Knowledge\n", encoding="utf-8")
        (knowledge_dir / "LEARNINGS.ratings.jsonl").write_text(
            '{"dir": "up", "id": "a1b2c3d4", "ts": "t"}\n',
            encoding="utf-8",
        )

        worktree = tmp_path / "wt"
        worktree.mkdir()

        config = ProjectConfig(
            knowledge=KnowledgeConfig(enabled=True, path="docs/LEARNINGS.md"),
        )
        bootstrap_worktree(worktree, config, repo_root)

        # Neither the knowledge file nor its ratings sidecar are copied into the worktree.
        assert not (worktree / "docs" / "LEARNINGS.md").exists()
        assert not (worktree / "docs" / "LEARNINGS.ratings.jsonl").exists()

    def test_propagates_allowlist_when_configured(self, tmp_path: Path) -> None:
        """Allowlist is written to worktree using wade's default Bash(wade *) pattern.

        With an empty ProjectConfig(), PermissionsConfig.allowed_commands defaults
        to ["wade *"] which canonical_to_claude translates to "Bash(wade *)".
        """
        import json

        wade_allow_pattern = "Bash(wade *)"

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        claude_dir = repo_root / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(
            json.dumps({"permissions": {"allow": [wade_allow_pattern]}}) + "\n",
            encoding="utf-8",
        )

        worktree = tmp_path / "wt"
        worktree.mkdir()

        config = ProjectConfig()
        bootstrap_worktree(worktree, config, repo_root)

        wt_settings = worktree / ".claude" / "settings.json"
        assert wt_settings.is_file()
        data = json.loads(wt_settings.read_text(encoding="utf-8"))
        assert wade_allow_pattern in data["permissions"]["allow"]

    def test_allowlist_always_propagated_even_without_repo_root_settings(
        self, tmp_path: Path
    ) -> None:
        """Allowlist is always written to worktree regardless of repo root state."""
        wade_allow_pattern = "Bash(wade *)"

        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        worktree = tmp_path / "wt"
        worktree.mkdir()

        config = ProjectConfig()
        bootstrap_worktree(worktree, config, repo_root)

        wt_settings = worktree / ".claude" / "settings.json"
        assert wt_settings.is_file()
        data = json.loads(wt_settings.read_text(encoding="utf-8"))
        assert wade_allow_pattern in data["permissions"]["allow"]

    def test_self_init_creates_symlinks(self, tmp_path: Path) -> None:
        """When repo_root is the wade package root, skills are symlinked from worktree templates."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        worktree = tmp_path / "wt"
        worktree.mkdir()

        # Create templates in the worktree (mimics a wade repo worktree checkout)
        skills_tpl = worktree / "templates" / "skills"
        for skill_name in ("task", "plan-session", "implementation-session", "deps"):
            (skills_tpl / skill_name).mkdir(parents=True, exist_ok=True)
            (skills_tpl / skill_name / "SKILL.md").write_text(f"# {skill_name}\n")

        config = ProjectConfig()
        with patch("wade.skills.installer.get_wade_repo_root", return_value=repo_root):
            bootstrap_worktree(worktree, config, repo_root)

        # Skills should be symlinks, not copies
        task_skill = worktree / ".claude" / "skills" / "task"
        assert task_skill.is_symlink()
        assert (task_skill / "SKILL.md").read_text() == "# task\n"

    def test_non_self_init_creates_copies(self, tmp_path: Path) -> None:
        """When repo_root is NOT the wade package root, skills are copied (not symlinked)."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        worktree = tmp_path / "wt"
        worktree.mkdir()

        config = ProjectConfig()
        # get_wade_repo_root returns a different path — not self-init
        with patch(
            "wade.skills.installer.get_wade_repo_root",
            return_value=tmp_path / "some-other-path",
        ):
            bootstrap_worktree(worktree, config, repo_root)

        # Skills should be regular files, not symlinks
        task_skill = worktree / ".claude" / "skills" / "task"
        assert not task_skill.is_symlink()

    def test_selective_skills_only_installs_listed(self, tmp_path: Path) -> None:
        """bootstrap_worktree with skills parameter installs only those skills."""
        from wade.skills.installer import IMPLEMENT_SKILLS

        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        worktree = tmp_path / "wt"
        worktree.mkdir()

        config = ProjectConfig()
        with patch(
            "wade.skills.installer.get_wade_repo_root",
            return_value=tmp_path / "some-other-path",
        ):
            bootstrap_worktree(worktree, config, repo_root, skills=IMPLEMENT_SKILLS)

        skills_dir = worktree / ".claude" / "skills"
        # IMPLEMENT_SKILLS = ["implementation-session", "task", "knowledge"]
        assert (skills_dir / "implementation-session").is_dir()
        assert (skills_dir / "task").is_dir()
        assert (skills_dir / "knowledge").is_dir()
        # Other skills should NOT be installed
        assert not (skills_dir / "plan-session").exists()
        assert not (skills_dir / "deps").exists()
        assert not (skills_dir / "review-pr-comments-session").exists()

    def test_self_init_selective_skills(self, tmp_path: Path) -> None:
        """Self-init with skills parameter only symlinks listed skills."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()

        worktree = tmp_path / "wt"
        worktree.mkdir()

        # Create templates in the worktree
        skills_tpl = worktree / "templates" / "skills"
        for skill_name in ("task", "plan-session", "implementation-session", "deps"):
            (skills_tpl / skill_name).mkdir(parents=True, exist_ok=True)
            (skills_tpl / skill_name / "SKILL.md").write_text(f"# {skill_name}\n")

        config = ProjectConfig()
        with patch("wade.skills.installer.get_wade_repo_root", return_value=repo_root):
            bootstrap_worktree(worktree, config, repo_root, skills=["task", "deps"])

        skills_dir = worktree / ".claude" / "skills"
        assert (skills_dir / "task").is_symlink()
        assert (skills_dir / "deps").is_symlink()
        assert not (skills_dir / "implementation-session").exists()
        assert not (skills_dir / "plan-session").exists()


class TestBuildImplementationPrompt:
    def test_includes_issue_info(self) -> None:
        task = Task(id="42", title="Add auth")
        prompt = build_implementation_prompt(task)
        assert "#42" in prompt
        assert "Add auth" in prompt
        assert "PLAN.md" in prompt

    def test_includes_body_when_no_plan(self) -> None:
        task = Task(id="42", title="Add auth", body="Implement OAuth2 login flow.")
        prompt = build_implementation_prompt(task, has_plan=False)
        assert "Implement OAuth2 login flow." in prompt
        assert "## Issue Description" in prompt

    def test_omits_body_when_plan_exists(self) -> None:
        task = Task(id="42", title="Add auth", body="Implement OAuth2 login flow.")
        prompt = build_implementation_prompt(task, has_plan=True)
        assert "## Issue Description" not in prompt
        assert "Implement OAuth2 login flow." not in prompt

    def test_no_body_section_when_body_empty(self) -> None:
        task = Task(id="42", title="Add auth", body="")
        prompt = build_implementation_prompt(task, has_plan=False)
        assert "## Issue Description" not in prompt
        # Template content still present
        assert "#42" in prompt
        assert "Add auth" in prompt


class TestBuildImplementationIssueContextHeader:
    def test_contains_body_not_title(self) -> None:
        task = Task(id="7", title="Fix bug", body="Something is broken.")
        header = _build_implementation_issue_context_header(task)
        # Title is already in the template — header only adds the description
        assert "# Issue #7" not in header
        assert "Something is broken." in header
        assert "## Issue Description" in header

    def test_ends_with_separator(self) -> None:
        task = Task(id="1", title="T", body="Body text.")
        header = _build_implementation_issue_context_header(task)
        assert "---" in header


# ---------------------------------------------------------------------------
# Target resolution tests
# ---------------------------------------------------------------------------


class TestResolveTarget:
    def test_resolves_issue_number(self) -> None:
        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test")
        config = ProjectConfig()

        task = _resolve_task_target("42", provider, config)
        assert task is not None
        assert task.id == "42"
        provider.read_task.assert_called_once_with("42")

    def test_resolves_plan_file(self, tmp_path: Path) -> None:
        plan = tmp_path / "PLAN.md"
        plan.write_text("# feat: new feature\n\n## Tasks\n- Do stuff\n")

        provider = MagicMock()
        provider.create_task.return_value = Task(id="99", title="feat: new feature")
        config = ProjectConfig(
            project=ProjectSettings(issue_label="feature-plan"),
        )

        task = _resolve_task_target(str(plan), provider, config)
        assert task is not None
        assert task.id == "99"

    def test_handles_read_failure(self) -> None:
        provider = MagicMock()
        provider.read_task.side_effect = Exception("Not found")
        config = ProjectConfig()

        task = _resolve_task_target("999", provider, config)
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
            "wade.services.implementation_service.batch.get_provider", return_value=provider
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
            "wade.services.implementation_service.batch.get_provider", return_value=provider
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
        from wade.git.worktree import create_worktree

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
        from wade.git.worktree import create_worktree

        wt_dir = tmp_git_repo.parent / "feat-10-something"
        create_worktree(tmp_git_repo, "feat/10-something", wt_dir, "main")

        path = find_worktree_path("1", project_root=tmp_git_repo)
        assert path is None


# ---------------------------------------------------------------------------
# Command assembly tests — verify exact subprocess.run cmd lists
# ---------------------------------------------------------------------------


class TestImplementationLaunchCommandAssembly:
    """Verify each adapter builds the correct command for work sessions."""

    def test_claude_launch_with_transcript(self, tmp_path: Path) -> None:
        """Claude launch must NOT include --output-file (flag does not exist in Claude CLI)."""
        adapter = ClaudeAdapter()
        transcript = tmp_path / "transcript.jsonl"

        with (
            patch("wade.utils.process.shutil.which", return_value=None),
            patch("wade.utils.process.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            adapter.launch(
                working_dir=tmp_path,
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

        with patch("wade.utils.process.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            adapter.launch(
                working_dir=tmp_path,
                model="claude-haiku-4-5",
            )
            cmd = mock_run.call_args[0][0]
            assert "--output-file" not in cmd

    def test_copilot_launch_no_transcript_support(self, tmp_path: Path) -> None:
        """Copilot launch should NOT include --output-file (no transcript support)."""
        adapter = CopilotAdapter()

        with (
            patch("wade.utils.process.shutil.which", return_value=None),
            patch("wade.utils.process.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            adapter.launch(
                working_dir=tmp_path,
                model="claude-sonnet-4.6",
                transcript_path=tmp_path / "transcript.jsonl",
            )
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "copilot"
            assert "--model" in cmd
            assert "claude-sonnet-4.6" in cmd
            assert "--output-file" not in cmd
            assert mock_run.call_args[1]["cwd"] == tmp_path

    def test_antigravity_cli_launch_command(self, tmp_path: Path) -> None:
        """Antigravity CLI launch should use the 'agy' binary with --model."""
        adapter = AntigravityCLIAdapter()

        with patch("wade.utils.process.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            adapter.launch(
                working_dir=tmp_path,
                model="gemini-3-pro",
            )
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "agy"
            assert "--model" in cmd
            assert "gemini-3-pro" in cmd
            assert mock_run.call_args[1]["cwd"] == tmp_path

    def test_antigravity_ide_launch_command(self, tmp_path: Path) -> None:
        """Antigravity IDE (GUI) launch opens the workspace via `antigravity <workdir>`.

        crossby 0.10.2 changed the launch from `antigravity .` to an explicit
        working-dir argument so the target is unambiguous. Guard the exact
        command shape so a regression to `.` (or a leaked model flag) is caught.
        """
        adapter = AntigravityAdapter()

        with patch("crossby.ai_tools.antigravity.run_with_transcript", return_value=0) as mock_rwt:
            adapter.launch(working_dir=tmp_path)
            cmd = mock_rwt.call_args[0][0]
            # Exact shape: guards against a regression to `antigravity .` and a
            # leaked --model flag in one assertion.
            assert cmd == ["antigravity", str(tmp_path)]

    def test_codex_launch_command(self, tmp_path: Path) -> None:
        """Codex launch should use 'codex' binary with --model."""
        adapter = CodexAdapter()

        with (
            patch("wade.utils.process.shutil.which", return_value=None),
            patch("wade.utils.process.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            adapter.launch(
                working_dir=tmp_path,
                model="o4-mini",
            )
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "codex"
            assert "--model" in cmd
            assert "o4-mini" in cmd
            assert mock_run.call_args[1]["cwd"] == tmp_path

    def test_no_plan_mode_in_implementation_session(self, tmp_path: Path) -> None:
        """Work session launches should NOT include plan/approval mode flags."""
        adapters: list[AbstractAITool] = [
            ClaudeAdapter(),
            CopilotAdapter(),
            AntigravityCLIAdapter(),
            CodexAdapter(),
        ]
        for adapter in adapters:
            with (
                patch("wade.utils.process.shutil.which", return_value=None),
                patch("wade.utils.process.subprocess.run") as mock_run,
            ):
                mock_run.return_value = MagicMock(returncode=0)
                adapter.launch(
                    working_dir=tmp_path,
                    model="test-model",
                )
                cmd = mock_run.call_args[0][0]
                tool = adapter.TOOL_ID
                assert "--permission-mode" not in cmd, f"{tool}: leaked --permission-mode"
                assert "--approval-mode" not in cmd, f"{tool}: leaked --approval-mode"


# ---------------------------------------------------------------------------
# Nested AI CLI session detection
# ---------------------------------------------------------------------------


class TestDetectAiCliEnv:
    """Tests for _detect_ai_cli_env() — the nested-session guard.

    Each test clears every marker var first so leftover env state from other
    tests (or the host running the suite) can't leak a false positive.
    """

    _MARKER_VARS = (
        "CLAUDE_CODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "COPILOT_CLI",
        "CODEX_CLI",
        "CURSOR_CLI",
        "ANTIGRAVITY_AGENT",
    )

    def _clear_markers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in self._MARKER_VARS:
            monkeypatch.delenv(var, raising=False)

    def test_antigravity_agent_marker_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ANTIGRAVITY_AGENT (agy's in-session marker) is recognized as a nested session."""
        self._clear_markers(monkeypatch)
        monkeypatch.setenv("ANTIGRAVITY_AGENT", "1")

        assert _detect_ai_cli_env() == "ANTIGRAVITY_AGENT"

    def test_no_markers_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no known marker vars set, detection returns None."""
        self._clear_markers(monkeypatch)

        assert _detect_ai_cli_env() is None


# ---------------------------------------------------------------------------
# Implementation start tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _default_no_open_pr_for_issue() -> object:
    """Default the issue→open-PR resolver to 'none' so any start() test in this
    module skips the not-open-PR gate's ``gh pr list`` (which now raises on
    failure). Tests that exercise resume-by-open-PR override this with their own
    patch. Module-scoped so it also covers start() tests outside
    ``TestImplementationStart`` (e.g. tracking-detection).
    """
    with patch(
        "wade.services.implementation_service.core.find_open_pr_branch_for_issue",
        return_value=None,
    ):
        yield


class TestImplementationStart:
    """Tests for implementation_service.start() — exercises the full start() orchestration."""

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
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.git.worktree.list_worktrees", return_value=[]),
            patch("wade.git.worktree.create_worktree") as mock_create,
            patch("wade.services.implementation_service.core.write_plan_md"),
            patch("wade.services.implementation_service.core.bootstrap_worktree"),
            patch("crossby.ai_tools.base.AbstractAITool.detect_installed", return_value=[]),
            patch(
                "wade.services.implementation_service.core._detect_ai_cli_env", return_value=None
            ),
            patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup(found=False)),
            patch(
                "wade.services.implementation_service.core.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ),
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)
            assert result.success is True
            mock_create.assert_called_once()

    def test_reuses_existing_worktree(self, tmp_path: Path) -> None:
        """Idempotency: list_worktrees returns matching branch → create_worktree NOT called."""
        task = self._make_task()
        branch_name = "feat/42-test-task"
        existing_wt = tmp_path / "existing-wt"
        existing_wt.mkdir()

        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch(
                "wade.git.worktree.list_worktrees",
                return_value=[Worktree(path=str(existing_wt), branch=branch_name)],
            ),
            patch("wade.git.worktree.create_worktree") as mock_create,
            patch("wade.services.implementation_service.core.write_plan_md"),
            patch("wade.services.implementation_service.core.bootstrap_worktree"),
            patch("crossby.ai_tools.base.AbstractAITool.detect_installed", return_value=[]),
            patch(
                "wade.services.implementation_service.core._detect_ai_cli_env", return_value=None
            ),
            patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup(found=False)),
            patch(
                "wade.services.implementation_service.core.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ),
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)
            assert result.success is True
            mock_create.assert_not_called()

    def test_closed_pr_branch_falls_back_to_title_branch(self, tmp_path: Path) -> None:
        """A retitled issue whose stale branch has a CLOSED PR must start fresh.

        Resolution matches the stale branch by issue number, but its PR is closed
        (not resumable). If start() kept that branch, the session's worktree would
        sit on it while bootstrap_draft_pr opens the new draft PR on a different
        title-based branch. Verify start() falls back to the reconstructed name so
        the worktree and the bootstrapped PR agree (#428 review).
        """
        task = Task(id="42", title="renamed title")
        stale_branch = "feat/42-original-slug"
        reconstructed = "feat/42-renamed-title"

        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        def pr_by_branch(_repo_root: Path, branch: str) -> PRLookup:
            if branch == stale_branch:
                return PRLookup(found=True, pr=PRRef(number=7, state="CLOSED"))
            return PRLookup(found=False)

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch(
                "wade.services.implementation_service._shared.git_repo.get_current_branch",
                return_value="main",
            ),
            patch(
                "wade.git.worktree.list_worktrees",
                return_value=[Worktree(path=str(tmp_path / "wt"), branch=stale_branch)],
            ),
            patch("wade.git.branch.branch_exists", return_value=False),
            patch("wade.git.worktree.create_worktree") as mock_create,
            patch("wade.services.implementation_service.core.write_plan_md"),
            patch("wade.services.implementation_service.core.bootstrap_worktree"),
            patch("crossby.ai_tools.base.AbstractAITool.detect_installed", return_value=[]),
            patch(
                "wade.services.implementation_service.core._detect_ai_cli_env", return_value=None
            ),
            patch("wade.git.pr.get_pr_for_branch", side_effect=pr_by_branch),
            # No live open PR for the issue (autouse _default_no_open_pr) → gate
            # falls back to the title branch.
            patch(
                "wade.services.implementation_service.core.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ) as mock_bootstrap,
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)

        assert result.success is True
        # Started fresh on the title-based branch, never the closed PR's stale one.
        mock_bootstrap.assert_called_once()
        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["branch_name"] == reconstructed

    def test_open_pr_on_other_branch_is_resumed_not_rebootstrapped(self, tmp_path: Path) -> None:
        """Ambiguity is settled by PR state, not branch-name ordering (#428 review).

        The issue has a stale branch (closed PR) that resolution adopts by number,
        AND a live open PR on a different branch. start() must resume the open PR's
        branch rather than bootstrap a third PR on the reconstructed title branch.
        """
        task = Task(id="42", title="renamed title")
        stale_branch = "feat/42-original-slug"
        live_branch = "feat/42-open-pr-branch"

        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        def pr_by_branch(_repo_root: Path, branch: str) -> PRLookup:
            if branch == live_branch:
                return PRLookup(found=True, pr=PRRef(number=9, state="OPEN"))
            if branch == stale_branch:
                return PRLookup(found=True, pr=PRRef(number=7, state="CLOSED"))
            return PRLookup(found=False)

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch(
                "wade.services.implementation_service._shared.git_repo.get_current_branch",
                return_value="main",
            ),
            patch(
                "wade.git.worktree.list_worktrees",
                return_value=[Worktree(path=str(tmp_path / "wt"), branch=stale_branch)],
            ),
            patch("wade.git.branch.branch_exists", return_value=True),
            patch("wade.services.implementation_service.core.git_repo.fetch_ref"),
            patch("wade.git.worktree.checkout_existing_branch_worktree"),
            patch("wade.git.worktree.create_worktree") as mock_create,
            patch("wade.services.implementation_service.core.write_plan_md"),
            patch("wade.services.implementation_service.core.bootstrap_worktree"),
            patch("crossby.ai_tools.base.AbstractAITool.detect_installed", return_value=[]),
            patch(
                "wade.services.implementation_service.core._detect_ai_cli_env", return_value=None
            ),
            patch("wade.git.pr.get_pr_for_branch", side_effect=pr_by_branch),
            patch("wade.git.pr.get_pr_body", return_value=None),
            # A live open PR exists for the issue on a different branch.
            patch(
                "wade.services.implementation_service.core.find_open_pr_branch_for_issue",
                return_value=live_branch,
            ),
            patch(
                "wade.services.implementation_service.core.bootstrap_draft_pr",
            ) as mock_bootstrap,
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)

        assert result.success is True
        # Resumed the open PR's branch; no fresh branch created, no third PR bootstrapped.
        mock_bootstrap.assert_not_called()
        mock_create.assert_not_called()

    def test_aborts_when_open_pr_listing_fails(self, tmp_path: Path) -> None:
        """A `gh pr list` failure in the not-open gate must abort, not bootstrap.

        Otherwise a transient listing failure would look like 'no open PR' and
        scaffold a duplicate over a live PR on another same-issue branch (#428).
        """
        from wade.git.pr import GhCliError

        task = Task(id="42", title="renamed title")
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch(
                "wade.services.implementation_service._shared.git_repo.get_current_branch",
                return_value="main",
            ),
            patch("wade.git.worktree.list_worktrees", return_value=[]),
            # Resolved branch has a CLOSED PR → gate consults the open-PR listing…
            patch(
                "wade.git.pr.get_pr_for_branch",
                return_value=PRLookup(found=True, pr=PRRef(number=7, state="CLOSED")),
            ),
            # …and that listing fails: must abort rather than treat as absence.
            patch(
                "wade.services.implementation_service.core.find_open_pr_branch_for_issue",
                side_effect=GhCliError("gh pr list failed"),
            ),
            patch("wade.git.worktree.create_worktree") as mock_create,
            patch("wade.services.implementation_service.core.bootstrap_draft_pr") as mock_bootstrap,
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)

        assert result.success is False
        mock_bootstrap.assert_not_called()
        mock_create.assert_not_called()

    def test_returns_false_on_creation_failure(self, tmp_path: Path) -> None:
        """create_worktree raises GitError → start() returns False."""
        task = self._make_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.git.worktree.list_worktrees", return_value=[]),
            patch(
                "wade.git.worktree.create_worktree",
                side_effect=GitError("Branch already exists"),
            ),
            patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup(found=False)),
            patch(
                "wade.services.implementation_service.core.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ),
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)

        assert result.success is False

    def test_cd_only_prints_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """cd_only=True → worktree path printed to stdout, no AI launched, returns True."""
        task = self._make_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.git.worktree.list_worktrees", return_value=[]),
            patch("wade.git.worktree.create_worktree"),
            patch("wade.services.implementation_service.core.write_plan_md"),
            patch("wade.services.implementation_service.core.bootstrap_worktree"),
            patch(
                "wade.services.implementation_service.core._detect_ai_cli_env", return_value=None
            ),
            patch("crossby.ai_tools.base.AbstractAITool.get") as mock_get,
            patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup(found=False)),
            patch(
                "wade.services.implementation_service.core.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ),
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path, cd_only=True)
            assert result.success is True
            mock_get.assert_not_called()

        captured = capsys.readouterr()
        assert "42" in captured.out  # Worktree path containing issue ID was printed

    def test_inside_ai_cli_skips_launch(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """AI env detected → AI tool not called, path printed."""
        task = self._make_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.git.worktree.list_worktrees", return_value=[]),
            patch("wade.git.worktree.create_worktree"),
            patch("wade.services.implementation_service.core.write_plan_md"),
            patch("wade.services.implementation_service.core.bootstrap_worktree"),
            patch(
                "wade.services.implementation_service.core._detect_ai_cli_env",
                return_value="CLAUDE_CODE",
            ),
            patch("crossby.ai_tools.base.AbstractAITool.get") as mock_get,
            patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup(found=False)),
            patch(
                "wade.services.implementation_service.core.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ),
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)
            assert result.success is True
            mock_get.assert_not_called()

        captured = capsys.readouterr()
        assert "42" in captured.out  # Worktree path containing issue ID was printed

    def test_no_plan_plan_first_skips_ai_selection(self, tmp_path: Path) -> None:
        """No plan + 'Plan first' → plan_service called, confirm_ai_selection NOT called."""
        task = self._make_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup(found=False)),
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
            patch("wade.services.implementation_service.core.confirm_ai_selection") as mock_confirm,
            patch("wade.services.plan_service.plan", return_value=True) as mock_plan,
        ):
            mock_prompts.is_tty.return_value = True
            mock_prompts.select.return_value = 0  # "Plan first (recommended)"

            result = start("42", project_root=tmp_path)

        assert result.success is True
        mock_plan.assert_called_once_with(issue_id="42", project_root=tmp_path)
        mock_confirm.assert_not_called()

    def test_no_plan_proceed_calls_ai_selection_and_bootstrap(self, tmp_path: Path) -> None:
        """No plan + 'Proceed' → confirm_ai_selection called, bootstrap_draft_pr called."""
        task = self._make_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.git.worktree.list_worktrees", return_value=[]),
            patch("wade.git.worktree.create_worktree"),
            patch("wade.services.implementation_service.core.write_plan_md"),
            patch("wade.services.implementation_service.core.bootstrap_worktree"),
            patch(
                "wade.services.implementation_service.core._detect_ai_cli_env", return_value=None
            ),
            patch("crossby.ai_tools.base.AbstractAITool.detect_installed", return_value=[]),
            patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup(found=False)),
            patch(
                "wade.services.implementation_service.core.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ) as mock_bootstrap,
            patch(
                "wade.services.implementation_service.core.confirm_ai_selection",
                return_value=("claude", "claude-sonnet-4-6", None, False),
            ) as mock_confirm,
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
        ):
            call_order: list[str] = []

            def _confirm(*args: object, **kwargs: object) -> tuple[str, str, None, bool]:
                call_order.append("confirm")
                return ("claude", "claude-sonnet-4-6", None, False)

            def _bootstrap(*args: object, **kwargs: object) -> dict[str, object]:
                call_order.append("bootstrap")
                return {"number": 1, "url": "http://test"}

            mock_confirm.side_effect = _confirm
            mock_bootstrap.side_effect = _bootstrap
            mock_prompts.is_tty.return_value = True
            mock_prompts.select.return_value = 1  # "Proceed without plan"

            result = start("42", project_root=tmp_path)

        assert result.success is False  # AI launch fails in test environment → failure
        mock_confirm.assert_called_once()
        mock_bootstrap.assert_called_once()
        assert call_order == ["confirm", "bootstrap"]

    def test_pr_lookup_failure_aborts_before_bootstrap(self, tmp_path: Path) -> None:
        """A failed PR lookup must stop — never scaffold a duplicate draft PR.

        On lookup_failed, existing_pr would collapse to None and bootstrap a
        fresh draft over a branch that may already have an open PR with a plan.
        """
        task = self._make_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch(
                "wade.git.pr.get_pr_for_branch",
                return_value=PRLookup(found=False, lookup_failed=True),
            ),
            patch("wade.services.implementation_service.core.bootstrap_draft_pr") as mock_bootstrap,
            patch("wade.services.implementation_service.core.confirm_ai_selection") as mock_confirm,
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
        ):
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)

        assert result.success is False
        mock_bootstrap.assert_not_called()
        mock_confirm.assert_not_called()


class TestImplementationStartBaseBranch:
    """start() base resolution (#376): inherit the draft PR's base, --base override."""

    _PR_BODY = "Implements #42\n<!-- wade:plan:start -->\nplan\n<!-- wade:plan:end -->"
    _CORE = "wade.services.implementation_service.core"

    def _make_config(self) -> ProjectConfig:
        return ProjectConfig(project=ProjectSettings(main_branch="main"))

    def _open_pr(self, base: str = "main") -> PRLookup:
        return PRLookup(
            found=True,
            pr=PRRef(number=7, url="http://x/7", state="OPEN", baseRefName=base),
        )

    def _worktree_path(self, tmp_path: Path) -> Path:
        # Mirrors core.start(): <worktrees_dir>/<repo_name>/<branch with / -> ->.
        return tmp_path / "wt" / tmp_path.name / "feat-42-test-task"

    def _enter_common_patches(
        self,
        stack: contextlib.ExitStack,
        tmp_path: Path,
        provider: MagicMock,
        *,
        pr_base: str,
    ) -> None:
        """Enter every patch shared by these tests EXCEPT update_pr_base (asserted per-test)."""
        self._worktree_path(tmp_path).mkdir(parents=True, exist_ok=True)
        c = self._CORE
        stack.enter_context(patch(f"{c}.load_config", return_value=self._make_config()))
        stack.enter_context(patch(f"{c}.get_provider", return_value=provider))
        stack.enter_context(patch("wade.git.repo.get_repo_root", return_value=tmp_path))
        stack.enter_context(patch(f"{c}._resolve_worktrees_dir", return_value=tmp_path / "wt"))
        stack.enter_context(
            patch("wade.git.pr.get_pr_for_branch", return_value=self._open_pr(base=pr_base))
        )
        stack.enter_context(patch("wade.git.pr.get_pr_body", return_value=self._PR_BODY))
        stack.enter_context(patch("wade.git.branch.branch_exists", return_value=True))
        stack.enter_context(patch("wade.git.worktree.list_worktrees", return_value=[]))
        stack.enter_context(patch("wade.git.worktree.checkout_existing_branch_worktree"))
        # Default: a scaffold-only branch — no in-flight work, and its re-root on the
        # new base is a no-op stub (the real re-root has its own tests). Per-test
        # overrides model the in-flight case (#376 review).
        stack.enter_context(patch(f"{c}._branch_has_real_work", return_value=False))
        stack.enter_context(patch(f"{c}.reroot_scaffold_branch_for_retarget", return_value=True))
        stack.enter_context(patch(f"{c}.write_plan_md"))
        stack.enter_context(patch(f"{c}.bootstrap_worktree"))
        stack.enter_context(patch(f"{c}._detect_ai_cli_env", return_value=None))
        stack.enter_context(patch(f"{c}._catchup_and_surface_staleness", return_value=None))
        stack.enter_context(
            patch("crossby.ai_tools.base.AbstractAITool.detect_installed", return_value=[])
        )
        mock_prompts = stack.enter_context(patch(f"{c}.prompts"))
        mock_prompts.is_tty.return_value = False

    def test_inherits_base_from_existing_pr_and_persists(self, tmp_path: Path) -> None:
        """No --base + open PR on develop → worktree base inherits develop and is persisted."""
        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test task")

        with contextlib.ExitStack() as stack:
            self._enter_common_patches(stack, tmp_path, provider, pr_base="develop")
            update_base = stack.enter_context(
                patch("wade.git.pr.update_pr_base", return_value=True)
            )
            result = start("42", project_root=tmp_path)

        assert result.success is True
        base_file = self._worktree_path(tmp_path) / ".wade" / "base_branch"
        assert base_file.read_text().strip() == "develop"
        update_base.assert_not_called()  # inheriting is not a retarget

    def test_explicit_base_override_retargets_pr(self, tmp_path: Path) -> None:
        """--base X while the PR targets main → update_pr_base called, X persisted."""
        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test task")

        with contextlib.ExitStack() as stack:
            self._enter_common_patches(stack, tmp_path, provider, pr_base="main")
            update_base = stack.enter_context(
                patch("wade.git.pr.update_pr_base", return_value=True)
            )
            result = start("42", project_root=tmp_path, base_branch="release/x")

        assert result.success is True
        update_base.assert_called_once()
        assert update_base.call_args.args[2] == "release/x"
        base_file = self._worktree_path(tmp_path) / ".wade" / "base_branch"
        assert base_file.read_text().strip() == "release/x"

    def test_failed_retarget_aborts(self, tmp_path: Path) -> None:
        """A failed update_pr_base surfaces failure rather than proceeding on a stale base."""
        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test task")

        with contextlib.ExitStack() as stack:
            self._enter_common_patches(stack, tmp_path, provider, pr_base="main")
            stack.enter_context(patch("wade.git.pr.update_pr_base", return_value=False))
            result = start("42", project_root=tmp_path, base_branch="release/x")

        assert result.success is False

    def test_failed_retarget_restores_rerooted_head(self, tmp_path: Path) -> None:
        """When update_pr_base fails after the reroot force-pushed the rewritten head, the
        head is rolled back so the remote branch and the still-old-base PR stay consistent —
        the same rollback bootstrap_draft_pr added, wired into start()'s parallel retarget
        entry point (#376 review)."""
        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test task")

        with contextlib.ExitStack() as stack:
            self._enter_common_patches(stack, tmp_path, provider, pr_base="main")
            stack.enter_context(patch("wade.git.pr.update_pr_base", return_value=False))
            # Model a reroot that rewrote the head: pre != post SHA → restore expected.
            stack.enter_context(
                patch(f"{self._CORE}._resolve_head_sha", side_effect=["oldsha", "newsha"])
            )
            restore = stack.enter_context(patch(f"{self._CORE}._restore_scaffold_head"))
            result = start("42", project_root=tmp_path, base_branch="release/x")

        assert result.success is False
        restore.assert_called_once_with(tmp_path, "feat/42-test-task", "oldsha", 7)

    def test_failed_retarget_skips_restore_when_head_unchanged(self, tmp_path: Path) -> None:
        """A real-work branch is left untouched by the reroot (SHA unchanged), so a failed
        retarget must NOT hard-reset it — skip the restore to avoid discarding work (#376)."""
        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test task")

        with contextlib.ExitStack() as stack:
            self._enter_common_patches(stack, tmp_path, provider, pr_base="main")
            stack.enter_context(patch("wade.git.pr.update_pr_base", return_value=False))
            stack.enter_context(
                patch(f"{self._CORE}._resolve_head_sha", side_effect=["samesha", "samesha"])
            )
            restore = stack.enter_context(patch(f"{self._CORE}._restore_scaffold_head"))
            result = start("42", project_root=tmp_path, base_branch="release/x")

        assert result.success is False
        restore.assert_not_called()

    def test_override_to_main_clears_stale_base_file(self, tmp_path: Path) -> None:
        """`--base main` retargets a PR previously on a non-main base and deletes the
        stale .wade/base_branch so catchup/sync/done stop targeting the old base (#376)."""
        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test task")

        with contextlib.ExitStack() as stack:
            self._enter_common_patches(stack, tmp_path, provider, pr_base="develop")
            update_base = stack.enter_context(
                patch("wade.git.pr.update_pr_base", return_value=True)
            )
            # A stale pin left by a previous non-main run on the reused worktree.
            wade_dir = self._worktree_path(tmp_path) / ".wade"
            wade_dir.mkdir(parents=True, exist_ok=True)
            (wade_dir / "base_branch").write_text("develop\n")

            result = start("42", project_root=tmp_path, base_branch="main")

        assert result.success is True
        update_base.assert_called_once()  # develop -> main retarget
        base_file = self._worktree_path(tmp_path) / ".wade" / "base_branch"
        assert not base_file.exists()

    def test_inflight_base_override_aborts_without_confirmation(self, tmp_path: Path) -> None:
        """A --base retarget on a branch with in-flight work cannot rewrite history, so
        without a TTY confirmation it aborts rather than silently flipping the PR base and
        polluting its diff with the old base's commits — mirrors the plan guard (#376)."""
        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test task")

        with contextlib.ExitStack() as stack:
            self._enter_common_patches(stack, tmp_path, provider, pr_base="develop")
            # Override the scaffold default: this branch carries real work.
            stack.enter_context(patch(f"{self._CORE}._branch_has_real_work", return_value=True))
            update_base = stack.enter_context(
                patch("wade.git.pr.update_pr_base", return_value=True)
            )
            result = start("42", project_root=tmp_path, base_branch="main")

        assert result.success is False
        update_base.assert_not_called()  # PR base never silently flipped

    def test_empty_pr_base_aborts_retarget_when_base_unknown(self, tmp_path: Path) -> None:
        """If the PR's base reads back empty, the current base is unknown, so the reroot
        cannot prove the branch is a rerootable scaffold and refuses — `start()` must abort
        before touching the PR base rather than blindly reset/force-push a branch of unknown
        provenance (#376 review). The reroot's own abort-on-unknown-base is unit-tested in
        test_draft_pr_retarget; here we model that refusal to verify start()'s wiring."""
        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test task")

        with contextlib.ExitStack() as stack:
            self._enter_common_patches(stack, tmp_path, provider, pr_base="")
            # Override the scaffold default: the real reroot returns False for an unknown
            # (empty) old base — model that here.
            stack.enter_context(
                patch(
                    f"{self._CORE}.reroot_scaffold_branch_for_retarget",
                    return_value=False,
                )
            )
            update_base = stack.enter_context(
                patch("wade.git.pr.update_pr_base", return_value=True)
            )
            result = start("42", project_root=tmp_path, base_branch="develop")

        assert result.success is False
        update_base.assert_not_called()  # PR base never touched on an unprovable reroot

    def test_malformed_explicit_base_is_rejected(self, tmp_path: Path) -> None:
        """A hand-typed --base with whitespace / invalid ref chars fails fast — symmetric
        with the plan-declared path validated at plan-done, and before any work (#376)."""
        provider = MagicMock()

        with (
            patch(f"{self._CORE}.load_config", return_value=self._make_config()),
            patch(f"{self._CORE}.get_provider", return_value=provider),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
        ):
            result = start("42", project_root=tmp_path, base_branch="bad base")

        assert result.success is False
        provider.read_task.assert_not_called()  # rejected before the issue is read

    def test_empty_explicit_base_is_rejected(self, tmp_path: Path) -> None:
        """An explicit `--base ""` is malformed and must fail fast — validating on
        `is not None` (not truthiness) so an empty value is rejected rather than silently
        inheriting the PR/main base (#376 review)."""
        provider = MagicMock()

        with (
            patch(f"{self._CORE}.load_config", return_value=self._make_config()),
            patch(f"{self._CORE}.get_provider", return_value=provider),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
        ):
            result = start("42", project_root=tmp_path, base_branch="")

        assert result.success is False
        provider.read_task.assert_not_called()  # rejected before the issue is read


# ---------------------------------------------------------------------------
# Implementation batch tests
# ---------------------------------------------------------------------------


class TestImplementationBatch:
    """Tests for implementation_service.batch() — exercises topology and launch dispatch."""

    def _batch_patches(self, tmp_path: Path, **overrides: object):  # type: ignore[no-untyped-def]
        """Common context manager patches for batch tests."""
        from contextlib import ExitStack

        defaults = {
            "wade.services.implementation_service.core.load_config": ProjectConfig(),
            "wade.git.repo.get_repo_root": tmp_path,
            "wade.services.implementation_service.batch._build_graph_from_issues": None,
            "wade.services.implementation_service.batch.launch_batch_in_terminals": True,
            "wade.services.implementation_service.batch._find_tracking_issue": None,
            "wade.services.implementation_service.batch.poll_batch_completion": None,
        }
        defaults.update(overrides)
        stack = ExitStack()
        mocks = {}
        for target, rv in defaults.items():
            m = stack.enter_context(patch(target, return_value=rv))
            mocks[target.rsplit(".", 1)[-1]] = m
        return stack, mocks

    def test_launches_independent_issues(self, tmp_path: Path) -> None:
        """No deps graph → all issues passed to batch launcher."""
        stack, mocks = self._batch_patches(tmp_path)
        with stack:
            result = batch(["1", "2", "3"], project_root=tmp_path)

        assert result is True
        mocks["launch_batch_in_terminals"].assert_called_once()
        items = mocks["launch_batch_in_terminals"].call_args[0][0]
        assert len(items) == 3

    def test_launches_only_first_in_chain(self, tmp_path: Path) -> None:
        """Dependency chain → only the first issue in batch, rest printed."""
        mock_graph = MagicMock()
        mock_graph.edges = [MagicMock()]  # non-empty → triggers partition
        mock_graph.partition.return_value = ([], [["1", "2", "3"]])

        stack, mocks = self._batch_patches(
            tmp_path,
            **{"wade.services.implementation_service.batch._build_graph_from_issues": mock_graph},
        )
        with stack:
            result = batch(["1", "2", "3"], project_root=tmp_path)

        assert result is True
        items = mocks["launch_batch_in_terminals"].call_args[0][0]
        assert len(items) == 1  # Only the first in the chain
        assert items[0][0][:3] == ["wade", "implement", "1"]

    def test_terminal_failure_is_non_fatal(self, tmp_path: Path) -> None:
        """Terminal launch failure is non-fatal — batch continues to polling."""
        stack, mocks = self._batch_patches(
            tmp_path,
            **{"wade.services.implementation_service.batch.launch_batch_in_terminals": False},
        )
        with stack:
            result = batch(["1", "2"], project_root=tmp_path)

        # Terminal failure is non-fatal; batch still returns True and polls
        assert result is True
        mocks["poll_batch_completion"].assert_called_once()

    def test_deduplicates_issue_numbers(self, tmp_path: Path) -> None:
        """Duplicate issue numbers are removed, launching each only once."""
        stack, mocks = self._batch_patches(tmp_path)
        with stack:
            result = batch(["1", "2", "1", "3", "2"], project_root=tmp_path)

        assert result is True
        items = mocks["launch_batch_in_terminals"].call_args[0][0]
        assert len(items) == 3  # 1, 2, 3 — not 5

    def test_batch_items_contain_correct_commands(self, tmp_path: Path) -> None:
        """Batch items contain correct wade implement commands with flags."""
        stack, mocks = self._batch_patches(tmp_path)
        with stack:
            result = batch(["1", "2"], project_root=tmp_path)

        assert result is True
        items = mocks["launch_batch_in_terminals"].call_args[0][0]
        # Each item is (command, cwd, title)
        for item in items:
            cmd, cwd, title = item
            assert cmd[:2] == ["wade", "implement"]
            assert cwd == str(tmp_path)
            assert title.startswith("wade #")

    def test_model_not_passed_when_not_explicit(self, tmp_path: Path) -> None:
        """When model_explicit=False, --model is NOT passed to child commands."""
        stack, mocks = self._batch_patches(tmp_path)
        with stack:
            batch(["1"], model="claude-sonnet-4-6", model_explicit=False, project_root=tmp_path)

        items = mocks["launch_batch_in_terminals"].call_args[0][0]
        cmd = items[0][0]
        assert "--model" not in cmd

    def test_model_passed_when_explicit(self, tmp_path: Path) -> None:
        """When model_explicit=True, --model IS passed to child commands."""
        stack, mocks = self._batch_patches(tmp_path)
        with stack:
            batch(["1"], model="claude-sonnet-4-6", model_explicit=True, project_root=tmp_path)

        items = mocks["launch_batch_in_terminals"].call_args[0][0]
        cmd = items[0][0]
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-sonnet-4-6"

    def test_dependency_cycle_returns_false(self, tmp_path: Path) -> None:
        """Dependency cycle in graph.partition() returns False with clean error."""
        mock_graph = MagicMock()
        mock_graph.edges = [MagicMock()]
        mock_graph.partition.side_effect = ValueError("cycle")

        stack, mocks = self._batch_patches(
            tmp_path,
            **{"wade.services.implementation_service.batch._build_graph_from_issues": mock_graph},
        )
        with stack:
            result = batch(["1", "2"], project_root=tmp_path)

        assert result is False
        mocks["launch_batch_in_terminals"].assert_not_called()


# ---------------------------------------------------------------------------
# Batch polling / status classification
# ---------------------------------------------------------------------------


def _make_pr(**kwargs: object) -> PRSummary:
    """Build a PRSummary with sensible defaults for tests."""
    defaults: dict[str, object] = {
        "number": 1,
        "url": "http://pr/1",
        "headRefName": "feat/1-test",
        "state": "OPEN",
        "isDraft": False,
        "mergedAt": None,
    }
    defaults.update(kwargs)
    return PRSummary(**defaults)  # type: ignore[arg-type]


class TestClassifyIssueStatus:
    """Tests for _classify_issue_status()."""

    def test_merged_pr(self, tmp_path: Path) -> None:
        pr_by_issue = {"1": _make_pr(mergedAt="2024-01-01", state="MERGED")}
        result = _classify_issue_status("1", pr_by_issue, set(), "main", tmp_path)
        assert result == _BATCH_STATUS_MERGED

    def test_draft_pr_is_in_progress(self, tmp_path: Path) -> None:
        pr_by_issue = {"1": _make_pr(isDraft=True)}
        result = _classify_issue_status("1", pr_by_issue, set(), "main", tmp_path)
        assert result == _BATCH_STATUS_IN_PROGRESS

    def test_open_pr_not_draft_is_done(self, tmp_path: Path) -> None:
        pr_by_issue = {"1": _make_pr()}
        result = _classify_issue_status("1", pr_by_issue, set(), "main", tmp_path)
        assert result == _BATCH_STATUS_DONE

    def test_closed_pr_without_merge_is_not_done(self, tmp_path: Path) -> None:
        pr_by_issue = {"1": _make_pr(state="CLOSED")}
        with patch(
            "wade.services.implementation_service.batch._is_merged_to_main", return_value=False
        ):
            result = _classify_issue_status("1", pr_by_issue, set(), "main", tmp_path)
        assert result == _BATCH_STATUS_NOT_STARTED

    def test_no_pr_no_branch_is_not_started(self, tmp_path: Path) -> None:
        with patch(
            "wade.services.implementation_service.batch._is_merged_to_main", return_value=False
        ):
            result = _classify_issue_status("1", {}, set(), "main", tmp_path)
        assert result == _BATCH_STATUS_NOT_STARTED

    def test_no_pr_with_branch_is_in_progress(self, tmp_path: Path) -> None:
        branches = {"origin/feat/1-add-auth"}
        with patch("wade.git.branch.commits_ahead", return_value=1):
            result = _classify_issue_status("1", {}, branches, "main", tmp_path)
        assert result == _BATCH_STATUS_IN_PROGRESS

    def test_no_pr_no_branch_direct_merge_is_done(self, tmp_path: Path) -> None:
        with patch(
            "wade.services.implementation_service.batch._is_merged_to_main", return_value=True
        ):
            result = _classify_issue_status("1", {}, set(), "main", tmp_path)
        assert result == _BATCH_STATUS_DONE

    def test_branch_query_failed_is_unknown(self, tmp_path: Path) -> None:
        """A failed branch query (branch_set=None) must not report NOT_STARTED."""
        result = _classify_issue_status("1", {}, None, "main", tmp_path)
        assert result == _BATCH_STATUS_UNKNOWN

    def test_pr_classification_ignores_none_branch_set(self, tmp_path: Path) -> None:
        """When a PR exists, a failed branch query is irrelevant to the status."""
        pr_by_issue = {"1": _make_pr(mergedAt="2024-01-01", state="MERGED")}
        result = _classify_issue_status("1", pr_by_issue, None, "main", tmp_path)
        assert result == _BATCH_STATUS_MERGED


class TestBranchQueryHelpers:
    """Tests for _get_remote_branches() and _query_branches()."""

    def test_get_remote_branches_delegates_to_git_layer(self, tmp_path: Path) -> None:
        with patch(
            "wade.services.implementation_service.batch.git_branch.list_branch_names",
            return_value={"main", "origin/feat/1-x"},
        ) as mock_list:
            result = _get_remote_branches(tmp_path)
        assert result == {"main", "origin/feat/1-x"}
        mock_list.assert_called_once_with(tmp_path)

    def test_get_remote_branches_propagates_git_error(self, tmp_path: Path) -> None:
        with (
            patch(
                "wade.services.implementation_service.batch.git_branch.list_branch_names",
                side_effect=GitError("boom"),
            ),
            pytest.raises(GitError),
        ):
            _get_remote_branches(tmp_path)

    def test_query_branches_returns_fresh_on_success(self, tmp_path: Path) -> None:
        with patch(
            "wade.services.implementation_service.batch._get_remote_branches",
            return_value={"main"},
        ):
            result = _query_branches(tmp_path, previous={"stale"})
        assert result == {"main"}

    def test_query_branches_keeps_previous_on_error(self, tmp_path: Path) -> None:
        with patch(
            "wade.services.implementation_service.batch._get_remote_branches",
            side_effect=GitError("lock"),
        ):
            result = _query_branches(tmp_path, previous={"main"})
        assert result == {"main"}

    def test_query_branches_returns_none_on_first_cycle_error(self, tmp_path: Path) -> None:
        """First cycle has no prior snapshot — a failure stays None, not empty set."""
        with patch(
            "wade.services.implementation_service.batch._get_remote_branches",
            side_effect=GitError("lock"),
        ):
            result = _query_branches(tmp_path, previous=None)
        assert result is None


class TestBuildPrIndex:
    """Tests for _build_pr_index()."""

    def test_maps_prs_to_issue_numbers(self, tmp_path: Path) -> None:
        mock_prs = [
            _make_pr(number=10, headRefName="feat/1-auth", url="http://pr/10"),
            _make_pr(number=11, headRefName="feat/2-fix", url="http://pr/11"),
            _make_pr(number=12, headRefName="feat/99-other", url="http://pr/12"),
        ]
        with patch("wade.git.pr.list_prs", return_value=mock_prs):
            result = _build_pr_index(tmp_path, ["1", "2"])

        assert "1" in result
        assert "2" in result
        assert "99" not in result  # Not in requested issues

    def test_empty_prs(self, tmp_path: Path) -> None:
        with patch("wade.git.pr.list_prs", return_value=[]):
            result = _build_pr_index(tmp_path, ["1"])
        assert result == {}


class TestFindTrackingIssue:
    """Tests for _find_tracking_issue()."""

    def test_finds_parent_from_second_issue(self) -> None:
        """Iterates through issues to find parent, not just the first."""
        mock_provider = MagicMock()
        mock_provider.find_parent_issue.side_effect = [None, "100", None]

        with (
            patch(
                "wade.services.implementation_service.batch.get_provider",
                return_value=mock_provider,
            ),
        ):
            result = _find_tracking_issue(["1", "2", "3"], ProjectConfig())

        assert result == "100"
        assert mock_provider.find_parent_issue.call_count == 2

    def test_returns_none_when_no_parent(self) -> None:
        mock_provider = MagicMock()
        mock_provider.find_parent_issue.return_value = None

        with patch(
            "wade.services.implementation_service.batch.get_provider", return_value=mock_provider
        ):
            result = _find_tracking_issue(["1", "2"], ProjectConfig())

        assert result is None


class TestPollBatchCompletion:
    """Tests for poll_batch_completion()."""

    def test_exits_when_all_done(self, tmp_path: Path) -> None:
        """Polling exits immediately when all issues are done."""
        pr_index = {
            "1": _make_pr(number=1, url="http://pr/1"),
            "2": _make_pr(number=2, url="http://pr/2"),
        }
        with (
            patch(
                "wade.services.implementation_service.batch._build_pr_index", return_value=pr_index
            ),
            patch(
                "wade.services.implementation_service.batch._get_remote_branches",
                return_value=set(),
            ),
            patch("wade.services.implementation_service.batch.git_sync.fetch_origin"),
        ):
            poll_batch_completion(
                issue_numbers=["1", "2"],
                repo_root=tmp_path,
                config=ProjectConfig(),
                poll_interval=0,
                timeout=1,
            )
        # Should exit without error (all done on first poll)

    def test_auto_triggers_review_batch(self, tmp_path: Path) -> None:
        """Auto-triggers coherence review when tracking issue exists and all done."""
        pr_index = {
            "1": _make_pr(number=1, url="http://pr/1"),
        }
        with (
            patch(
                "wade.services.implementation_service.batch._build_pr_index", return_value=pr_index
            ),
            patch(
                "wade.services.implementation_service.batch._get_remote_branches",
                return_value=set(),
            ),
            patch("wade.services.implementation_service.batch.git_sync.fetch_origin"),
            patch("wade.services.batch_review_service.review_batch") as mock_review,
        ):
            poll_batch_completion(
                issue_numbers=["1"],
                repo_root=tmp_path,
                config=ProjectConfig(),
                tracking_id="100",
                poll_interval=0,
                timeout=1,
            )
        mock_review.assert_called_once_with("100", project_root=tmp_path)

    def test_branch_query_error_does_not_crash_or_complete(self, tmp_path: Path) -> None:
        """A failed branch query is handled (unknown), never crashes, and does not

        auto-trigger the coherence review (the issue is not counted as done).
        """
        with (
            patch("wade.services.implementation_service.batch._build_pr_index", return_value={}),
            patch(
                "wade.services.implementation_service.batch._get_remote_branches",
                side_effect=GitError("could not write index"),
            ),
            patch("wade.services.implementation_service.batch.git_sync.fetch_origin"),
            patch("wade.services.implementation_service.batch.time.sleep"),
            patch("wade.services.batch_review_service.review_batch") as mock_review,
        ):
            poll_batch_completion(
                issue_numbers=["1"],
                repo_root=tmp_path,
                config=ProjectConfig(),
                tracking_id="100",
                poll_interval=0,
                timeout=1,
            )
        mock_review.assert_not_called()


# ---------------------------------------------------------------------------
# _parse_overwrite_paths / _pull_main_after_merge
# ---------------------------------------------------------------------------

UNTRACKED_STDERR = (
    "error: The following untracked working tree files would be overwritten by merge:\n"
    "\t.claude/settings.json\n"
    "\t.wade-managed\n"
    "Please move or remove them before you merge.\n"
)

LOCAL_CHANGES_STDERR = (
    "error: Your local changes to the following files would be overwritten by merge:\n"
    "\tsrc/main.py\n"
    "Please commit your changes or stash them before you merge.\n"
)


class TestParseOverwritePaths:
    def test_extracts_paths_from_untracked_stderr(self) -> None:
        paths = _parse_overwrite_paths(UNTRACKED_STDERR)
        assert paths == [".claude/settings.json", ".wade-managed"]

    def test_returns_empty_for_unrelated_stderr(self) -> None:
        paths = _parse_overwrite_paths("fatal: some other error\n")
        assert paths == []

    def test_ignores_local_changes_block_when_both_present(self) -> None:
        """When git reports both failure classes in one stderr (local-changes
        block first, untracked block second), parsing must anchor on the
        untracked marker specifically — not the generic "would be overwritten
        by merge" substring both blocks share — or it would return the
        tracked, locally-modified path instead of the untracked one."""
        combined_stderr = LOCAL_CHANGES_STDERR + UNTRACKED_STDERR
        paths = _parse_overwrite_paths(combined_stderr)
        assert paths == [".claude/settings.json", ".wade-managed"]


class TestPullMainAfterMerge:
    def test_untracked_backed_up_then_retry(self, tmp_path: Path) -> None:
        """Untracked-files error moves colliding files to a backup dir (never
        deletes them) and retries the pull."""
        # Create the files that would be "untracked"
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")
        managed = tmp_path / ".wade-managed"
        managed.write_text("# managed")

        fail_result = MagicMock(returncode=1, stderr=UNTRACKED_STDERR)
        ok_result = MagicMock(returncode=0, stderr="")

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                side_effect=[fail_result, ok_result],
            ),
            patch("wade.services.implementation_service.lifecycle.console") as mock_console,
        ):
            _pull_main_after_merge(tmp_path)

        # Original locations are cleared so the pull can proceed...
        assert not settings.exists()
        assert not managed.exists()
        # ...but the files are preserved in the backup dir, never destroyed.
        backup_root = tmp_path / ".wade" / "pull-backups"
        assert (backup_root / ".claude" / "settings.json").read_text() == "{}"
        assert (backup_root / ".wade-managed").read_text() == "# managed"
        # The success path (test_untracked_success_keeps_backup) prints the notice
        # so a human sees where the set-aside files went; no failure warning fires.
        warn_calls = [c.args[0] for c in mock_console.warn.call_args_list]
        assert any("Backed up untracked files" in msg for msg in warn_calls)
        assert not any("Could not sync local main" in msg for msg in warn_calls)

    def test_local_changes_triggers_stash_and_retry(self, tmp_path: Path) -> None:
        """Tracked-files error triggers stash, pull retry, then stash pop."""
        target_file = tmp_path / "src" / "main.py"
        target_file.parent.mkdir(parents=True)
        target_file.write_text("print('hello')")

        fail_result = MagicMock(returncode=1, stderr=LOCAL_CHANGES_STDERR)
        stash_ok = MagicMock(returncode=0)
        pull_ok = MagicMock(returncode=0, stderr="")
        pop_ok = MagicMock(returncode=0)

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                side_effect=[fail_result, pull_ok],
            ),
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash",
                return_value=stash_ok,
            ) as mock_stash,
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash_pop",
                return_value=pop_ok,
            ) as mock_pop,
        ):
            _pull_main_after_merge(tmp_path)

        # File must NOT be deleted
        assert target_file.exists()
        mock_stash.assert_called_once_with(tmp_path)
        mock_pop.assert_called_once_with(tmp_path)

    def test_local_changes_stash_failure_warns(self, tmp_path: Path) -> None:
        """When stash fails, falls through to warning without retry."""
        fail_result = MagicMock(returncode=1, stderr=LOCAL_CHANGES_STDERR)
        stash_fail = MagicMock(returncode=1)

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                return_value=fail_result,
            ),
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash",
                return_value=stash_fail,
            ),
            patch("wade.services.implementation_service.lifecycle.git_repo.stash_pop") as mock_pop,
            patch("wade.services.implementation_service.lifecycle.console") as mock_console,
        ):
            _pull_main_after_merge(tmp_path)

        mock_pop.assert_not_called()
        mock_console.warn.assert_called_once()
        mock_console.hint.assert_called_once()

    def test_local_changes_pull_retry_failure_warns(self, tmp_path: Path) -> None:
        """When stash succeeds but pull retry fails, warns and still pops stash."""
        fail_result = MagicMock(returncode=1, stderr=LOCAL_CHANGES_STDERR)
        stash_ok = MagicMock(returncode=0)
        pull_fail = MagicMock(returncode=1, stderr="some error")
        pop_ok = MagicMock(returncode=0)

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                side_effect=[fail_result, pull_fail],
            ),
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash",
                return_value=stash_ok,
            ),
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash_pop",
                return_value=pop_ok,
            ) as mock_pop,
            patch("wade.services.implementation_service.lifecycle.console") as mock_console,
        ):
            _pull_main_after_merge(tmp_path)

        mock_pop.assert_called_once_with(tmp_path)
        mock_console.warn.assert_called_once()
        mock_console.hint.assert_called_once()

    def test_local_changes_stash_pop_failure_warns(self, tmp_path: Path) -> None:
        """When stash pop fails after a successful pull, warns with a recovery hint."""
        fail_result = MagicMock(returncode=1, stderr=LOCAL_CHANGES_STDERR)
        stash_ok = MagicMock(returncode=0)
        pull_ok = MagicMock(returncode=0, stderr="")
        pop_fail = MagicMock(returncode=1)

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                side_effect=[fail_result, pull_ok],
            ),
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash",
                return_value=stash_ok,
            ),
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash_pop",
                return_value=pop_fail,
            ) as mock_pop,
            patch("wade.services.implementation_service.lifecycle.console") as mock_console,
        ):
            _pull_main_after_merge(tmp_path)

        # Pull succeeded, so the only warning comes from the failed stash pop.
        mock_pop.assert_called_once_with(tmp_path)
        mock_console.warn.assert_called_once()
        mock_console.hint.assert_called_once()

    def test_untracked_retry_failure_restores_files(self, tmp_path: Path) -> None:
        """Core regression: when the retry pull fails after moving untracked
        collisions aside, the files are restored to their original paths (a
        failed sync is a no-op) and a warning is printed."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")
        managed = tmp_path / ".wade-managed"
        managed.write_text("# managed")

        untracked_fail = MagicMock(returncode=1, stderr=UNTRACKED_STDERR)
        unknown_fail = MagicMock(returncode=1, stderr="fatal: unable to access remote\n")

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                side_effect=[untracked_fail, unknown_fail],
            ),
            patch("wade.services.implementation_service.lifecycle.console") as mock_console,
        ):
            _pull_main_after_merge(tmp_path)

        # Files are back where they started, with their original content...
        assert settings.read_text() == "{}"
        assert managed.read_text() == "# managed"
        # ...and nothing is stranded in the backup dir (rolled back cleanly).
        backup_root = tmp_path / ".wade" / "pull-backups"
        assert not (backup_root / ".claude" / "settings.json").exists()
        assert not (backup_root / ".wade-managed").exists()
        # A failed sync warns the user.
        warn_calls = [c.args[0] for c in mock_console.warn.call_args_list]
        assert any("Could not sync local main" in msg for msg in warn_calls)

    def test_untracked_then_local_changes_combined(self, tmp_path: Path) -> None:
        """The combined scenario MAX_RESOLVE_ATTEMPTS keeps a spare for:
        untracked collision -> move aside -> retry fails with a *local-changes*
        error -> stash -> retry succeeds. Both classes handled; final tree state
        correct (untracked files backed up, stash popped)."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")
        managed = tmp_path / ".wade-managed"
        managed.write_text("# managed")

        untracked_fail = MagicMock(returncode=1, stderr=UNTRACKED_STDERR)
        local_fail = MagicMock(returncode=1, stderr=LOCAL_CHANGES_STDERR)
        ok = MagicMock(returncode=0, stderr="")

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                side_effect=[untracked_fail, local_fail, ok],
            ),
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash",
                return_value=MagicMock(returncode=0),
            ) as mock_stash,
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash_pop",
                return_value=MagicMock(returncode=0),
            ) as mock_pop,
        ):
            _pull_main_after_merge(tmp_path)

        # Untracked files preserved in the backup dir on the success path...
        assert not settings.exists()
        assert not managed.exists()
        backup_root = tmp_path / ".wade" / "pull-backups"
        assert (backup_root / ".claude" / "settings.json").read_text() == "{}"
        assert (backup_root / ".wade-managed").read_text() == "# managed"
        # ...and the stashed tracked changes were popped back.
        mock_stash.assert_called_once_with(tmp_path)
        mock_pop.assert_called_once_with(tmp_path)

    def test_cap_exhaustion_rolls_back(self, tmp_path: Path) -> None:
        """Three full resolve iterations that each make progress but whose retry
        still fails exhaust MAX_RESOLVE_ATTEMPTS: the loop ends and rolls back
        every move plus the stash. Guards the cap boundary itself (distinct from
        the no-progress early exit and the 2-iteration combined path)."""
        assert MAX_RESOLVE_ATTEMPTS == 3  # this test asserts the exact-cap path
        file_a = tmp_path / ".claude" / "settings.json"
        file_a.parent.mkdir(parents=True)
        file_a.write_text("A")
        file_b = tmp_path / ".wade-managed"
        file_b.write_text("B")

        stderr_a = (
            "error: The following untracked working tree files would be overwritten by merge:\n"
            "\t.claude/settings.json\n"
            "Please move or remove them before you merge.\n"
        )
        stderr_b = (
            "error: The following untracked working tree files would be overwritten by merge:\n"
            "\t.wade-managed\n"
            "Please move or remove them before you merge.\n"
        )
        # pull1 -> iter1 moves A -> pull2 (local) -> iter2 stashes -> pull3 (B
        # untracked) -> iter3 moves B -> pull4 still fails -> loop ends (cap).
        pulls = [
            MagicMock(returncode=1, stderr=stderr_a),
            MagicMock(returncode=1, stderr=LOCAL_CHANGES_STDERR),
            MagicMock(returncode=1, stderr=stderr_b),
            MagicMock(returncode=1, stderr="fatal: unable to access remote\n"),
        ]

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                side_effect=pulls,
            ) as mock_pull,
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash",
                return_value=MagicMock(returncode=0),
            ),
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash_pop",
                return_value=MagicMock(returncode=0),
            ) as mock_pop,
            patch("wade.services.implementation_service.lifecycle.console") as mock_console,
        ):
            _pull_main_after_merge(tmp_path)

        # Exactly the entry pull + 3 retries — the cap stopped a 4th resolution.
        assert mock_pull.call_count == 4
        # Both moved files restored, stash popped, and the user warned.
        assert file_a.read_text() == "A"
        assert file_b.read_text() == "B"
        mock_pop.assert_called_once_with(tmp_path)
        warn_calls = [c.args[0] for c in mock_console.warn.call_args_list]
        assert any("Could not sync local main" in msg for msg in warn_calls)

    def test_terminal_failure_after_stash_and_move_rolls_back_both(self, tmp_path: Path) -> None:
        """Both a move and a stash happened, then the final retry fails: the
        rollback restores the moved files AND pops the stash before warning."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")
        managed = tmp_path / ".wade-managed"
        managed.write_text("# managed")

        untracked_fail = MagicMock(returncode=1, stderr=UNTRACKED_STDERR)
        local_fail = MagicMock(returncode=1, stderr=LOCAL_CHANGES_STDERR)
        unknown_fail = MagicMock(returncode=1, stderr="fatal: unable to access remote\n")

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                side_effect=[untracked_fail, local_fail, unknown_fail],
            ),
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash",
                return_value=MagicMock(returncode=0),
            ),
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash_pop",
                return_value=MagicMock(returncode=0),
            ) as mock_pop,
            patch("wade.services.implementation_service.lifecycle.console") as mock_console,
        ):
            _pull_main_after_merge(tmp_path)

        # Moved files restored to their original paths...
        assert settings.read_text() == "{}"
        assert managed.read_text() == "# managed"
        # ...the stash was popped during rollback...
        mock_pop.assert_called_once_with(tmp_path)
        # ...and the user was warned about the failed sync.
        warn_calls = [c.args[0] for c in mock_console.warn.call_args_list]
        assert any("Could not sync local main" in msg for msg in warn_calls)

    def test_untracked_retry_failure_restores_nested_path(self, tmp_path: Path) -> None:
        """A nested collision path whose parent dir is rmdir'd during move-aside
        is still restored on rollback — proving _restore_backed_up recreates the
        parent dir rather than hitting the manual-command fallback."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")

        nested_stderr = (
            "error: The following untracked working tree files would be overwritten by merge:\n"
            "\t.claude/settings.json\n"
            "Please move or remove them before you merge.\n"
        )
        untracked_fail = MagicMock(returncode=1, stderr=nested_stderr)
        unknown_fail = MagicMock(returncode=1, stderr="fatal: unable to access remote\n")

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                side_effect=[untracked_fail, unknown_fail],
            ),
            patch("wade.services.implementation_service.lifecycle.console"),
        ):
            _pull_main_after_merge(tmp_path)

        # The .claude/ parent was rmdir'd during move-aside then recreated here.
        assert settings.read_text() == "{}"
        assert not (tmp_path / ".wade" / "pull-backups" / ".claude").exists()

    def test_restore_failure_prints_manual_command_and_continues(self, tmp_path: Path) -> None:
        """If restoring one pair raises OSError, the exact `mv` recovery command
        is surfaced, the *other* pairs are still restored, and stash_pop runs."""
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")
        managed = tmp_path / ".wade-managed"
        managed.write_text("# managed")

        managed_backup = tmp_path / ".wade" / "pull-backups" / ".wade-managed"

        real_move = shutil.move

        def flaky_move(src: str, dst: str) -> object:
            # Fail only when restoring .wade-managed (moving it back out of the
            # backup dir); every other move — including move-aside — is real.
            if str(src) == str(managed_backup):
                raise OSError("simulated restore failure")
            return real_move(src, dst)

        untracked_fail = MagicMock(returncode=1, stderr=UNTRACKED_STDERR)
        local_fail = MagicMock(returncode=1, stderr=LOCAL_CHANGES_STDERR)
        unknown_fail = MagicMock(returncode=1, stderr="fatal: unable to access remote\n")

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                side_effect=[untracked_fail, local_fail, unknown_fail],
            ),
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash",
                return_value=MagicMock(returncode=0),
            ),
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.stash_pop",
                return_value=MagicMock(returncode=0),
            ) as mock_pop,
            patch(
                "wade.services.implementation_service.lifecycle.shutil.move",
                side_effect=flaky_move,
            ),
            patch("wade.services.implementation_service.lifecycle.console") as mock_console,
        ):
            _pull_main_after_merge(tmp_path)

        # The other pair (settings.json) was still restored despite the failure...
        assert settings.read_text() == "{}"
        # ...the un-restorable file remains safe in the backup dir...
        assert not managed.exists()
        assert managed_backup.read_text() == "# managed"
        # ...the exact manual recovery command was surfaced to the user...
        hint_calls = [c.args[0] for c in mock_console.hint.call_args_list]
        assert any(f"mv {managed_backup} {managed}" in msg for msg in hint_calls)
        # ...and the stash was still popped (one bad move never strands the stash).
        mock_pop.assert_called_once_with(tmp_path)

    def test_restore_failure_shell_quotes_paths_with_spaces(self, tmp_path: Path) -> None:
        """The manual `mv` recovery command must be safely copy-pasteable even
        when the repo or a colliding filename contains a space — unquoted paths
        would not execute as shown and could trigger unintended shell
        expansion."""
        repo_root = tmp_path / "my repo"
        settings = repo_root / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")

        settings_backup = repo_root / ".wade" / "pull-backups" / ".claude" / "settings.json"

        real_move = shutil.move

        def flaky_move(src: str, dst: str) -> object:
            if str(src) == str(settings_backup):
                raise OSError("simulated restore failure")
            return real_move(src, dst)

        stderr = (
            "error: The following untracked working tree files would be overwritten by merge:\n"
            "\t.claude/settings.json\n"
            "Please move or remove them before you merge.\n"
        )
        untracked_fail = MagicMock(returncode=1, stderr=stderr)
        unknown_fail = MagicMock(returncode=1, stderr="fatal: unable to access remote\n")

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                side_effect=[untracked_fail, unknown_fail],
            ),
            patch(
                "wade.services.implementation_service.lifecycle.shutil.move",
                side_effect=flaky_move,
            ),
            patch("wade.services.implementation_service.lifecycle.console") as mock_console,
        ):
            _pull_main_after_merge(repo_root)

        hint_calls = [c.args[0] for c in mock_console.hint.call_args_list]
        expected = f"mv {shlex.quote(str(settings_backup))} {shlex.quote(str(settings))}"
        assert any(expected in msg for msg in hint_calls)

    def test_no_movable_files_breaks_and_warns(self, tmp_path: Path) -> None:
        """An untracked error whose colliding files are all already gone makes no
        progress: the loop breaks immediately (no retry) and warns — covering the
        no-progress early exit, distinct from cap exhaustion."""
        untracked_fail = MagicMock(returncode=1, stderr=UNTRACKED_STDERR)

        with (
            patch(
                "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
                side_effect=[untracked_fail],
            ) as mock_pull,
            patch("wade.services.implementation_service.lifecycle.console") as mock_console,
        ):
            _pull_main_after_merge(tmp_path)

        # No retry — the entry pull is the only call.
        assert mock_pull.call_count == 1
        warn_calls = [c.args[0] for c in mock_console.warn.call_args_list]
        assert any("Could not sync local main" in msg for msg in warn_calls)


class TestCapturePostSessionUsage:
    def test_session_only_updates_session_blocks_without_impl_usage(self, tmp_path: Path) -> None:
        """Session-only transcript data should still be persisted to PR/issue bodies."""
        transcript = tmp_path / ".transcript"
        transcript.write_text("resume me\n")

        adapter = MagicMock()
        adapter.parse_transcript.return_value = TokenUsage(session_id="session-abc-123")

        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test issue", body="Issue body\n")

        with (
            patch(
                "wade.services.implementation_service.core.git_pr.get_pr_for_branch",
                return_value=PRLookup(found=True, pr=PRRef(number=7, state="OPEN")),
            ),
            patch(
                "wade.services.implementation_service.core.git_pr.get_pr_body",
                return_value="PR body\n",
            ),
            patch(
                "wade.services.implementation_service.core.git_pr.update_pr_body",
                return_value=True,
            ) as mock_update_pr,
            patch("wade.services.implementation_service.core.console") as mock_console,
        ):
            model = _capture_post_session_usage(
                transcript_path=transcript,
                adapter=adapter,
                repo_root=tmp_path,
                branch="feat/42-test",
                ai_tool="claude",
                model=None,
                issue_number="42",
                provider=provider,
            )

        assert model is None
        mock_console.warn.assert_not_called()

        updated_pr_body = mock_update_pr.call_args.args[2]
        assert "wade:sessions:start" in updated_pr_body
        assert "session-abc-123" in updated_pr_body
        assert "wade:impl-usage:start" not in updated_pr_body

        provider.update_task.assert_called_once()
        updated_issue_body = provider.update_task.call_args.kwargs["body"]
        assert "wade:sessions:start" in updated_issue_body
        assert "session-abc-123" in updated_issue_body
        assert "wade:impl-usage:start" not in updated_issue_body

    def test_breakdown_only_usage_still_updates_impl_usage_blocks(self, tmp_path: Path) -> None:
        """Per-model-only usage data should still be persisted to PR and issue bodies."""
        transcript = tmp_path / ".transcript"
        transcript.write_text("resume me\n")

        adapter = MagicMock()
        adapter.parse_transcript.return_value = TokenUsage(
            model_breakdown=[
                ModelBreakdown(
                    model="claude-sonnet-4-6",
                    input_tokens=120,
                    output_tokens=30,
                    cached_tokens=0,
                )
            ]
        )

        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test issue", body="Issue body\n")

        with (
            patch(
                "wade.services.implementation_service.core.git_pr.get_pr_for_branch",
                return_value=PRLookup(found=True, pr=PRRef(number=7, state="OPEN")),
            ),
            patch(
                "wade.services.implementation_service.core.git_pr.get_pr_body",
                return_value="PR body\n",
            ),
            patch(
                "wade.services.implementation_service.core.git_pr.update_pr_body",
                return_value=True,
            ) as mock_update_pr,
            patch("wade.services.implementation_service.core.console") as mock_console,
        ):
            model = _capture_post_session_usage(
                transcript_path=transcript,
                adapter=adapter,
                repo_root=tmp_path,
                branch="feat/42-test",
                ai_tool="claude",
                model=None,
                issue_number="42",
                provider=provider,
            )

        assert model == "claude-sonnet-4-6"
        mock_console.warn.assert_not_called()

        updated_pr_body = mock_update_pr.call_args.args[2]
        assert "wade:impl-usage:start" in updated_pr_body
        assert "**150**" in updated_pr_body
        assert "**0**" in updated_pr_body

        provider.update_task.assert_called_once()
        updated_issue_body = provider.update_task.call_args.kwargs["body"]
        assert "wade:impl-usage:start" in updated_issue_body
        assert "**150**" in updated_issue_body

    def test_premium_only_usage_still_updates_impl_usage_blocks(self, tmp_path: Path) -> None:
        """Premium-only transcript data should still be persisted to PR and issue bodies."""
        transcript = tmp_path / ".transcript"
        transcript.write_text("resume me\n")

        adapter = MagicMock()
        adapter.parse_transcript.return_value = TokenUsage(premium_requests=2)

        provider = MagicMock()
        provider.read_task.return_value = Task(id="42", title="Test issue", body="Issue body\n")

        with (
            patch(
                "wade.services.implementation_service.core.git_pr.get_pr_for_branch",
                return_value=PRLookup(found=True, pr=PRRef(number=7, state="OPEN")),
            ),
            patch(
                "wade.services.implementation_service.core.git_pr.get_pr_body",
                return_value="PR body\n",
            ),
            patch(
                "wade.services.implementation_service.core.git_pr.update_pr_body",
                return_value=True,
            ) as mock_update_pr,
            patch("wade.services.implementation_service.core.console") as mock_console,
        ):
            model = _capture_post_session_usage(
                transcript_path=transcript,
                adapter=adapter,
                repo_root=tmp_path,
                branch="feat/42-test",
                ai_tool="claude",
                model=None,
                issue_number="42",
                provider=provider,
            )

        assert model is None
        mock_console.warn.assert_not_called()

        updated_pr_body = mock_update_pr.call_args.args[2]
        assert "wade:impl-usage:start" in updated_pr_body
        assert "| Premium requests (est.) | **2** |" in updated_pr_body
        assert "| Total tokens | *unavailable* |" not in updated_pr_body

        provider.update_task.assert_called_once()
        updated_issue_body = provider.update_task.call_args.kwargs["body"]
        assert "wade:impl-usage:start" in updated_issue_body
        assert "| Premium requests (est.) | **2** |" in updated_issue_body


# ---------------------------------------------------------------------------
# Tracking issue detection in start()
# ---------------------------------------------------------------------------


class TestStartTrackingDetection:
    """Tests for tracking issue detection in start()."""

    def _tracking_task(self) -> Task:
        return Task(
            id="173",
            title="Tracking: #167, #169, #171",
            body="- [ ] #167\n- [ ] #169\n- [x] #171\n",
        )

    def _make_config(self) -> ProjectConfig:
        return ProjectConfig(project=ProjectSettings(main_branch="main"))

    def test_tracking_issue_redirects_to_batch(self, tmp_path: Path) -> None:
        """start() on a tracking issue with confirmed batch → calls batch()."""
        task = self._tracking_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
            patch(
                "wade.services.implementation_service.batch.check_tracking_issue_and_batch"
            ) as mock_batch,
        ):
            mock_prompts.confirm.return_value = True
            mock_batch.return_value = True
            result = start("173", project_root=tmp_path)

        assert result.success is True
        mock_batch.assert_called_once()
        call_kwargs = mock_batch.call_args
        assert call_kwargs.args[0].id == "173"  # task passed to check_tracking_issue_and_batch

    def test_tracking_issue_declined_returns_false(self, tmp_path: Path) -> None:
        """start() on a tracking issue with declined batch → returns False."""
        task = self._tracking_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
            patch(
                "wade.services.implementation_service.batch.check_tracking_issue_and_batch"
            ) as mock_batch,
        ):
            mock_batch.return_value = False  # User declined batch
            mock_prompts.confirm.return_value = False
            result = start("173", project_root=tmp_path)

        mock_batch.assert_called_once()
        assert result.success is False

    def test_tracking_issue_backticked_refs_redirects_to_batch(self, tmp_path: Path) -> None:
        """Checklist refs wrapped in backticks still trigger batch mode."""
        task = Task(
            id="173",
            title="Tracking: #167, #169, #171",
            body="- [ ] `#167`\n  - [ ] #169\n- [x] `#171`\n",
        )
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
            patch(
                "wade.services.implementation_service.batch.check_tracking_issue_and_batch"
            ) as mock_batch,
        ):
            mock_prompts.confirm.return_value = True
            mock_batch.return_value = True
            result = start("173", project_root=tmp_path)

        assert result.success is True
        mock_batch.assert_called_once()
        assert (
            mock_batch.call_args.args[0].id == "173"
        )  # task passed to check_tracking_issue_and_batch

    def test_regular_issue_not_affected(self, tmp_path: Path) -> None:
        """start() on a non-tracking issue proceeds normally (no batch redirect)."""
        task = Task(id="42", title="Add user auth")
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.git.worktree.list_worktrees", return_value=[]),
            patch("wade.git.worktree.create_worktree") as mock_create,
            patch("wade.services.implementation_service.core.write_plan_md"),
            patch("wade.services.implementation_service.core.bootstrap_worktree"),
            patch("crossby.ai_tools.base.AbstractAITool.detect_installed", return_value=[]),
            patch(
                "wade.services.implementation_service.core._detect_ai_cli_env", return_value=None
            ),
            patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup(found=False)),
            patch(
                "wade.services.implementation_service.core.bootstrap_draft_pr",
                return_value={"number": 1, "url": "http://test"},
            ),
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
            patch(
                "wade.services.implementation_service.batch.check_tracking_issue_and_batch"
            ) as mock_batch,
        ):
            mock_batch.return_value = None  # Not a tracking issue
            mock_prompts.is_tty.return_value = False
            result = start("42", project_root=tmp_path)

        assert result.success is True
        mock_batch.assert_called_once()
        mock_create.assert_called_once()

    def test_tracking_issue_forwards_ai_params(self, tmp_path: Path) -> None:
        """AI tool/model/effort/yolo parameters are forwarded to batch()."""
        task = self._tracking_task()
        mock_provider = MagicMock()
        mock_provider.read_task.return_value = task

        with (
            patch(
                "wade.services.implementation_service.core.load_config",
                return_value=self._make_config(),
            ),
            patch(
                "wade.services.implementation_service.core.get_provider", return_value=mock_provider
            ),
            patch("wade.git.repo.get_repo_root", return_value=tmp_path),
            patch("wade.services.implementation_service.core.prompts") as mock_prompts,
            patch(
                "wade.services.implementation_service.batch.check_tracking_issue_and_batch"
            ) as mock_batch,
        ):
            mock_prompts.confirm.return_value = True
            mock_batch.return_value = True
            start(
                "173",
                ai_tool="claude",
                model="opus",
                effort="high",
                project_root=tmp_path,
                ai_explicit=True,
                model_explicit=True,
                effort_explicit=True,
                yolo=True,
            )

        call_kwargs = mock_batch.call_args.kwargs
        assert call_kwargs["ai_tool"] == "claude"
        assert call_kwargs["model"] == "opus"
        assert call_kwargs["effort"] == "high"
        assert call_kwargs["ai_explicit"] is True
        assert call_kwargs["model_explicit"] is True
        assert call_kwargs["effort_explicit"] is True
        assert call_kwargs["yolo"] is True


# ---------------------------------------------------------------------------
# ImplementResult tests
# ---------------------------------------------------------------------------


class TestImplementResult:
    """Tests for the ImplementResult Pydantic model."""

    def test_defaults(self) -> None:
        result = ImplementResult(success=True)
        assert result.success is True
        assert result.merged is False

    def test_success_and_merged(self) -> None:
        result = ImplementResult(success=True, merged=True)
        assert result.success is True
        assert result.merged is True

    def test_failure(self) -> None:
        result = ImplementResult(success=False)
        assert result.success is False
        assert result.merged is False

    def test_failure_merged_ignored(self) -> None:
        """Even with merged=True, a failed result is still failed."""
        result = ImplementResult(success=False, merged=True)
        assert result.success is False
        assert result.merged is True


# ---------------------------------------------------------------------------
# Post-implementation lifecycle tests
# ---------------------------------------------------------------------------


class TestPostImplementationLifecyclePr:
    """Tests for _post_implementation_lifecycle_pr — merged status propagation."""

    def test_non_tty_returns_not_merged_without_browser_or_merge(self, tmp_path: Path) -> None:
        """Non-interactive runs should never auto-open or auto-merge a PR."""
        mock_provider = MagicMock()
        with (
            patch(
                "wade.git.pr.get_pr_for_branch",
                return_value=PRLookup(
                    found=True, pr=PRRef(number=10, url="http://test", state="OPEN")
                ),
            ),
            patch("wade.services.implementation_service.lifecycle.prompts") as mock_prompts,
            patch("wade.services.implementation_service.lifecycle.webbrowser.open") as mock_open,
            patch("wade.services.implementation_service.lifecycle._merge_pr") as mock_merge,
            patch("wade.services.review_service.poll_for_reviews") as mock_poll,
        ):
            mock_prompts.is_tty.return_value = False
            result = _post_implementation_lifecycle_pr(
                tmp_path, "feat/42", "42", tmp_path / "wt", mock_provider
            )

        assert result == MergeStatus.NOT_MERGED
        mock_open.assert_not_called()
        mock_merge.assert_not_called()
        mock_poll.assert_not_called()

    def test_merge_pr_returns_merged(self, tmp_path: Path) -> None:
        """User chooses 'Merge PR' → returns MERGED."""
        mock_provider = MagicMock()
        with (
            patch(
                "wade.git.pr.get_pr_for_branch",
                return_value=PRLookup(
                    found=True, pr=PRRef(number=10, url="http://test", state="OPEN")
                ),
            ),
            patch("wade.services.implementation_service.lifecycle.prompts") as mock_prompts,
            patch(
                "wade.services.implementation_service.lifecycle._merge_pr",
                return_value=MergeStatus.MERGED,
            ),
        ):
            mock_prompts.is_tty.return_value = True
            mock_prompts.confirm.return_value = False  # Don't open in browser
            mock_prompts.select.return_value = 0  # "Merge PR"
            result = _post_implementation_lifecycle_pr(
                tmp_path, "feat/42", "42", tmp_path / "wt", mock_provider
            )
        assert result == MergeStatus.MERGED

    def test_wait_for_reviews_returns_not_merged(self, tmp_path: Path) -> None:
        """User chooses 'Wait for reviews' → returns NOT_MERGED."""
        from wade.models.review import PollOutcome

        mock_provider = MagicMock()
        with (
            patch(
                "wade.git.pr.get_pr_for_branch",
                return_value=PRLookup(
                    found=True, pr=PRRef(number=10, url="http://test", state="OPEN")
                ),
            ),
            patch("wade.services.implementation_service.lifecycle.prompts") as mock_prompts,
            patch(
                "wade.services.review_service.poll_for_reviews",
                return_value=PollOutcome.INTERRUPTED,
            ),
        ):
            mock_prompts.is_tty.return_value = True
            mock_prompts.confirm.return_value = False
            mock_prompts.select.return_value = 1  # "Wait for reviews"
            result = _post_implementation_lifecycle_pr(
                tmp_path, "feat/42", "42", tmp_path / "wt", mock_provider
            )
        assert result == MergeStatus.NOT_MERGED

    def test_wait_for_reviews_comments_found_preserves_review_context(self, tmp_path: Path) -> None:
        """Polling into review mode should preserve the resolved implementation context."""
        from wade.models.review import PollOutcome

        mock_provider = MagicMock()
        with (
            patch(
                "wade.git.pr.get_pr_for_branch",
                return_value=PRLookup(
                    found=True, pr=PRRef(number=10, url="http://test", state="OPEN")
                ),
            ),
            patch("wade.services.implementation_service.lifecycle.prompts") as mock_prompts,
            patch(
                "wade.services.review_service.poll_for_reviews",
                return_value=PollOutcome.COMMENTS_FOUND,
            ),
            patch("wade.services.review_service.start") as mock_review_start,
        ):
            mock_prompts.is_tty.return_value = True
            mock_prompts.confirm.return_value = False
            mock_prompts.select.return_value = 1  # "Wait for reviews"
            result = _post_implementation_lifecycle_pr(
                tmp_path,
                "feat/42",
                "42",
                tmp_path / "wt",
                mock_provider,
                ai_tool="claude",
                model="claude-sonnet-4-5",
                detach=True,
                ai_explicit=True,
                model_explicit=True,
                permission_mode="yolo",
                network_access=False,
            )

        assert result == MergeStatus.NOT_MERGED
        # An explicit --no-network pin survives into the follow-on review session
        # rather than being silently re-resolved from ai.review_pr_comments config.
        mock_review_start.assert_called_once_with(
            "42",
            ai_tool="claude",
            model="claude-sonnet-4-5",
            project_root=tmp_path,
            detach=True,
            ai_explicit=True,
            model_explicit=True,
            permission_mode="yolo",
            permission_mode_explicit=False,
            network_access=False,
        )

    def test_wait_for_reviews_quiet_timeout_preserves_review_context(self, tmp_path: Path) -> None:
        """Quiet timeout should forward the original implementation context to review UX."""
        from wade.models.review import PollOutcome

        mock_provider = MagicMock()
        with (
            patch(
                "wade.git.pr.get_pr_for_branch",
                return_value=PRLookup(
                    found=True, pr=PRRef(number=10, url="http://test", state="OPEN")
                ),
            ),
            patch("wade.services.implementation_service.lifecycle.prompts") as mock_prompts,
            patch(
                "wade.services.review_service.poll_for_reviews",
                return_value=PollOutcome.QUIET_TIMEOUT,
            ),
            patch("wade.services.review_service._quiet_next_steps_prompt") as mock_quiet,
        ):
            mock_prompts.is_tty.return_value = True
            mock_prompts.confirm.return_value = False
            mock_prompts.select.return_value = 1  # "Wait for reviews"
            result = _post_implementation_lifecycle_pr(
                tmp_path,
                "feat/42",
                "42",
                tmp_path / "wt",
                mock_provider,
                ai_tool="claude",
                model="claude-sonnet-4-5",
                detach=True,
                ai_explicit=True,
                model_explicit=True,
                permission_mode="yolo",
                network_access=True,
            )

        assert result == MergeStatus.NOT_MERGED
        # An explicit --network pin survives into the quiet-timeout re-launch path
        # rather than being silently re-resolved from ai.review_pr_comments config.
        mock_quiet.assert_called_once_with(
            tmp_path,
            "feat/42",
            "42",
            tmp_path / "wt",
            10,
            mock_provider,
            ai_tool="claude",
            model="claude-sonnet-4-5",
            detach=True,
            ai_explicit=True,
            model_explicit=True,
            permission_mode="yolo",
            permission_mode_explicit=False,
            network_access=True,
        )

    def test_no_pr_found_returns_not_merged(self, tmp_path: Path) -> None:
        """No open PR → returns NOT_MERGED."""
        mock_provider = MagicMock()
        with patch("wade.git.pr.get_pr_for_branch", return_value=PRLookup(found=False)):
            result = _post_implementation_lifecycle_pr(
                tmp_path, "feat/42", "42", tmp_path / "wt", mock_provider
            )
        assert result == MergeStatus.NOT_MERGED


# ---------------------------------------------------------------------------
# Batch --chain flag tests
# ---------------------------------------------------------------------------


class TestBatchChainFlag:
    """Tests for batch() --chain flag propagation."""

    def _chain_patches(self, tmp_path: Path, **overrides: object):  # type: ignore[no-untyped-def]
        """Common patches for chain flag tests (prevents real polling)."""
        from contextlib import ExitStack

        defaults = {
            "wade.services.implementation_service.core.load_config": ProjectConfig(),
            "wade.git.repo.get_repo_root": tmp_path,
            "wade.services.implementation_service.batch._build_graph_from_issues": None,
            "wade.services.implementation_service.batch.launch_batch_in_terminals": True,
            "wade.services.implementation_service.batch._find_tracking_issue": None,
            "wade.services.implementation_service.batch.poll_batch_completion": None,
        }
        defaults.update(overrides)
        stack = ExitStack()
        mocks = {}
        for target, rv in defaults.items():
            m = stack.enter_context(patch(target, return_value=rv))
            mocks[target.rsplit(".", 1)[-1]] = m
        return stack, mocks

    def test_chain_flag_appended_to_first_in_chain(self, tmp_path: Path) -> None:
        """First issue in a dependency chain gets --chain with remaining IDs."""
        mock_graph = MagicMock()
        mock_graph.edges = [MagicMock()]
        mock_graph.partition.return_value = ([], [["1", "2", "3"]])

        stack, mocks = self._chain_patches(
            tmp_path,
            **{"wade.services.implementation_service.batch._build_graph_from_issues": mock_graph},
        )
        with stack:
            batch(["1", "2", "3"], project_root=tmp_path)

        items = mocks["launch_batch_in_terminals"].call_args[0][0]
        assert len(items) == 1
        cmd = items[0][0]
        assert "--chain" in cmd
        chain_idx = cmd.index("--chain")
        assert cmd[chain_idx + 1] == "2,3"

    def test_single_item_chain_has_no_chain_flag(self, tmp_path: Path) -> None:
        """A chain with only one item does not get --chain."""
        mock_graph = MagicMock()
        mock_graph.edges = [MagicMock()]
        mock_graph.partition.return_value = ([], [["1"]])

        stack, mocks = self._chain_patches(
            tmp_path,
            **{"wade.services.implementation_service.batch._build_graph_from_issues": mock_graph},
        )
        with stack:
            batch(["1"], project_root=tmp_path)

        items = mocks["launch_batch_in_terminals"].call_args[0][0]
        cmd = items[0][0]
        assert "--chain" not in cmd

    def test_independent_issues_no_chain_flag(self, tmp_path: Path) -> None:
        """Independent issues (no deps) do not get --chain."""
        stack, mocks = self._chain_patches(tmp_path)
        with stack:
            batch(["1", "2"], project_root=tmp_path)

        items = mocks["launch_batch_in_terminals"].call_args[0][0]
        for item in items:
            assert "--chain" not in item[0]


# ---------------------------------------------------------------------------
# CLI --chain continuation tests
# ---------------------------------------------------------------------------


class TestChainContinuation:
    """Tests for the --chain continuation loop in implement_cmd."""

    def test_chain_continues_on_confirm(self) -> None:
        """When user confirms, next issue in chain starts with stacked base."""
        from typer.testing import CliRunner

        from wade.cli.main import app

        runner = CliRunner()
        calls: list[dict[str, object]] = []

        def fake_start(**kwargs: object) -> ImplementResult:
            calls.append(kwargs)
            return ImplementResult(
                success=True, merged=False, branch_name=f"feat/{len(calls)}-branch"
            )

        with (
            patch("wade.services.implementation_service.start", side_effect=fake_start),
            patch("wade.ui.prompts.confirm", return_value=True),
            patch("wade.ui.prompts.select", return_value=0),
        ):
            result = runner.invoke(app, ["implement", "1", "--chain", "2,3"])

        assert result.exit_code == 0
        assert len(calls) == 3  # Issues 1, 2, 3
        # Second call should have base_branch from first call's branch_name
        assert calls[1]["base_branch"] == "feat/1-branch"
        assert calls[2]["base_branch"] == "feat/2-branch"

    def test_chain_continues_without_merge_gate(self) -> None:
        """Chain continues even when merged=False (stacked branches)."""
        from typer.testing import CliRunner

        from wade.cli.main import app

        runner = CliRunner()
        call_count = 0

        def fake_start(**kwargs: object) -> ImplementResult:
            nonlocal call_count
            call_count += 1
            return ImplementResult(success=True, merged=False, branch_name=f"feat/{call_count}-x")

        with (
            patch("wade.services.implementation_service.start", side_effect=fake_start),
            patch("wade.ui.prompts.confirm", return_value=True),
            patch("wade.ui.prompts.select", return_value=0),
        ):
            result = runner.invoke(app, ["implement", "1", "--chain", "2,3"])

        assert result.exit_code == 0
        assert call_count == 3  # No merge gate — all three run

    def test_chain_stops_on_decline(self) -> None:
        """When user declines, chain stops with resume hint including --base."""
        from typer.testing import CliRunner

        from wade.cli.main import app

        runner = CliRunner()

        with (
            patch(
                "wade.services.implementation_service.start",
                return_value=ImplementResult(
                    success=True, merged=False, branch_name="feat/1-my-branch"
                ),
            ),
            patch("wade.ui.prompts.confirm", return_value=False),
            patch("wade.ui.prompts.select", return_value=0),
        ):
            result = runner.invoke(app, ["implement", "1", "--chain", "2,3"])

        assert result.exit_code == 0
        output_lower = result.output.lower()
        assert "resume" in output_lower or "wade implement" in output_lower
        assert "--base" in result.output

    def test_empty_chain_runs_single_issue(self) -> None:
        """No --chain flag → runs single issue, no continuation."""
        from typer.testing import CliRunner

        from wade.cli.main import app

        runner = CliRunner()
        call_count = 0

        def fake_start(**kwargs: object) -> ImplementResult:
            nonlocal call_count
            call_count += 1
            return ImplementResult(success=True, merged=True)

        with (
            patch("wade.services.implementation_service.start", side_effect=fake_start),
            patch("wade.ui.prompts.select", return_value=0),
        ):
            result = runner.invoke(app, ["implement", "1"])

        assert result.exit_code == 0
        assert call_count == 1

    def test_chain_stops_on_failure(self) -> None:
        """When start returns success=False, chain exits immediately with code 1."""
        from typer.testing import CliRunner

        from wade.cli.main import app

        runner = CliRunner()
        call_count = 0

        def fake_start(**kwargs: object) -> ImplementResult:
            nonlocal call_count
            call_count += 1
            return ImplementResult(success=False, merged=False)

        with (
            patch("wade.services.implementation_service.start", side_effect=fake_start),
            patch("wade.ui.prompts.select", return_value=0),
        ):
            result = runner.invoke(app, ["implement", "1", "--chain", "2,3"])

        assert result.exit_code == 1
        assert call_count == 1  # No continuation after failure


class TestCarryForwardPendingVotes:
    """Ratings-only carry-forward (#358): a throwaway `rate` on main is flushed
    into the next attached worktree's log, and main is restored to clean."""

    @staticmethod
    def _git(cwd: Path, *args: str) -> None:
        import subprocess

        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def _make_main_with_committed_ratings(self, tmp_path: Path) -> Path:
        main = tmp_path / "main"
        main.mkdir()
        self._git(main, "init", "-b", "main")
        self._git(main, "config", "user.email", "t@t.com")
        self._git(main, "config", "user.name", "t")
        (main / "KNOWLEDGE.md").write_text("# Project Knowledge\n\n", encoding="utf-8")
        (main / "KNOWLEDGE.ratings.jsonl").write_text(
            '{"dir": "up", "id": "e", "ts": "committed"}\n', encoding="utf-8"
        )
        self._git(main, "add", "-A")
        self._git(main, "commit", "-m", "chore: init")
        return main

    def test_carries_pending_vote_and_cleans_main(self, tmp_path: Path) -> None:
        from wade.models.config import KnowledgeConfig, ProjectConfig
        from wade.services.implementation_service.bootstrap import _carry_forward_pending_votes

        main = self._make_main_with_committed_ratings(tmp_path)
        ratings = main / "KNOWLEDGE.ratings.jsonl"
        # A throwaway `rate` appended one uncommitted vote line to main's working copy.
        pending_line = '{"dir": "down", "id": "e", "ts": "pending"}'
        ratings.write_text(ratings.read_text() + pending_line + "\n", encoding="utf-8")

        worktree = tmp_path / "wt"
        self._git(main, "worktree", "add", "-b", "feat/1", str(worktree))

        config = ProjectConfig(knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"))
        _carry_forward_pending_votes(worktree, main, config)

        # Pending vote moved into the worktree's log (rides into that branch's PR).
        assert pending_line in (worktree / "KNOWLEDGE.ratings.jsonl").read_text(encoding="utf-8")
        # Main restored to its committed state — the pending line is gone.
        assert pending_line not in ratings.read_text(encoding="utf-8")

    def test_second_carry_is_a_noop(self, tmp_path: Path) -> None:
        # Serialized by the lock: once the first carry clears main, later bootstraps
        # see a clean main and do not double-carry.
        from wade.models.config import KnowledgeConfig, ProjectConfig
        from wade.services.implementation_service.bootstrap import _carry_forward_pending_votes

        main = self._make_main_with_committed_ratings(tmp_path)
        ratings = main / "KNOWLEDGE.ratings.jsonl"
        pending_line = '{"dir": "down", "id": "e", "ts": "pending"}'
        ratings.write_text(ratings.read_text() + pending_line + "\n", encoding="utf-8")

        config = ProjectConfig(knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"))

        wt1 = tmp_path / "wt1"
        self._git(main, "worktree", "add", "-b", "feat/1", str(wt1))
        _carry_forward_pending_votes(wt1, main, config)

        wt2 = tmp_path / "wt2"
        self._git(main, "worktree", "add", "-b", "feat/2", str(wt2))
        _carry_forward_pending_votes(wt2, main, config)

        # Only the first worktree got the vote; the second saw a clean main.
        wt1_text = (wt1 / "KNOWLEDGE.ratings.jsonl").read_text(encoding="utf-8")
        wt2_text = (wt2 / "KNOWLEDGE.ratings.jsonl").read_text(encoding="utf-8")
        assert wt1_text.count(pending_line) == 1
        assert wt2_text.count(pending_line) == 0

    def test_carries_verified_untracked_staged_vote_spool(self, tmp_path: Path) -> None:
        """A first detached vote reaches an attached PR even with no prior sidecar."""
        from wade.models.config import KnowledgeConfig, ProjectConfig
        from wade.services.implementation_service.bootstrap import _carry_forward_pending_votes

        main = tmp_path / "main"
        main.mkdir()
        self._git(main, "init", "-b", "main")
        self._git(main, "config", "user.email", "t@t.com")
        self._git(main, "config", "user.name", "t")
        (main / "KNOWLEDGE.md").write_text("# Project Knowledge\n\n", encoding="utf-8")
        self._git(main, "add", "KNOWLEDGE.md")
        self._git(main, "commit", "-m", "chore: init knowledge")
        pending_line = '{"dir": "up", "event_id": "detached-event", "id": "e", "ts": "pending"}'
        (main / "KNOWLEDGE.ratings.jsonl").write_text(pending_line + "\n", encoding="utf-8")
        worktree = tmp_path / "wt"
        self._git(main, "worktree", "add", "-b", "feat/first-rating", str(worktree))

        config = ProjectConfig(knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"))
        _carry_forward_pending_votes(worktree, main, config)

        assert pending_line in (worktree / "KNOWLEDGE.ratings.jsonl").read_text(encoding="utf-8")
        assert not (main / "KNOWLEDGE.ratings.jsonl").exists()

    def test_carries_staged_vote_through_legacy_ratings_migration(self, tmp_path: Path) -> None:
        """The first detached vote migrates legacy YAML in the attached PR, not main."""
        from wade.models.config import KnowledgeConfig, ProjectConfig
        from wade.services.implementation_service.bootstrap import _carry_forward_pending_votes
        from wade.services.knowledge_service import (
            create_rating_event,
            flush_staged_ratings,
            read_ratings,
            stage_rating_event,
        )

        main = tmp_path / "main"
        plan_worktree = tmp_path / "plan-worktree"
        main.mkdir()
        plan_worktree.mkdir()
        self._git(main, "init", "-b", "main")
        self._git(main, "config", "user.email", "t@t.com")
        self._git(main, "config", "user.name", "t")
        (main / "KNOWLEDGE.md").write_text("# Project Knowledge\n\n", encoding="utf-8")
        (main / "KNOWLEDGE.ratings.yml").write_text("e: {up: 2}\n", encoding="utf-8")
        self._git(main, "add", "-A")
        self._git(main, "commit", "-m", "chore: init legacy knowledge")
        config = ProjectConfig(knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"))
        stage_rating_event(plan_worktree, create_rating_event("e", "up"))

        handoff = flush_staged_ratings(plan_worktree, main, config.knowledge)
        assert handoff.success
        assert not (main / "KNOWLEDGE.ratings.yml").exists()

        worktree = tmp_path / "wt"
        self._git(main, "worktree", "add", "-b", "feat/legacy-rating", str(worktree))
        _carry_forward_pending_votes(worktree, main, config)

        assert (main / "KNOWLEDGE.ratings.yml").is_file()
        assert not (main / "KNOWLEDGE.ratings.jsonl").exists()
        assert not (worktree / "KNOWLEDGE.ratings.yml").exists()
        assert read_ratings(worktree / "KNOWLEDGE.ratings.jsonl")["e"].up == 3

    def test_failed_main_restore_carries_nothing(self, tmp_path: Path) -> None:
        # If restoring main fails, the votes must NOT be carried into the worktree —
        # otherwise they stay in main and get re-carried into a second worktree,
        # double-counting. They remain in main for a later bootstrap to retry.
        from unittest.mock import patch

        from wade.models.config import KnowledgeConfig, ProjectConfig
        from wade.services.implementation_service.bootstrap import _carry_forward_pending_votes

        main = self._make_main_with_committed_ratings(tmp_path)
        ratings = main / "KNOWLEDGE.ratings.jsonl"
        pending_line = '{"dir": "down", "id": "e", "ts": "pending"}'
        ratings.write_text(ratings.read_text() + pending_line + "\n", encoding="utf-8")

        worktree = tmp_path / "wt"
        self._git(main, "worktree", "add", "-b", "feat/1", str(worktree))
        config = ProjectConfig(knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"))

        with patch("wade.git.repo.checkout_paths", return_value=False):
            _carry_forward_pending_votes(worktree, main, config)

        # The pending vote was NOT carried into the worktree (its ratings stays the
        # committed version), and it remains in main for a later bootstrap to retry.
        wt_text = (worktree / "KNOWLEDGE.ratings.jsonl").read_text(encoding="utf-8")
        assert pending_line not in wt_text
        assert pending_line in ratings.read_text(encoding="utf-8")

    def test_failed_worktree_transfer_rolls_back_main(self, tmp_path: Path) -> None:
        # If persisting the votes into the worktree fails AFTER main is reset to HEAD,
        # main must be rolled back to its snapshot so the pending votes survive for a
        # later bootstrap — otherwise they'd be lost from BOTH locations.
        from wade.models.config import KnowledgeConfig, ProjectConfig
        from wade.services.implementation_service.bootstrap import _carry_forward_pending_votes

        main = self._make_main_with_committed_ratings(tmp_path)
        ratings = main / "KNOWLEDGE.ratings.jsonl"
        pending_line = '{"dir": "down", "id": "e", "ts": "pending"}'
        ratings.write_text(ratings.read_text() + pending_line + "\n", encoding="utf-8")

        worktree = tmp_path / "wt"
        self._git(main, "worktree", "add", "-b", "feat/1", str(worktree))
        config = ProjectConfig(knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"))

        # Force the worktree write to fail mid-transfer: replace the checked-out ratings
        # file with a directory so the append raises OSError (IsADirectoryError).
        wt_ratings = worktree / "KNOWLEDGE.ratings.jsonl"
        wt_ratings.unlink()
        wt_ratings.mkdir()

        _carry_forward_pending_votes(worktree, main, config)

        # main is rolled back to its snapshot — the pending vote survives for a retry.
        assert pending_line in ratings.read_text(encoding="utf-8")

    def test_carries_pending_vote_identical_to_a_committed_line(self, tmp_path: Path) -> None:
        # Regression (#358 review): a genuinely-new vote whose serialized line is
        # IDENTICAL to a line already committed in the worktree must still be carried.
        # Deduping pending against the worktree's committed records dropped it here while
        # the main restore removed it too — losing the vote from both places.
        from wade.models.config import KnowledgeConfig, ProjectConfig
        from wade.services.implementation_service.bootstrap import _carry_forward_pending_votes

        main = self._make_main_with_committed_ratings(tmp_path)
        ratings = main / "KNOWLEDGE.ratings.jsonl"
        # A throwaway `rate` appended a vote whose serialized form equals the already
        # committed line (same dir/id/ts) — a distinct event that just serializes alike.
        committed_line = '{"dir": "up", "id": "e", "ts": "committed"}'
        ratings.write_text(ratings.read_text() + committed_line + "\n", encoding="utf-8")

        worktree = tmp_path / "wt"
        self._git(main, "worktree", "add", "-b", "feat/1", str(worktree))
        wt_ratings = worktree / "KNOWLEDGE.ratings.jsonl"
        # The worktree checkout already carries the committed copy of that line.
        assert wt_ratings.read_text(encoding="utf-8").count(committed_line) == 1

        config = ProjectConfig(knowledge=KnowledgeConfig(enabled=True, path="KNOWLEDGE.md"))
        _carry_forward_pending_votes(worktree, main, config)

        # Both events survive checkout: the worktree's committed copy PLUS the carried one.
        assert wt_ratings.read_text(encoding="utf-8").count(committed_line) == 2
        # Main is restored to its single committed line (the pending duplicate removed).
        assert ratings.read_text(encoding="utf-8").count(committed_line) == 1
