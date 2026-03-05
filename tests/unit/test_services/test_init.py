"""Tests for init service — init, update, deinit lifecycle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from wade.models.ai import AIToolID
from wade.models.config import ComplexityModelMapping
from wade.services.init_service import (
    GITIGNORE_ENTRIES,
    GITIGNORE_MARKER_END,
    GITIGNORE_MARKER_START,
    MANIFEST_FILENAME,
    _clean_gitignore,
    _commit_wade_files,
    _configure_local_worktree,
    _ensure_gitignore,
    _prompt_command_overrides,
    _prompt_commit_or_local,
    _prompt_model_mapping,
    _prompt_project_settings,
    _read_manifest_version,
    _resolve_models,
    _select_ai_tool,
    _write_config,
    deinit,
    init,
    update,
)
from wade.skills.installer import get_templates_dir
from wade.skills.pointer import (
    MARKER_END,
    MARKER_START,
    ensure_pointer,
    extract_pointer_content,
    has_pointer,
    remove_pointer,
    write_pointer,
)

# ---------------------------------------------------------------------------
# Pointer tests
# ---------------------------------------------------------------------------


class TestPointer:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        write_pointer(target)
        assert target.is_file()
        content = target.read_text()
        assert MARKER_START in content
        assert MARKER_END in content
        assert "Git Workflow" in content

    def test_write_appends_to_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text("# My Project\n\nExisting content.\n")
        write_pointer(target)
        content = target.read_text()
        assert "# My Project" in content
        assert MARKER_START in content

    def test_has_pointer(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        assert not has_pointer(target)
        write_pointer(target)
        assert has_pointer(target)

    def test_extract_content(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        write_pointer(target)
        content = extract_pointer_content(target)
        assert content is not None
        assert "Git Workflow" in content

    def test_remove_marker_based(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text("# Project\n\nContent.\n")
        write_pointer(target)
        assert has_pointer(target)

        result = remove_pointer(target)
        assert result is True
        assert not has_pointer(target)
        # Original content preserved
        remaining = target.read_text()
        assert "# Project" in remaining

    def test_remove_old_style(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text(
            "# Project\n\n## Git Workflow\n\nSome workflow rules.\n\n## Other\n\nOther content.\n"
        )
        result = remove_pointer(target)
        assert result is True
        remaining = target.read_text()
        assert "## Other" in remaining
        assert "## Git Workflow" not in remaining

    def test_remove_empties_file(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        write_pointer(target)
        remove_pointer(target)
        assert not target.exists()

    def test_ensure_creates_agents_md(self, tmp_path: Path) -> None:
        result = ensure_pointer(tmp_path)
        assert result is not None
        assert "AGENTS.md" in result
        assert (tmp_path / "AGENTS.md").is_file()

    def test_ensure_prefers_agents_md(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Agents\n")
        (tmp_path / "CLAUDE.md").write_text("# Claude\n")
        result = ensure_pointer(tmp_path)
        assert "AGENTS.md" in result

    def test_ensure_refreshes_stale(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        # Write an old pointer
        target.write_text(f"{MARKER_START}\nOld content\n{MARKER_END}\n")
        result = ensure_pointer(tmp_path)
        assert result is not None
        # Should have been refreshed
        content = target.read_text()
        assert "Git Workflow" in content


# ---------------------------------------------------------------------------
# Skill installer tests
# ---------------------------------------------------------------------------


class TestSkillInstaller:
    def test_get_templates_dir(self) -> None:
        tdir = get_templates_dir()
        # Should exist (we created templates/ in the repo)
        assert tdir.is_dir()

    def test_install_copies_files(self, tmp_git_repo: Path) -> None:
        from wade.skills.installer import install_skills

        installed = install_skills(tmp_git_repo)
        assert len(installed) > 0

        # Check that cross-tool symlinks were created
        assert (tmp_git_repo / ".github" / "skills").exists()
        assert (tmp_git_repo / ".agents" / "skills").exists()
        assert (tmp_git_repo / ".gemini" / "skills").exists()

    def test_remove_skills(self, tmp_git_repo: Path) -> None:
        from wade.skills.installer import install_skills, remove_skills

        install_skills(tmp_git_repo)
        removed = remove_skills(tmp_git_repo)
        assert len(removed) > 0

        # Cross-tool symlinks should be gone
        assert not (tmp_git_repo / ".github" / "skills").exists()


# ---------------------------------------------------------------------------
# Gitignore block management tests
# ---------------------------------------------------------------------------


class TestGitignoreBlock:
    """Unit tests for _ensure_gitignore and _clean_gitignore."""

    # --- _ensure_gitignore ---

    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        _ensure_gitignore(tmp_path)
        gitignore = tmp_path / ".gitignore"
        assert gitignore.is_file()
        content = gitignore.read_text()
        assert GITIGNORE_MARKER_START in content
        assert GITIGNORE_MARKER_END in content
        for entry in GITIGNORE_ENTRIES:
            assert entry in content

    def test_appends_block_to_existing_file(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("__pycache__/\n*.py[cod]\n")
        _ensure_gitignore(tmp_path)
        content = gitignore.read_text()
        assert "__pycache__/" in content  # original preserved
        assert GITIGNORE_MARKER_START in content

    def test_idempotent_when_already_current(self, tmp_path: Path) -> None:
        _ensure_gitignore(tmp_path)
        mtime_after_first = (tmp_path / ".gitignore").stat().st_mtime
        _ensure_gitignore(tmp_path)
        mtime_after_second = (tmp_path / ".gitignore").stat().st_mtime
        assert mtime_after_first == mtime_after_second  # file not touched

    def test_replaces_stale_block(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(
            f"__pycache__/\n\n{GITIGNORE_MARKER_START}\nold-entry.txt\n{GITIGNORE_MARKER_END}\n"
        )
        _ensure_gitignore(tmp_path)
        content = gitignore.read_text()
        assert "old-entry.txt" not in content
        assert "__pycache__/" in content  # user content preserved
        for entry in GITIGNORE_ENTRIES:
            assert entry in content
        # Block appears exactly once
        assert content.count(GITIGNORE_MARKER_START) == 1

    def test_migrates_old_style_entries(self, tmp_path: Path) -> None:
        """Old-style entries (no markers) are cleaned up and replaced with block."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(
            "__pycache__/\n# wade managed files\n.wade-managed\n.issue-context.md\nPLAN.md\n"
        )
        _ensure_gitignore(tmp_path)
        content = gitignore.read_text()
        assert GITIGNORE_MARKER_START in content
        assert "__pycache__/" in content
        # Old comment removed; entries now inside the block
        assert content.count("PLAN.md") == 1
        assert ".issue-context.md" not in content
        assert "# wade managed files" not in content

    # --- _clean_gitignore ---

    def test_removes_marker_block(self, tmp_path: Path) -> None:
        _ensure_gitignore(tmp_path)
        _clean_gitignore(tmp_path)
        content = (tmp_path / ".gitignore").read_text()
        assert GITIGNORE_MARKER_START not in content
        assert GITIGNORE_MARKER_END not in content
        for entry in GITIGNORE_ENTRIES:
            assert entry not in content

    def test_preserves_user_content_on_clean(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("__pycache__/\n*.py[cod]\n")
        _ensure_gitignore(tmp_path)
        _clean_gitignore(tmp_path)
        content = (tmp_path / ".gitignore").read_text()
        assert "__pycache__/" in content
        assert "*.py[cod]" in content

    def test_backward_compat_removes_old_style_entries(self, tmp_path: Path) -> None:
        """Deinit on a project inited before markers were introduced."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(
            "__pycache__/\n# wade managed files\n.wade-managed\n.issue-context.md\nPLAN.md\n"
        )
        _clean_gitignore(tmp_path)
        content = (tmp_path / ".gitignore").read_text()
        assert "__pycache__/" in content
        assert ".wade-managed" not in content
        assert ".issue-context.md" not in content
        assert "PLAN.md" not in content
        assert "# wade managed files" not in content

    def test_clean_no_op_when_no_file(self, tmp_path: Path) -> None:
        _clean_gitignore(tmp_path)  # must not raise


# ---------------------------------------------------------------------------
# Init service tests
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_creates_config(self, tmp_git_repo: Path) -> None:
        success = init(project_root=tmp_git_repo, non_interactive=True)
        assert success

        config_path = tmp_git_repo / ".wade.yml"
        assert config_path.is_file()

        config = yaml.safe_load(config_path.read_text())
        assert config["version"] == 2
        assert config["project"]["merge_strategy"] == "PR"
        assert config["provider"]["name"] == "github"

    def test_init_creates_manifest(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        manifest = tmp_git_repo / MANIFEST_FILENAME
        assert manifest.is_file()
        content = manifest.read_text()
        assert ".wade.yml" in content

    def test_init_creates_gitignore_block(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        gitignore = tmp_git_repo / ".gitignore"
        assert gitignore.is_file()
        content = gitignore.read_text()
        assert GITIGNORE_MARKER_START in content
        assert GITIGNORE_MARKER_END in content
        for entry in GITIGNORE_ENTRIES:
            assert entry in content

    def test_init_creates_agents_pointer(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        assert (tmp_git_repo / "AGENTS.md").is_file()
        content = (tmp_git_repo / "AGENTS.md").read_text()
        assert "Git Workflow" in content

    def test_init_with_ai_tool(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, ai_tool="claude", non_interactive=True)
        config = yaml.safe_load((tmp_git_repo / ".wade.yml").read_text())
        assert config["ai"]["default_tool"] == "claude"

    def test_init_patches_existing_config(self, tmp_git_repo: Path) -> None:
        config_path = tmp_git_repo / ".wade.yml"
        config_path.write_text("version: 2\nproject:\n  issue_label: custom-label\n")

        init(project_root=tmp_git_repo, ai_tool="claude", non_interactive=True)

        config = yaml.safe_load(config_path.read_text())
        # Should preserve existing value
        assert config["project"]["issue_label"] == "custom-label"
        # Should add missing AI tool
        assert config["ai"]["default_tool"] == "claude"

    def test_init_not_in_git_repo(self, tmp_path: Path) -> None:
        success = init(project_root=tmp_path, non_interactive=True)
        assert not success

    def test_init_non_interactive_detects_main_branch(self, tmp_git_repo: Path) -> None:
        """Non-interactive init should auto-detect 'main' branch."""
        success = init(project_root=tmp_git_repo, non_interactive=True)
        assert success

        config = yaml.safe_load((tmp_git_repo / ".wade.yml").read_text())
        assert config["project"]["main_branch"] == "main"


# ---------------------------------------------------------------------------
# _select_ai_tool tests
# ---------------------------------------------------------------------------


class TestSelectAITool:
    def test_requested_tool_returned_directly(self) -> None:
        result = _select_ai_tool("claude", non_interactive=False)
        assert result == "claude"

    def test_unknown_tool_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown AI tool"):
            _select_ai_tool("unknown-tool", non_interactive=False)

    @patch("wade.services.init_service.AbstractAITool.detect_installed")
    def test_no_tools_returns_none(self, mock_detect: MagicMock) -> None:
        mock_detect.return_value = []
        result = _select_ai_tool(None, non_interactive=False)
        assert result is None

    @patch("wade.services.init_service.AbstractAITool.detect_installed")
    def test_single_tool_auto_selects(self, mock_detect: MagicMock) -> None:
        mock_detect.return_value = [AIToolID.CLAUDE]
        result = _select_ai_tool(None, non_interactive=False)
        assert result == "claude"

    @patch("wade.services.init_service.AbstractAITool.detect_installed")
    def test_multiple_tools_non_interactive_selects_first(self, mock_detect: MagicMock) -> None:
        mock_detect.return_value = [AIToolID.CLAUDE, AIToolID.COPILOT]
        result = _select_ai_tool(None, non_interactive=True)
        assert result == "claude"

    @patch("wade.ui.prompts.select")
    @patch("wade.services.init_service.AbstractAITool.detect_installed")
    def test_multiple_tools_interactive_selects_chosen(
        self, mock_detect: MagicMock, mock_select: MagicMock
    ) -> None:
        mock_detect.return_value = [AIToolID.CLAUDE, AIToolID.COPILOT]
        mock_select.return_value = 1  # copilot
        result = _select_ai_tool(None, non_interactive=False)
        assert result == "copilot"

    @patch("wade.ui.prompts.select")
    @patch("wade.services.init_service.AbstractAITool.detect_installed")
    def test_multiple_tools_skip_returns_none(
        self, mock_detect: MagicMock, mock_select: MagicMock
    ) -> None:
        mock_detect.return_value = [AIToolID.CLAUDE, AIToolID.COPILOT]
        mock_select.return_value = 2  # Skip (configure later)
        result = _select_ai_tool(None, non_interactive=False)
        assert result is None


# ---------------------------------------------------------------------------
# _resolve_models tests
# ---------------------------------------------------------------------------


class TestResolveModels:
    def test_no_tool_returns_empty(self) -> None:
        mapping = _resolve_models(None)
        assert mapping.easy is None

    def test_resolves_defaults(self) -> None:
        mapping = _resolve_models("claude")
        assert mapping.easy is not None
        assert mapping.complex is not None


# ---------------------------------------------------------------------------
# _prompt_project_settings tests
# ---------------------------------------------------------------------------


class TestPromptProjectSettings:
    def test_non_interactive_returns_defaults(self, tmp_git_repo: Path) -> None:
        result = _prompt_project_settings(tmp_git_repo, non_interactive=True)
        assert result["main_branch"] == "main"
        assert result["merge_strategy"] == "PR"
        assert result["branch_prefix"] == "feat"
        assert result["issue_label"] == "feature-plan"
        assert result["worktrees_dir"] == "../.worktrees"

    @patch("wade.ui.prompts.select")
    @patch("wade.ui.prompts.input_prompt")
    def test_interactive_uses_prompts(
        self, mock_input: MagicMock, mock_select: MagicMock, tmp_git_repo: Path
    ) -> None:
        # merge_strategy uses prompts.select (index 1 = "direct")
        mock_select.side_effect = [1]
        # branch_prefix, issue_label, worktrees_dir use input_prompt
        mock_input.side_effect = ["fix", "bug", "../worktrees"]
        result = _prompt_project_settings(tmp_git_repo, non_interactive=False)
        assert result["merge_strategy"] == "direct"
        assert result["branch_prefix"] == "fix"
        assert result["issue_label"] == "bug"
        assert result["worktrees_dir"] == "../worktrees"
        # main_branch is auto-detected, not prompted
        assert result["main_branch"] == "main"

    def test_detects_main_branch_from_git(self, tmp_git_repo: Path) -> None:
        result = _prompt_project_settings(tmp_git_repo, non_interactive=True)
        assert result["main_branch"] == "main"

    def test_fallback_main_branch_without_git(self, tmp_path: Path) -> None:
        """When not in a git repo, should fall back to 'main'."""
        result = _prompt_project_settings(tmp_path, non_interactive=True)
        assert result["main_branch"] == "main"


# ---------------------------------------------------------------------------
# _prompt_model_mapping tests
# ---------------------------------------------------------------------------


class TestPromptModelMapping:
    def test_non_interactive_passthrough(self) -> None:
        mapping = ComplexityModelMapping(
            easy="haiku", medium="haiku", complex="sonnet", very_complex="opus"
        )
        result = _prompt_model_mapping("claude", mapping, non_interactive=True)
        assert result == mapping

    @patch("wade.ui.prompts.select")
    def test_interactive_allows_edits(self, mock_select: MagicMock) -> None:
        mapping = ComplexityModelMapping(
            easy="haiku", medium="haiku", complex="sonnet", very_complex="opus"
        )
        # User accepts all defaults (select returns default index each time)
        mock_select.side_effect = lambda title, items, default=0, **kw: default
        result = _prompt_model_mapping("claude", mapping, non_interactive=False)
        assert result.easy == "haiku"
        assert result.complex == "sonnet"

    @patch("wade.ui.prompts.input_prompt")
    @patch("wade.ui.prompts.select")
    def test_interactive_overrides_values(
        self, mock_select: MagicMock, mock_input: MagicMock
    ) -> None:
        mapping = ComplexityModelMapping(
            easy="haiku", medium="haiku", complex="sonnet", very_complex="opus"
        )
        # select returns last index (Custom...) each time
        mock_select.side_effect = lambda title, items, default=0, **kw: len(items) - 1
        mock_input.side_effect = ["custom-easy", "custom-med", "custom-complex", "custom-vc"]
        result = _prompt_model_mapping("claude", mapping, non_interactive=False)
        assert result.easy == "custom-easy"
        assert result.medium == "custom-med"
        assert result.complex == "custom-complex"
        assert result.very_complex == "custom-vc"


# ---------------------------------------------------------------------------
# _prompt_command_overrides tests
# ---------------------------------------------------------------------------


class TestPromptCommandOverrides:
    def test_non_interactive_returns_empty(self) -> None:
        result = _prompt_command_overrides(["claude"], non_interactive=True)
        assert result == {"plan": {}, "deps": {}}

    @patch("wade.ui.prompts.select")
    def test_interactive_no_overrides(self, mock_select: MagicMock) -> None:
        # Select "Skip (use default)" for plan and deps only
        # With installed_tools=["claude"], options are: ["claude", "Skip (use default)"]
        mock_select.side_effect = [1, 1]  # index 1 = Skip
        result = _prompt_command_overrides(["claude"], non_interactive=False)
        assert result["plan"] == {}
        assert result["deps"] == {}
        assert "work" not in result

    @patch("wade.services.init_service._suggest_model_for_tool")
    @patch("wade.ui.prompts.select")
    def test_interactive_with_tool_override(
        self, mock_select: MagicMock, mock_suggest: MagicMock
    ) -> None:
        mock_suggest.return_value = "gemini-2.5-pro"
        # installed_tools=["claude", "gemini"], tool_options=["claude", "gemini", "Skip"]
        # plan: idx 1 = gemini; model for plan: idx 1 = "gemini-2.5-pro" (2nd in gemini list);
        # deps: idx 2 = "Skip (use default)"
        mock_select.side_effect = [1, 1, 2]
        result = _prompt_command_overrides(["claude", "gemini"], non_interactive=False)
        assert result["plan"]["tool"] == "gemini"
        assert result["plan"]["model"] == "gemini-2.5-pro"
        assert result["deps"] == {}
        assert "work" not in result


# ---------------------------------------------------------------------------
# _write_config tests
# ---------------------------------------------------------------------------


class TestWriteConfig:
    def test_default_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        _write_config(config_path, "claude", ComplexityModelMapping())
        config = yaml.safe_load(config_path.read_text())
        assert config["version"] == 2
        assert config["project"]["main_branch"] == "main"
        assert config["ai"]["default_tool"] == "claude"

    def test_with_project_settings(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        settings = {
            "main_branch": "master",
            "merge_strategy": "direct",
            "branch_prefix": "fix",
            "issue_label": "bug",
            "worktrees_dir": "../trees",
        }
        _write_config(config_path, "claude", ComplexityModelMapping(), project_settings=settings)
        config = yaml.safe_load(config_path.read_text())
        assert config["project"]["main_branch"] == "master"
        assert config["project"]["merge_strategy"] == "direct"
        assert config["project"]["branch_prefix"] == "fix"
        assert config["project"]["issue_label"] == "bug"
        assert config["project"]["worktrees_dir"] == "../trees"

    def test_with_command_overrides(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        overrides = {
            "plan": {"tool": "gemini", "model": "gemini-2.5-pro"},
            "deps": {},
        }
        _write_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            work_tool="copilot",
            command_overrides=overrides,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["plan"]["tool"] == "gemini"
        assert config["ai"]["plan"]["model"] == "gemini-2.5-pro"
        assert "deps" not in config["ai"]
        assert config["ai"]["work"]["tool"] == "copilot"
        assert "model" not in config["ai"]["work"]

    def test_with_model_mapping(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        mapping = ComplexityModelMapping(
            easy="haiku", medium="haiku", complex="sonnet", very_complex="opus"
        )
        _write_config(config_path, "claude", mapping)
        config = yaml.safe_load(config_path.read_text())
        assert config["models"]["claude"]["easy"] == "haiku"
        assert config["models"]["claude"]["complex"] == "sonnet"
        assert config["models"]["claude"]["very_complex"] == "opus"

    def test_no_tool_omits_ai_and_models(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        _write_config(config_path, None, ComplexityModelMapping())
        config = yaml.safe_load(config_path.read_text())
        assert "default_tool" not in config.get("ai", {})
        assert "models" not in config

    def test_with_default_model_and_work_tool(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        mapping = ComplexityModelMapping(easy="haiku", complex="sonnet")
        _write_config(
            config_path,
            "claude",
            mapping,
            work_tool="gemini",
            default_model="gemini-2.5-pro",
        )
        config = yaml.safe_load(config_path.read_text())
        # default_model written to ai section
        assert config["ai"]["default_model"] == "gemini-2.5-pro"
        # work tool written only when different from default_tool
        assert config["ai"]["work"]["tool"] == "gemini"
        # models keyed by work_tool, not default_tool
        assert "gemini" in config["models"]
        assert "claude" not in config.get("models", {})

    def test_work_tool_same_as_ai_tool_omits_work_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        _write_config(config_path, "claude", ComplexityModelMapping(), work_tool="claude")
        config = yaml.safe_load(config_path.read_text())
        # work section omitted when tool matches default
        assert "work" not in config.get("ai", {})


# ---------------------------------------------------------------------------
# Update service tests
# ---------------------------------------------------------------------------


class TestUpdate:
    def test_update_refreshes_skills(self, tmp_git_repo: Path) -> None:
        # First init
        init(project_root=tmp_git_repo, non_interactive=True)

        # Then update
        success = update(project_root=tmp_git_repo)
        assert success

    def test_update_without_config_fails(self, tmp_git_repo: Path) -> None:
        success = update(project_root=tmp_git_repo)
        assert not success


# ---------------------------------------------------------------------------
# Deinit service tests
# ---------------------------------------------------------------------------


class TestDeinitConfirmation:
    """Tests for the confirmation gate in deinit() (force=False path)."""

    @patch("wade.ui.prompts.confirm", return_value=False)
    def test_deinit_aborts_when_user_declines(
        self, mock_confirm: MagicMock, tmp_git_repo: Path
    ) -> None:
        """When the user declines, deinit() returns False without removing config."""
        config_path = tmp_git_repo / ".wade.yml"
        config_path.write_text("version: 2\n")

        result = deinit(project_root=tmp_git_repo, force=False)

        assert result is False
        assert config_path.is_file()  # Config was NOT removed
        mock_confirm.assert_called_once()

    @patch("wade.ui.prompts.confirm", return_value=True)
    def test_deinit_proceeds_when_user_confirms(
        self, mock_confirm: MagicMock, tmp_git_repo: Path
    ) -> None:
        """When the user confirms, deinit() proceeds and removes config."""
        init(project_root=tmp_git_repo, non_interactive=True)
        config_path = tmp_git_repo / ".wade.yml"
        assert config_path.is_file()

        result = deinit(project_root=tmp_git_repo, force=False)

        assert result is True
        assert not config_path.is_file()  # Config WAS removed
        mock_confirm.assert_called_once()


class TestDeinit:
    def test_deinit_removes_config(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        assert (tmp_git_repo / ".wade.yml").is_file()

        deinit(project_root=tmp_git_repo, force=True)
        assert not (tmp_git_repo / ".wade.yml").is_file()

    def test_deinit_removes_manifest(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        deinit(project_root=tmp_git_repo, force=True)
        assert not (tmp_git_repo / MANIFEST_FILENAME).is_file()

    def test_deinit_removes_pointer(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        assert (tmp_git_repo / "AGENTS.md").is_file()

        deinit(project_root=tmp_git_repo, force=True)
        # AGENTS.md should be removed (was only pointer content)
        assert not (tmp_git_repo / "AGENTS.md").is_file()

    def test_deinit_preserves_agents_content(self, tmp_git_repo: Path) -> None:
        # Create AGENTS.md with project-specific content
        agents = tmp_git_repo / "AGENTS.md"
        agents.write_text("# My Project\n\nProject-specific rules.\n")

        init(project_root=tmp_git_repo, non_interactive=True)
        deinit(project_root=tmp_git_repo, force=True)

        # AGENTS.md should still exist with project content
        assert agents.is_file()
        content = agents.read_text()
        assert "# My Project" in content
        assert MARKER_START not in content

    def test_deinit_cleans_gitignore(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        deinit(project_root=tmp_git_repo, force=True)
        gitignore = tmp_git_repo / ".gitignore"
        if gitignore.is_file():
            content = gitignore.read_text()
            assert GITIGNORE_MARKER_START not in content
            assert GITIGNORE_MARKER_END not in content
            for entry in GITIGNORE_ENTRIES:
                assert entry not in content

    def test_deinit_removes_wade_dir(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        # Simulate wade creating its database directory
        wade_dir = tmp_git_repo / ".wade"
        wade_dir.mkdir(exist_ok=True)
        (wade_dir / "wade.db").write_text("fake db")

        deinit(project_root=tmp_git_repo, force=True)
        assert not wade_dir.exists()

    def test_full_lifecycle(self, tmp_git_repo: Path) -> None:
        """Test init → update → deinit full lifecycle."""
        # Init
        assert init(project_root=tmp_git_repo, ai_tool="claude", non_interactive=True)
        assert (tmp_git_repo / ".wade.yml").is_file()
        assert (tmp_git_repo / MANIFEST_FILENAME).is_file()
        assert (tmp_git_repo / "AGENTS.md").is_file()

        # Update
        assert update(project_root=tmp_git_repo)

        # Deinit
        assert deinit(project_root=tmp_git_repo, force=True)
        assert not (tmp_git_repo / ".wade.yml").is_file()
        assert not (tmp_git_repo / MANIFEST_FILENAME).is_file()


# ---------------------------------------------------------------------------
# _read_manifest_version tests
# ---------------------------------------------------------------------------


class TestReadManifestVersion:
    def test_parses_wade_version(self, tmp_path: Path) -> None:
        manifest = tmp_path / MANIFEST_FILENAME
        manifest.write_text("# Managed by wade 0.1.0\n.wade.yml\n")
        assert _read_manifest_version(tmp_path) == "0.1.0"

    def test_parses_wade_version_other_value(self, tmp_path: Path) -> None:
        manifest = tmp_path / MANIFEST_FILENAME
        manifest.write_text("# Managed by wade 3.14.0\n.wade.yml\n")
        assert _read_manifest_version(tmp_path) == "3.14.0"

    def test_returns_none_when_no_manifest(self, tmp_path: Path) -> None:
        assert _read_manifest_version(tmp_path) is None

    def test_returns_none_when_no_version_line(self, tmp_path: Path) -> None:
        manifest = tmp_path / MANIFEST_FILENAME
        manifest.write_text(".wade.yml\n.claude/skills/workflow/SKILL.md\n")
        assert _read_manifest_version(tmp_path) is None


# ---------------------------------------------------------------------------
# Extended update() tests — migrations, legacy, allowlist
# ---------------------------------------------------------------------------


class TestUpdateExtended:
    def test_update_runs_migrations(self, tmp_git_repo: Path) -> None:
        """update() should call run_all_migrations on the config path."""
        init(project_root=tmp_git_repo, non_interactive=True)

        with patch("wade.config.migrations.run_all_migrations") as mock_mig:
            mock_mig.return_value = False
            update(project_root=tmp_git_repo, skip_self_upgrade=True)
            mock_mig.assert_called_once()

    def test_update_configures_allowlist(self, tmp_git_repo: Path) -> None:
        """update() should call configure_allowlist when Claude is the configured tool."""
        with patch(
            "wade.services.init_service.AbstractAITool.detect_installed",
            return_value=[AIToolID.CLAUDE],
        ):
            init(project_root=tmp_git_repo, non_interactive=True)

        with patch("wade.config.claude_allowlist.configure_allowlist") as mock_allow:
            update(project_root=tmp_git_repo, skip_self_upgrade=True)
            mock_allow.assert_called_once()

    def test_skip_self_upgrade_flag(self, tmp_git_repo: Path) -> None:
        """skip_self_upgrade=True should not call _maybe_self_upgrade."""
        init(project_root=tmp_git_repo, non_interactive=True)

        with patch("wade.services.init_service._maybe_self_upgrade") as mock_upgrade:
            update(project_root=tmp_git_repo, skip_self_upgrade=True)
            mock_upgrade.assert_not_called()

    def test_update_version_transition(self, tmp_git_repo: Path) -> None:
        """update() should detect version change from manifest."""
        init(project_root=tmp_git_repo, non_interactive=True)

        # Write a manifest with an old version number
        manifest = tmp_git_repo / MANIFEST_FILENAME
        manifest.write_text("# Managed by wade 0.0.1\n.wade.yml\n")

        success = update(project_root=tmp_git_repo, skip_self_upgrade=True)
        assert success  # update should succeed and detect version difference


# ---------------------------------------------------------------------------
# Gitignore entries coverage
# ---------------------------------------------------------------------------


class TestGitignoreEntries:
    """Verify GITIGNORE_ENTRIES includes all wade-managed paths."""

    def test_contains_wade_config(self) -> None:
        assert ".wade.yml" in GITIGNORE_ENTRIES

    def test_contains_skill_dirs(self) -> None:
        assert ".claude/skills/" in GITIGNORE_ENTRIES
        assert ".github/skills" in GITIGNORE_ENTRIES
        assert ".agents/" in GITIGNORE_ENTRIES
        assert ".gemini/" in GITIGNORE_ENTRIES

    def test_contains_internal_files(self) -> None:
        assert ".wade/" in GITIGNORE_ENTRIES
        assert ".wade-managed" in GITIGNORE_ENTRIES
        assert "PLAN.md" in GITIGNORE_ENTRIES
        assert "PR-SUMMARY.md" in GITIGNORE_ENTRIES


# ---------------------------------------------------------------------------
# _configure_local_worktree tests
# ---------------------------------------------------------------------------


class TestConfigureLocalWorktree:
    def test_adds_wade_yml_to_copy_to_worktree(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            yaml.dump({"version": 2, "project": {"main_branch": "main"}}),
            encoding="utf-8",
        )
        _configure_local_worktree(config_path)
        config = yaml.safe_load(config_path.read_text())
        assert ".wade.yml" in config["hooks"]["copy_to_worktree"]

    def test_idempotent(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            yaml.dump(
                {
                    "version": 2,
                    "hooks": {"copy_to_worktree": [".wade.yml"]},
                }
            ),
            encoding="utf-8",
        )
        _configure_local_worktree(config_path)
        config = yaml.safe_load(config_path.read_text())
        assert config["hooks"]["copy_to_worktree"].count(".wade.yml") == 1

    def test_preserves_existing_entries(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            yaml.dump(
                {
                    "version": 2,
                    "hooks": {"copy_to_worktree": [".env"]},
                }
            ),
            encoding="utf-8",
        )
        _configure_local_worktree(config_path)
        config = yaml.safe_load(config_path.read_text())
        assert ".env" in config["hooks"]["copy_to_worktree"]
        assert ".wade.yml" in config["hooks"]["copy_to_worktree"]

    def test_handles_missing_hooks_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(yaml.dump({"version": 2}), encoding="utf-8")
        _configure_local_worktree(config_path)
        config = yaml.safe_load(config_path.read_text())
        assert ".wade.yml" in config["hooks"]["copy_to_worktree"]

    def test_handles_unreadable_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        # File doesn't exist — should not raise
        _configure_local_worktree(config_path)


# ---------------------------------------------------------------------------
# _commit_wade_files tests
# ---------------------------------------------------------------------------


class TestCommitWadeFiles:
    def test_commits_files(self, tmp_git_repo: Path) -> None:
        """git add --force + git commit should succeed on a real repo."""
        config_path = tmp_git_repo / ".wade.yml"
        config_path.write_text("version: 2\n")
        gitignore = tmp_git_repo / ".gitignore"
        gitignore.write_text(".wade/\n")
        manifest = tmp_git_repo / MANIFEST_FILENAME
        manifest.write_text(".wade.yml\n")
        agents = tmp_git_repo / "AGENTS.md"
        agents.write_text("# Agents\n")

        _commit_wade_files(tmp_git_repo, [])

        # Verify commit was created
        import subprocess

        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=tmp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "chore: initialize wade" in result.stdout

    def test_handles_commit_failure(self, tmp_path: Path) -> None:
        """When not in a git repo, should warn instead of crashing."""
        # tmp_path is not a git repo — git add will fail
        _commit_wade_files(tmp_path, [])
        # Should not raise — just logs a warning


# ---------------------------------------------------------------------------
# _prompt_commit_or_local tests
# ---------------------------------------------------------------------------


class TestPromptCommitOrLocal:
    def test_non_interactive_configures_local(self, tmp_path: Path) -> None:
        """Non-interactive mode should configure copy_to_worktree, not commit."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(yaml.dump({"version": 2}), encoding="utf-8")

        _prompt_commit_or_local(tmp_path, config_path, [], non_interactive=True)

        config = yaml.safe_load(config_path.read_text())
        assert ".wade.yml" in config["hooks"]["copy_to_worktree"]

    @patch("wade.ui.prompts.is_tty", return_value=False)
    def test_no_tty_configures_local(self, _mock_tty: MagicMock, tmp_path: Path) -> None:
        """When not a TTY, should configure copy_to_worktree."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(yaml.dump({"version": 2}), encoding="utf-8")

        _prompt_commit_or_local(tmp_path, config_path, [], non_interactive=False)

        config = yaml.safe_load(config_path.read_text())
        assert ".wade.yml" in config["hooks"]["copy_to_worktree"]

    @patch("wade.ui.prompts.is_tty", return_value=True)
    @patch("wade.ui.prompts.confirm", return_value=True)
    @patch("wade.services.init_service._commit_wade_files")
    def test_interactive_commit_yes(
        self,
        mock_commit: MagicMock,
        _mock_confirm: MagicMock,
        _mock_tty: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When user says yes, should call _commit_wade_files."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(yaml.dump({"version": 2}), encoding="utf-8")

        _prompt_commit_or_local(tmp_path, config_path, ["a.md"], non_interactive=False)

        mock_commit.assert_called_once_with(tmp_path, ["a.md"])

    @patch("wade.ui.prompts.is_tty", return_value=True)
    @patch("wade.ui.prompts.confirm", return_value=False)
    def test_interactive_commit_no(
        self,
        _mock_confirm: MagicMock,
        _mock_tty: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When user says no, should configure copy_to_worktree."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(yaml.dump({"version": 2}), encoding="utf-8")

        _prompt_commit_or_local(tmp_path, config_path, [], non_interactive=False)

        config = yaml.safe_load(config_path.read_text())
        assert ".wade.yml" in config["hooks"]["copy_to_worktree"]

    def test_init_non_interactive_adds_copy_to_worktree(self, tmp_git_repo: Path) -> None:
        """Full init in non-interactive mode should set copy_to_worktree."""
        init(project_root=tmp_git_repo, non_interactive=True)

        config = yaml.safe_load((tmp_git_repo / ".wade.yml").read_text())
        assert ".wade.yml" in config["hooks"]["copy_to_worktree"]
