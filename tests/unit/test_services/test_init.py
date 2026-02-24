"""Tests for init service — init, update, deinit lifecycle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from ghaiw.models.ai import AIToolID
from ghaiw.models.config import ComplexityModelMapping
from ghaiw.services.init_service import (
    MANIFEST_FILENAME,
    _prompt_command_overrides,
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
from ghaiw.skills.installer import get_templates_dir
from ghaiw.skills.pointer import (
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
        from ghaiw.skills.installer import install_skills

        installed = install_skills(tmp_git_repo)
        assert len(installed) > 0

        # Check that cross-tool symlinks were created
        assert (tmp_git_repo / ".github" / "skills").exists()
        assert (tmp_git_repo / ".agents" / "skills").exists()
        assert (tmp_git_repo / ".gemini" / "skills").exists()

    def test_remove_skills(self, tmp_git_repo: Path) -> None:
        from ghaiw.skills.installer import install_skills, remove_skills

        install_skills(tmp_git_repo)
        removed = remove_skills(tmp_git_repo)
        assert len(removed) > 0

        # Cross-tool symlinks should be gone
        assert not (tmp_git_repo / ".github" / "skills").exists()


# ---------------------------------------------------------------------------
# Init service tests
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_creates_config(self, tmp_git_repo: Path) -> None:
        success = init(project_root=tmp_git_repo, non_interactive=True)
        assert success

        config_path = tmp_git_repo / ".ghaiw.yml"
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
        assert ".ghaiw.yml" in content

    def test_init_creates_gitignore_entries(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        gitignore = tmp_git_repo / ".gitignore"
        assert gitignore.is_file()
        content = gitignore.read_text()
        assert ".ghaiw-managed" in content

    def test_init_creates_agents_pointer(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        assert (tmp_git_repo / "AGENTS.md").is_file()
        content = (tmp_git_repo / "AGENTS.md").read_text()
        assert "Git Workflow" in content

    def test_init_with_ai_tool(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, ai_tool="claude", non_interactive=True)
        config = yaml.safe_load((tmp_git_repo / ".ghaiw.yml").read_text())
        assert config["ai"]["default_tool"] == "claude"

    def test_init_patches_existing_config(self, tmp_git_repo: Path) -> None:
        config_path = tmp_git_repo / ".ghaiw.yml"
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

        config = yaml.safe_load((tmp_git_repo / ".ghaiw.yml").read_text())
        assert config["project"]["main_branch"] == "main"


# ---------------------------------------------------------------------------
# _select_ai_tool tests
# ---------------------------------------------------------------------------


class TestSelectAITool:
    def test_requested_tool_returned_directly(self) -> None:
        result = _select_ai_tool("claude", non_interactive=False)
        assert result == "claude"

    def test_unknown_tool_warns_and_returns(self) -> None:
        result = _select_ai_tool("unknown-tool", non_interactive=False)
        assert result == "unknown-tool"

    @patch("ghaiw.services.init_service.AbstractAITool.detect_installed")
    def test_no_tools_returns_none(self, mock_detect: MagicMock) -> None:
        mock_detect.return_value = []
        result = _select_ai_tool(None, non_interactive=False)
        assert result is None

    @patch("ghaiw.services.init_service.AbstractAITool.detect_installed")
    def test_single_tool_auto_selects(self, mock_detect: MagicMock) -> None:
        mock_detect.return_value = [AIToolID.CLAUDE]
        result = _select_ai_tool(None, non_interactive=False)
        assert result == "claude"

    @patch("ghaiw.services.init_service.AbstractAITool.detect_installed")
    def test_multiple_tools_non_interactive_selects_first(self, mock_detect: MagicMock) -> None:
        mock_detect.return_value = [AIToolID.CLAUDE, AIToolID.COPILOT]
        result = _select_ai_tool(None, non_interactive=True)
        assert result == "claude"

    @patch("ghaiw.ui.prompts.select")
    @patch("ghaiw.services.init_service.AbstractAITool.detect_installed")
    def test_multiple_tools_interactive_selects_chosen(
        self, mock_detect: MagicMock, mock_select: MagicMock
    ) -> None:
        mock_detect.return_value = [AIToolID.CLAUDE, AIToolID.COPILOT]
        mock_select.return_value = 1  # copilot
        result = _select_ai_tool(None, non_interactive=False)
        assert result == "copilot"

    @patch("ghaiw.ui.prompts.select")
    @patch("ghaiw.services.init_service.AbstractAITool.detect_installed")
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
    def test_no_tool_returns_empty_and_false(self) -> None:
        mapping, probed = _resolve_models(None)
        assert mapping.easy is None
        assert probed is False

    @patch("ghaiw.services.init_service.AbstractAITool.get")
    def test_successful_probe_returns_true(self, mock_get: MagicMock) -> None:
        adapter = MagicMock()
        adapter.get_recommended_mapping.return_value = ComplexityModelMapping(
            easy="haiku", medium="haiku", complex="sonnet", very_complex="opus"
        )
        adapter.normalize_model_format.side_effect = lambda x: x
        mock_get.return_value = adapter

        mapping, probed = _resolve_models("claude")
        assert probed is True
        assert mapping.easy == "haiku"
        assert mapping.complex == "sonnet"

    @patch("ghaiw.services.init_service.AbstractAITool.get")
    def test_failed_probe_returns_false(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = ValueError("No such tool")

        mapping, probed = _resolve_models("claude")
        assert probed is False
        # Should still get defaults
        assert mapping.easy is not None


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

    @patch("ghaiw.ui.prompts.input_prompt")
    def test_interactive_uses_prompts(self, mock_input: MagicMock, tmp_git_repo: Path) -> None:
        mock_input.side_effect = ["direct", "fix", "bug", "../worktrees"]
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
        result = _prompt_model_mapping("claude", mapping, True, non_interactive=True)
        assert result == mapping

    @patch("ghaiw.ui.prompts.input_prompt")
    def test_interactive_allows_edits(self, mock_input: MagicMock) -> None:
        mapping = ComplexityModelMapping(
            easy="haiku", medium="haiku", complex="sonnet", very_complex="opus"
        )
        # User accepts all defaults (returns empty → falls back to mapping values)
        mock_input.side_effect = ["", "", "", ""]
        result = _prompt_model_mapping("claude", mapping, True, non_interactive=False)
        assert result.easy == "haiku"
        assert result.complex == "sonnet"

    @patch("ghaiw.ui.prompts.input_prompt")
    def test_interactive_overrides_values(self, mock_input: MagicMock) -> None:
        mapping = ComplexityModelMapping(
            easy="haiku", medium="haiku", complex="sonnet", very_complex="opus"
        )
        mock_input.side_effect = ["custom-easy", "custom-med", "custom-complex", "custom-vc"]
        result = _prompt_model_mapping("claude", mapping, True, non_interactive=False)
        assert result.easy == "custom-easy"
        assert result.medium == "custom-med"
        assert result.complex == "custom-complex"
        assert result.very_complex == "custom-vc"

    @patch("ghaiw.services.init_service.console")
    def test_probing_failed_shows_warning(self, mock_console: MagicMock) -> None:
        mapping = ComplexityModelMapping(easy="haiku")
        _prompt_model_mapping("claude", mapping, False, non_interactive=True)
        # Non-interactive returns early, but no warning in non-interactive mode
        # Test interactive mode warning
        mock_console.reset_mock()

    @patch("ghaiw.ui.prompts.input_prompt")
    @patch("ghaiw.services.init_service.console")
    def test_probing_failed_interactive_warns(
        self, mock_console: MagicMock, mock_input: MagicMock
    ) -> None:
        mapping = ComplexityModelMapping(easy="haiku")
        mock_input.side_effect = ["", "", "", ""]
        _prompt_model_mapping("claude", mapping, False, non_interactive=False)
        mock_console.warn.assert_called_once()
        assert "Could not auto-detect" in mock_console.warn.call_args[0][0]


# ---------------------------------------------------------------------------
# _prompt_command_overrides tests
# ---------------------------------------------------------------------------


class TestPromptCommandOverrides:
    def test_non_interactive_returns_empty(self) -> None:
        result = _prompt_command_overrides(["claude"], non_interactive=True)
        assert result == {"plan": {}, "deps": {}, "work": {}}

    @patch("ghaiw.ui.prompts.select")
    def test_interactive_no_overrides(self, mock_select: MagicMock) -> None:
        # Select "Skip (use default)" for all three commands
        # With installed_tools=["claude"], options are: ["claude", "Skip (use default)"]
        mock_select.side_effect = [1, 1, 1]  # index 1 = Skip
        result = _prompt_command_overrides(["claude"], non_interactive=False)
        assert result["plan"] == {}
        assert result["deps"] == {}
        assert result["work"] == {}

    @patch("ghaiw.services.init_service._suggest_model_for_tool")
    @patch("ghaiw.ui.prompts.input_prompt")
    @patch("ghaiw.ui.prompts.select")
    def test_interactive_with_tool_override(
        self, mock_select: MagicMock, mock_input: MagicMock, mock_suggest: MagicMock
    ) -> None:
        mock_suggest.return_value = "gemini-2.5-pro"
        # plan: select gemini (index 1), deps: skip (index 2), work: skip (index 2)
        mock_select.side_effect = [1, 2, 2]
        # After selecting gemini for plan, input_prompt asks for model
        mock_input.side_effect = ["gemini-2.5-pro"]
        result = _prompt_command_overrides(["claude", "gemini"], non_interactive=False)
        assert result["plan"]["tool"] == "gemini"
        assert result["plan"]["model"] == "gemini-2.5-pro"
        assert result["deps"] == {}
        assert result["work"] == {}


# ---------------------------------------------------------------------------
# _write_config tests
# ---------------------------------------------------------------------------


class TestWriteConfig:
    def test_default_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        _write_config(config_path, "claude", ComplexityModelMapping())
        config = yaml.safe_load(config_path.read_text())
        assert config["version"] == 2
        assert config["project"]["main_branch"] == "main"
        assert config["ai"]["default_tool"] == "claude"

    def test_with_project_settings(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
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
        config_path = tmp_path / ".ghaiw.yml"
        overrides = {
            "plan": {"tool": "gemini", "model": "gemini-2.5-pro"},
            "deps": {},
            "work": {"tool": "copilot"},
        }
        _write_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            command_overrides=overrides,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["plan"]["tool"] == "gemini"
        assert config["ai"]["plan"]["model"] == "gemini-2.5-pro"
        assert "deps" not in config["ai"]
        assert config["ai"]["work"]["tool"] == "copilot"
        assert "model" not in config["ai"]["work"]

    def test_with_model_mapping(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        mapping = ComplexityModelMapping(
            easy="haiku", medium="haiku", complex="sonnet", very_complex="opus"
        )
        _write_config(config_path, "claude", mapping)
        config = yaml.safe_load(config_path.read_text())
        assert config["models"]["claude"]["easy"] == "haiku"
        assert config["models"]["claude"]["complex"] == "sonnet"
        assert config["models"]["claude"]["very_complex"] == "opus"

    def test_no_tool_omits_ai_and_models(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".ghaiw.yml"
        _write_config(config_path, None, ComplexityModelMapping())
        config = yaml.safe_load(config_path.read_text())
        assert "default_tool" not in config.get("ai", {})
        assert "models" not in config


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


class TestDeinit:
    def test_deinit_removes_config(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        assert (tmp_git_repo / ".ghaiw.yml").is_file()

        deinit(project_root=tmp_git_repo, force=True)
        assert not (tmp_git_repo / ".ghaiw.yml").is_file()

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
            assert ".ghaiw-managed" not in content

    def test_full_lifecycle(self, tmp_git_repo: Path) -> None:
        """Test init → update → deinit full lifecycle."""
        # Init
        assert init(project_root=tmp_git_repo, ai_tool="claude", non_interactive=True)
        assert (tmp_git_repo / ".ghaiw.yml").is_file()
        assert (tmp_git_repo / MANIFEST_FILENAME).is_file()
        assert (tmp_git_repo / "AGENTS.md").is_file()

        # Update
        assert update(project_root=tmp_git_repo)

        # Deinit
        assert deinit(project_root=tmp_git_repo, force=True)
        assert not (tmp_git_repo / ".ghaiw.yml").is_file()
        assert not (tmp_git_repo / MANIFEST_FILENAME).is_file()


# ---------------------------------------------------------------------------
# _read_manifest_version tests
# ---------------------------------------------------------------------------


class TestReadManifestVersion:
    def test_parses_ghaiwpy_version(self, tmp_path: Path) -> None:
        manifest = tmp_path / MANIFEST_FILENAME
        manifest.write_text("# Managed by ghaiwpy 0.1.0\n.ghaiw.yml\n")
        assert _read_manifest_version(tmp_path) == "0.1.0"

    def test_parses_ghaiw_version(self, tmp_path: Path) -> None:
        manifest = tmp_path / MANIFEST_FILENAME
        manifest.write_text("# Managed by ghaiw 3.14.0\n.ghaiw.yml\n")
        assert _read_manifest_version(tmp_path) == "3.14.0"

    def test_returns_none_when_no_manifest(self, tmp_path: Path) -> None:
        assert _read_manifest_version(tmp_path) is None

    def test_returns_none_when_no_version_line(self, tmp_path: Path) -> None:
        manifest = tmp_path / MANIFEST_FILENAME
        manifest.write_text(".ghaiw.yml\n.claude/skills/workflow/SKILL.md\n")
        assert _read_manifest_version(tmp_path) is None


# ---------------------------------------------------------------------------
# Extended update() tests — migrations, legacy, allowlist
# ---------------------------------------------------------------------------


class TestUpdateExtended:
    def test_update_runs_migrations(self, tmp_git_repo: Path) -> None:
        """update() should call run_all_migrations on the config path."""
        init(project_root=tmp_git_repo, non_interactive=True)

        with patch("ghaiw.config.migrations.run_all_migrations") as mock_mig:
            mock_mig.return_value = False
            update(project_root=tmp_git_repo, skip_self_upgrade=True)
            mock_mig.assert_called_once()

    def test_update_cleans_legacy_artifacts(self, tmp_git_repo: Path) -> None:
        """update() should call cleanup_legacy_artifacts."""
        init(project_root=tmp_git_repo, non_interactive=True)

        with patch("ghaiw.config.legacy.cleanup_legacy_artifacts") as mock_clean:
            mock_clean.return_value = 0
            update(project_root=tmp_git_repo, skip_self_upgrade=True)
            mock_clean.assert_called_once()

    def test_update_configures_allowlist(self, tmp_git_repo: Path) -> None:
        """update() should call configure_allowlist."""
        init(project_root=tmp_git_repo, non_interactive=True)

        with patch("ghaiw.config.claude_allowlist.configure_allowlist") as mock_allow:
            update(project_root=tmp_git_repo, skip_self_upgrade=True)
            mock_allow.assert_called_once()

    def test_skip_self_upgrade_flag(self, tmp_git_repo: Path) -> None:
        """skip_self_upgrade=True should not call _maybe_self_upgrade."""
        init(project_root=tmp_git_repo, non_interactive=True)

        with patch("ghaiw.services.init_service._maybe_self_upgrade") as mock_upgrade:
            update(project_root=tmp_git_repo, skip_self_upgrade=True)
            mock_upgrade.assert_not_called()

    def test_update_version_transition(self, tmp_git_repo: Path) -> None:
        """update() should detect version change from manifest."""
        init(project_root=tmp_git_repo, non_interactive=True)

        # Write a manifest with an old version number
        manifest = tmp_git_repo / MANIFEST_FILENAME
        manifest.write_text("# Managed by ghaiwpy 0.0.1\n.ghaiw.yml\n")

        success = update(project_root=tmp_git_repo, skip_self_upgrade=True)
        assert success  # update should succeed and detect version difference
