"""Tests for implementation service — start, batch, bootstrap, cd."""

from __future__ import annotations

import json
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
from wade.services.implementation_service import (
    _BATCH_STATUS_DONE,
    _BATCH_STATUS_IN_PROGRESS,
    _BATCH_STATUS_MERGED,
    _BATCH_STATUS_NOT_STARTED,
    _BATCH_STATUS_UNKNOWN,
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
        plan.write_text("# New Feature\n\n## Tasks\n- Do stuff\n")

        provider = MagicMock()
        provider.create_task.return_value = Task(id="99", title="New Feature")
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
# Implementation start tests
# ---------------------------------------------------------------------------


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
                return_value=[{"path": str(existing_wt), "branch": branch_name}],
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

        with patch(
            "wade.services.implementation_service.lifecycle.git_repo.pull_ff_only",
            side_effect=[fail_result, ok_result],
        ):
            _pull_main_after_merge(tmp_path)

        # Original locations are cleared so the pull can proceed...
        assert not settings.exists()
        assert not managed.exists()
        # ...but the files are preserved in the backup dir, never destroyed.
        backup_root = tmp_path / ".wade" / "pull-backups"
        assert (backup_root / ".claude" / "settings.json").read_text() == "{}"
        assert (backup_root / ".wade-managed").read_text() == "# managed"

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
            )

        assert result == MergeStatus.NOT_MERGED
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
            )

        assert result == MergeStatus.NOT_MERGED
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
