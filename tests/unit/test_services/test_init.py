"""Tests for init service — init, update, deinit lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from crossby.models.ai import AIToolID

from wade.models.config import ComplexityModelMapping, ProjectSettings
from wade.services.init_service import (
    GITIGNORE_ENTRIES,
    GITIGNORE_MARKER_END,
    GITIGNORE_MARKER_START,
    MANIFEST_FILENAME,
    _check_gh_auth,
    _clean_gitignore,
    _ensure_wade_dir_self_ignoring,
    _maybe_self_upgrade,
    _migrate_gitignore_block,
    _patch_config,
    _prompt_ai_section,
    _prompt_command_overrides,
    _prompt_hooks_setup,
    _prompt_model_mapping,
    _prompt_project_settings,
    _prompt_provider_setup,
    _read_manifest_version,
    _resolve_models,
    _save_token_to_env,
    _select_ai_tool,
    _select_or_skip,
    _show_init_summary,
    _validate_clickup_token,
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
from wade.utils.install import InstallMethod

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
        assert (tmp_git_repo / ".cursor" / "skills").exists()

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
    """Unit tests for _clean_gitignore (backward compat) and migration helpers."""

    # --- _clean_gitignore (backward compat for deinit) ---

    def test_removes_marker_block(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        inner = "\n".join(GITIGNORE_ENTRIES)
        gitignore.write_text(f"{GITIGNORE_MARKER_START}\n{inner}\n{GITIGNORE_MARKER_END}\n")
        _clean_gitignore(tmp_path)
        content = gitignore.read_text()
        assert GITIGNORE_MARKER_START not in content
        assert GITIGNORE_MARKER_END not in content
        for entry in GITIGNORE_ENTRIES:
            assert entry not in content

    def test_preserves_user_content_on_clean(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        inner = "\n".join(GITIGNORE_ENTRIES)
        gitignore.write_text(
            f"__pycache__/\n*.py[cod]\n\n{GITIGNORE_MARKER_START}\n{inner}\n{GITIGNORE_MARKER_END}\n"
        )
        _clean_gitignore(tmp_path)
        content = gitignore.read_text()
        assert "__pycache__/" in content
        assert "*.py[cod]" in content
        assert GITIGNORE_MARKER_START not in content

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

    # --- _migrate_gitignore_block ---

    def test_migrate_removes_stale_block(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        inner = "\n".join(GITIGNORE_ENTRIES)
        gitignore.write_text(
            f"__pycache__/\n\n{GITIGNORE_MARKER_START}\n{inner}\n{GITIGNORE_MARKER_END}\n"
        )
        _migrate_gitignore_block(tmp_path)
        content = gitignore.read_text()
        assert GITIGNORE_MARKER_START not in content
        assert "__pycache__/" in content

    def test_migrate_no_op_when_no_block(self, tmp_path: Path) -> None:
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("__pycache__/\n")
        _migrate_gitignore_block(tmp_path)
        content = gitignore.read_text()
        assert "__pycache__/" in content

    def test_migrate_no_op_when_no_file(self, tmp_path: Path) -> None:
        _migrate_gitignore_block(tmp_path)  # must not raise

    # --- _ensure_wade_dir_self_ignoring ---

    def test_creates_wade_dir_with_gitignore(self, tmp_path: Path) -> None:
        _ensure_wade_dir_self_ignoring(tmp_path)
        gi = tmp_path / ".wade" / ".gitignore"
        assert gi.is_file()
        assert gi.read_text().strip() == "*"

    def test_idempotent(self, tmp_path: Path) -> None:
        _ensure_wade_dir_self_ignoring(tmp_path)
        _ensure_wade_dir_self_ignoring(tmp_path)
        gi = tmp_path / ".wade" / ".gitignore"
        assert gi.read_text().strip() == "*"


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
        manifest = tmp_git_repo / ".wade" / MANIFEST_FILENAME
        assert manifest.is_file()
        content = manifest.read_text()
        assert ".wade.yml" in content

    def test_init_does_not_create_gitignore_block(self, tmp_git_repo: Path) -> None:
        """init() no longer writes a committed gitignore block."""
        init(project_root=tmp_git_repo, non_interactive=True)
        gitignore = tmp_git_repo / ".gitignore"
        if gitignore.is_file():
            content = gitignore.read_text()
            assert GITIGNORE_MARKER_START not in content

    def test_init_creates_wade_dir_self_ignoring(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        gi = tmp_git_repo / ".wade" / ".gitignore"
        assert gi.is_file()
        assert gi.read_text().strip() == "*"

    def test_init_does_not_write_agents_pointer_to_main(self, tmp_git_repo: Path) -> None:
        """init() no longer writes the AGENTS.md pointer to main — only worktrees get it."""
        init(project_root=tmp_git_repo, non_interactive=True)
        assert not (tmp_git_repo / "AGENTS.md").is_file()
        assert not (tmp_git_repo / "CLAUDE.md").exists()

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

    @patch(
        "wade.services.init_service.commands._prompt_knowledge_setup",
        return_value={"enabled": True, "path": "docs/KNOWLEDGE.md"},
    )
    def test_init_with_knowledge_enabled_creates_file_and_config(
        self, _mock_knowledge: MagicMock, tmp_git_repo: Path
    ) -> None:
        success = init(project_root=tmp_git_repo, non_interactive=True)
        assert success

        config = yaml.safe_load((tmp_git_repo / ".wade.yml").read_text())
        assert config["knowledge"] == {"enabled": True, "path": "docs/KNOWLEDGE.md"}
        # #358: knowledge files are NOT added to copy_to_worktree — they are tracked,
        # so a worktree gets the committed version; copying manufactures a stale snapshot.
        copy_list = config["hooks"].get("copy_to_worktree", []) or []
        assert "docs/KNOWLEDGE.md" not in copy_list
        assert "docs/KNOWLEDGE.ratings.jsonl" not in copy_list
        assert "docs/KNOWLEDGE.ratings.yml" not in copy_list

        knowledge_path = tmp_git_repo / "docs" / "KNOWLEDGE.md"
        assert knowledge_path.exists()
        assert knowledge_path.read_text(encoding="utf-8").startswith("# Project Knowledge")

    @patch(
        "wade.services.init_service.commands._prompt_knowledge_setup",
        return_value={"enabled": True, "path": "../KNOWLEDGE.md"},
    )
    def test_init_rejects_invalid_knowledge_path_before_writing_config(
        self, _mock_knowledge: MagicMock, tmp_git_repo: Path
    ) -> None:
        success = init(project_root=tmp_git_repo, non_interactive=True)
        assert not success
        assert not (tmp_git_repo / ".wade.yml").exists()
        assert not (tmp_git_repo.parent / "KNOWLEDGE.md").exists()


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

    @patch("wade.services.init_service.prompts_ai.AbstractAITool.detect_installed")
    def test_no_tools_returns_none(self, mock_detect: MagicMock) -> None:
        mock_detect.return_value = []
        result = _select_ai_tool(None, non_interactive=False)
        assert result is None

    @patch("wade.services.init_service.prompts_ai.AbstractAITool.detect_installed")
    def test_single_tool_auto_selects(self, mock_detect: MagicMock) -> None:
        mock_detect.return_value = [AIToolID.CLAUDE]
        result = _select_ai_tool(None, non_interactive=False)
        assert result == "claude"

    @patch("wade.services.init_service.prompts_ai.AbstractAITool.detect_installed")
    def test_multiple_tools_non_interactive_selects_first(self, mock_detect: MagicMock) -> None:
        mock_detect.return_value = [AIToolID.CLAUDE, AIToolID.COPILOT]
        result = _select_ai_tool(None, non_interactive=True)
        assert result == "claude"

    @patch("wade.ui.prompts.select")
    @patch("wade.services.init_service.prompts_ai.AbstractAITool.detect_installed")
    def test_multiple_tools_interactive_selects_chosen(
        self, mock_detect: MagicMock, mock_select: MagicMock
    ) -> None:
        mock_detect.return_value = [AIToolID.CLAUDE, AIToolID.COPILOT]
        mock_select.return_value = 1  # copilot
        result = _select_ai_tool(None, non_interactive=False)
        assert result == "copilot"

    @patch("wade.ui.prompts.select")
    @patch("wade.services.init_service.prompts_ai.AbstractAITool.detect_installed")
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
        assert result.main_branch == "main"
        assert result.merge_strategy == "PR"
        assert result.branch_prefix == "feat"
        assert result.issue_label == "feature-plan"
        assert result.worktrees_dir == "../.worktrees"

    @patch("wade.ui.prompts.select")
    @patch("wade.ui.prompts.input_prompt")
    def test_interactive_uses_prompts(
        self, mock_input: MagicMock, mock_select: MagicMock, tmp_git_repo: Path
    ) -> None:
        # merge_strategy is no longer prompted (only "PR" since #357 retired
        # "direct") — the wizard just collects branch_prefix, issue_label,
        # worktrees_dir via input_prompt and never calls select here.
        mock_input.side_effect = ["fix", "bug", "../worktrees"]
        result = _prompt_project_settings(tmp_git_repo, non_interactive=False)
        assert result.merge_strategy == "PR"
        assert result.branch_prefix == "fix"
        assert result.issue_label == "bug"
        assert result.worktrees_dir == "../worktrees"
        # main_branch is auto-detected, not prompted
        assert result.main_branch == "main"
        mock_select.assert_not_called()

    def test_detects_main_branch_from_git(self, tmp_git_repo: Path) -> None:
        result = _prompt_project_settings(tmp_git_repo, non_interactive=True)
        assert result.main_branch == "main"

    def test_fallback_main_branch_without_git(self, tmp_path: Path) -> None:
        """When not in a git repo, should fall back to 'main'."""
        result = _prompt_project_settings(tmp_path, non_interactive=True)
        assert result.main_branch == "main"


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
        # review_pr_comments (#389) flows through _COMMAND_OVERRIDE_NAMES as an
        # empty default; the wizard doesn't prompt for it (not in cmd_triples), so
        # it is never written unless a user sets ai.review_pr_comments by hand.
        assert result == {
            "plan": {},
            "deps": {},
            "review_plan": {},
            "review_implementation": {},
            "review_batch": {},
            "review_pr_comments": {},
        }

    @patch("wade.ui.prompts.select")
    def test_interactive_no_overrides(self, mock_select: MagicMock) -> None:
        """Accepting defaults should preserve current review/deps default modes."""
        mock_select.side_effect = lambda _title, _items, default=0, **_kw: default
        result = _prompt_command_overrides(
            ["claude"],
            non_interactive=False,
            default_tool="claude",
        )
        assert result["plan"] == {}
        assert result["deps"] == {"mode": "headless"}
        assert result["review_plan"] == {"enabled": "true", "mode": "prompt"}
        assert result["review_implementation"] == {"enabled": "true", "mode": "prompt"}
        assert result["review_batch"] == {"enabled": "true", "mode": "interactive"}

    @patch("wade.ui.prompts.select")
    def test_interactive_reviews_disabled(self, mock_select: MagicMock) -> None:
        # plan: Skip tool; deps: Skip tool (no effective tool → mode skipped)
        # review_plan/review_implementation/review_batch: Enable=No
        mock_select.side_effect = [
            1,  # plan: Skip tool
            1,  # deps: Skip tool (no effective tool → no mode prompt)
            1,  # review_plan: Enable=No
            1,  # review_implementation: Enable=No
            1,  # review_batch: Enable=No
        ]
        result = _prompt_command_overrides(["claude"], non_interactive=False)
        assert result["plan"] == {}
        assert result["deps"] == {}
        assert result["review_plan"] == {"enabled": "false"}
        assert result["review_implementation"] == {"enabled": "false"}
        assert result["review_batch"] == {"enabled": "false"}

    @patch("wade.ui.prompts.select")
    def test_review_batch_mode_prompt_defaults_to_interactive(self, mock_select: MagicMock) -> None:
        """Batch review should default to interactive, not prompt, in the init wizard."""
        mock_select.side_effect = lambda _title, _items, default=0, **_kw: default

        _prompt_command_overrides(
            ["claude"],
            non_interactive=False,
            default_tool="claude",
        )

        batch_mode_call = next(
            call
            for call in mock_select.call_args_list
            if call.args[0] == "  Delegation mode for batch review"
        )
        assert batch_mode_call.kwargs["default"] == 2

    @patch("wade.services.init_service.prompts_setup._collect_model_options")
    @patch("wade.services.init_service.prompts_setup._suggest_model_for_tool")
    @patch("wade.ui.prompts.select")
    def test_interactive_with_tool_override(
        self, mock_select: MagicMock, mock_suggest: MagicMock, mock_collect: MagicMock
    ) -> None:
        mock_collect.return_value = ["gemini-3-pro", "gemini-3-flash"]
        mock_suggest.return_value = "gemini-3-pro"
        # installed_tools=["claude", "antigravity-cli"],
        #   tool_options=["claude", "antigravity-cli", "Skip"]
        # plan tool: idx 1 = antigravity-cli
        # plan model: idx 1 = "gemini-3-flash" (2nd option)
        # plan effort: idx 0 = Skip (antigravity-cli supports_effort=True)
        # plan YOLO: idx 0 = Skip (antigravity-cli supports_yolo=True; Skip inherits global)
        # deps: idx 2 = Skip, no effective tool (no default_tool) → mode skipped
        # review_plan/review_implementation/review_batch:
        #   Enable=Yes (idx 0), mode=prompt (idx 0) → skip tool/model
        mock_select.side_effect = [1, 1, 0, 0, 2, 0, 0, 0, 0, 0, 0]
        result = _prompt_command_overrides(["claude", "antigravity-cli"], non_interactive=False)
        assert result["plan"]["tool"] == "antigravity-cli"
        assert result["plan"]["model"] == "gemini-3-flash"
        assert result["deps"] == {}
        assert result["review_plan"] == {"enabled": "true", "mode": "prompt"}
        assert result["review_implementation"] == {"enabled": "true", "mode": "prompt"}
        assert result["review_batch"] == {"enabled": "true", "mode": "prompt"}

    @patch("wade.services.init_service.prompts_setup.AbstractAITool.get")
    @patch("wade.services.init_service.prompts_setup._collect_model_options")
    @patch("wade.services.init_service.prompts_setup._suggest_model_for_tool")
    @patch("wade.ui.prompts.select")
    def test_review_headless_mode_prompts_tool_and_model(
        self,
        mock_select: MagicMock,
        mock_suggest: MagicMock,
        mock_collect: MagicMock,
        mock_get_tool: MagicMock,
    ) -> None:
        """Selecting headless/interactive for review should trigger tool and model prompts."""
        mock_suggest.return_value = "claude-sonnet"
        mock_collect.return_value = ["claude-haiku", "claude-sonnet"]
        # Patch capability to disable effort/yolo so this test stays focused on tool/model
        mock_caps = MagicMock()
        mock_caps.supports_effort = False
        mock_caps.supports_yolo = False
        mock_caps.supported_efforts = None
        mock_get_tool.return_value.capabilities.return_value = mock_caps
        # tool_options=["claude", "Skip"]
        # model_options=["claude-haiku", "claude-sonnet", "Custom...", "Skip"]
        # plan: Skip (idx=1)
        # deps: Skip (idx=1), no effective tool → no mode
        # review_plan: Enable=Yes (idx=0), mode=headless (idx=1),
        #   tool=claude (idx=0), model=claude-sonnet (idx=1)
        # review_implementation: Enable=No (idx=1)
        mock_select.side_effect = [
            1,  # plan: Skip
            1,  # deps: Skip (no mode)
            0,  # review_plan: Enable=Yes
            1,  # review_plan: mode=headless (idx 1 in [prompt, headless, interactive])
            0,  # review_plan: tool=claude
            1,  # review_plan: model=claude-sonnet (idx 1)
            1,  # review_implementation: Enable=No
            1,  # review_batch: Enable=No
        ]
        result = _prompt_command_overrides(["claude"], non_interactive=False)
        assert result["review_plan"]["mode"] == "headless"
        assert result["review_plan"]["tool"] == "claude"
        assert result["review_plan"]["model"] == "claude-sonnet"

    @patch("wade.ui.prompts.select")
    def test_deps_with_default_tool_shows_mode(self, mock_select: MagicMock) -> None:
        """When default_tool is set, deps should show headless/interactive mode prompt."""
        # plan: Skip tool (1), effort=Skip (0), yolo=Skip (0) — inherits default_tool
        # deps: Skip tool (1), mode=headless (0), effort=Skip (0) — no yolo for headless
        # review_plan/review_implementation/review_batch: Enable=No (1)
        mock_select.side_effect = [1, 0, 0, 1, 0, 0, 1, 1, 1]
        result = _prompt_command_overrides(["claude"], non_interactive=False, default_tool="claude")
        assert result["deps"] == {"mode": "headless"}

    @patch("wade.ui.prompts.select")
    def test_deps_mode_excludes_self_review(self, mock_select: MagicMock) -> None:
        """Deps mode prompt must not include 'prompt (self-review)' as an option."""
        # plan: Skip tool (1), effort=Skip (0), yolo=Skip (0) — inherits default_tool
        # deps: Skip tool (1), mode=headless (0), effort=Skip (0) — no yolo for headless
        # review_plan/review_implementation/review_batch: Enable=No (1)
        mock_select.side_effect = [1, 0, 0, 1, 0, 0, 1, 1, 1]
        _prompt_command_overrides(["claude"], non_interactive=False, default_tool="claude")
        # The deps mode call is the 5th select call (index 4):
        # 0=plan-tool 1=plan-effort 2=plan-yolo 3=deps-tool 4=deps-mode 5=deps-effort
        deps_mode_call = mock_select.call_args_list[4]
        mode_options_arg = deps_mode_call.args[1]
        assert "prompt (self-review)" not in mode_options_arg
        assert "headless (AI one-shot)" in mode_options_arg
        assert "interactive (AI session)" in mode_options_arg

    @patch("wade.ui.prompts.select")
    def test_wizard_skips_yolo_for_headless_deps(self, mock_select: MagicMock) -> None:
        """Yolo prompt must not appear when deps mode is headless."""
        prompts_asked: list[str] = []
        call_count = [0]

        def tracking(title: str, _items: object, default: int = 0, **_kw: object) -> int:
            prompts_asked.append(title)
            call_count[0] += 1
            # plan=Skip(1), plan-effort=Skip(0), plan-yolo=Skip(0)
            # deps=Skip(1), deps-mode=headless(0), deps-effort=Skip(0) — no yolo
            # reviews all No(1)
            seq = [1, 0, 0, 1, 0, 0, 1, 1, 1]
            idx = call_count[0] - 1
            return seq[idx] if idx < len(seq) else default

        mock_select.side_effect = tracking
        _prompt_command_overrides(["claude"], non_interactive=False, default_tool="claude")
        pm_count = sum(1 for p in prompts_asked if "Permission" in p)
        assert pm_count == 1  # plan asks permission mode; headless deps does not

    @patch("wade.ui.prompts.select")
    def test_wizard_asks_yolo_for_interactive_deps(self, mock_select: MagicMock) -> None:
        """Yolo prompt appears when deps mode is interactive."""
        prompts_asked: list[str] = []
        call_count = 0

        def capturing_select(title: str, items: list[str], **kw: object) -> int:
            nonlocal call_count
            prompts_asked.append(title)
            call_count += 1
            # plan-tool=Skip(1), plan-effort=Skip(0), plan-yolo=Skip(0)
            # deps-tool=Skip(1), deps-mode=interactive(1), deps-effort=Skip(0), deps-yolo=Skip(0)
            # reviews: Enable=No(1) x3
            sequence = [1, 0, 0, 1, 1, 0, 0, 1, 1, 1]
            return sequence[call_count - 1] if call_count <= len(sequence) else 0

        mock_select.side_effect = capturing_select
        _prompt_command_overrides(["claude"], non_interactive=False, default_tool="claude")
        pm_count = sum(1 for p in prompts_asked if "Permission" in p)
        assert pm_count == 2  # plan asks permission mode; interactive deps also asks

    @patch("wade.services.init_service.prompts_setup.AbstractAITool.get")
    @patch("wade.services.init_service.prompts_setup._collect_model_options")
    @patch("wade.services.init_service.prompts_setup._suggest_model_for_tool")
    @patch("wade.ui.prompts.select")
    def test_wizard_skips_yolo_for_headless_review(
        self,
        mock_select: MagicMock,
        mock_suggest: MagicMock,
        mock_collect: MagicMock,
        mock_get_tool: MagicMock,
    ) -> None:
        """Yolo prompt must not appear for a review command configured as headless."""
        mock_suggest.return_value = "claude-test"
        mock_collect.return_value = ["claude-test"]
        mock_caps = MagicMock()
        mock_caps.supports_effort = False
        mock_caps.supports_yolo = True  # would appear if not for headless gating
        mock_caps.supported_efforts = None
        mock_get_tool.return_value.capabilities.return_value = mock_caps

        prompts_asked: list[str] = []
        call_count = [0]

        def tracking(title: str, _items: object, default: int = 0, **_kw: object) -> int:
            prompts_asked.append(title)
            call_count[0] += 1
            # plan=Skip(1), deps=Skip(1) — no default_tool so no effective_tool
            # review_plan=Yes(0), mode=headless(1), tool=claude(0), model=first(0)
            # review_impl=No(1), review_batch=No(1)
            seq = [1, 1, 0, 1, 0, 0, 1, 1]
            idx = call_count[0] - 1
            return seq[idx] if idx < len(seq) else default

        mock_select.side_effect = tracking
        _prompt_command_overrides(["claude"], non_interactive=False)

        pm_prompts = [p for p in prompts_asked if "Permission" in p]
        assert not pm_prompts

    @patch("wade.ui.prompts.select")
    def test_wizard_always_asks_yolo_for_plan(self, mock_select: MagicMock) -> None:
        """Plan (always interactive) must always get the yolo prompt when tool supports it."""
        prompts_asked: list[str] = []

        def gated_select(title: str, items: list[str], default: int = 0, **_kw: object) -> int:
            prompts_asked.append(title)
            # plan-tool=Skip(1), plan-effort=Skip(0), plan-yolo=Skip(0)
            # deps: no tool (no default_tool) → skip
            # reviews: Enable=No(1)
            seq = [1, 0, 0, 1, 1, 1, 1]
            idx = len(prompts_asked) - 1
            return seq[idx] if idx < len(seq) else default

        mock_select.side_effect = gated_select
        # Use non_interactive=False with default_tool="claude" (supports yolo)
        # plan: Skip tool → inherits claude → effort + permission-mode prompts should appear
        _prompt_command_overrides(["claude"], non_interactive=False, default_tool="claude")

        assert any("Permission" in p for p in prompts_asked)


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
        settings = ProjectSettings(
            main_branch="master",
            branch_prefix="fix",
            issue_label="bug",
            worktrees_dir="../trees",
        )
        _write_config(config_path, "claude", ComplexityModelMapping(), project_settings=settings)
        config = yaml.safe_load(config_path.read_text())
        assert config["project"]["main_branch"] == "master"
        assert config["project"]["merge_strategy"] == "PR"
        assert config["project"]["branch_prefix"] == "fix"
        assert config["project"]["issue_label"] == "bug"
        assert config["project"]["worktrees_dir"] == "../trees"

    def test_with_command_overrides(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        overrides = {
            "plan": {"tool": "antigravity-cli", "model": "gemini-3-pro"},
            "deps": {},
        }
        _write_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            implement_tool="copilot",
            command_overrides=overrides,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["plan"]["tool"] == "antigravity-cli"
        assert config["ai"]["plan"]["model"] == "gemini-3-pro"
        assert "deps" not in config["ai"]
        assert config["ai"]["implement"]["tool"] == "copilot"
        assert "model" not in config["ai"]["implement"]

    def test_with_enabled_overrides(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        overrides = {
            "review_plan": {"enabled": "false"},
            "review_implementation": {"enabled": "true", "tool": "claude", "mode": "prompt"},
            "review_batch": {"enabled": "true", "tool": "copilot", "mode": "headless"},
        }
        _write_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            command_overrides=overrides,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["review_plan"]["enabled"] is False
        assert "tool" not in config["ai"]["review_plan"]
        assert config["ai"]["review_implementation"]["enabled"] is True
        assert config["ai"]["review_implementation"]["tool"] == "claude"
        assert config["ai"]["review_implementation"]["mode"] == "prompt"
        assert config["ai"]["review_batch"]["enabled"] is True
        assert config["ai"]["review_batch"]["tool"] == "copilot"
        assert config["ai"]["review_batch"]["mode"] == "headless"

    def test_with_model_mapping(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        mapping = ComplexityModelMapping(
            easy="haiku", medium="sonnet", complex="sonnet", very_complex="opus"
        )
        _write_config(config_path, "claude", mapping)
        config = yaml.safe_load(config_path.read_text())
        assert config["models"]["claude"]["easy"] == {"model": "haiku", "effort": None}
        assert config["models"]["claude"]["medium"] == {"model": "sonnet", "effort": None}
        assert config["models"]["claude"]["complex"] == {"model": "sonnet", "effort": None}
        assert config["models"]["claude"]["very_complex"] == {"model": "opus", "effort": None}

    def test_write_config_with_only_medium_and_very_complex(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        mapping = ComplexityModelMapping(medium="sonnet", very_complex="opus")
        _write_config(config_path, "claude", mapping)
        config = yaml.safe_load(config_path.read_text())
        assert "models" in config
        assert config["models"]["claude"]["medium"] == {"model": "sonnet", "effort": None}
        assert config["models"]["claude"]["very_complex"] == {"model": "opus", "effort": None}

    def test_no_tool_omits_ai_and_models(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        _write_config(config_path, None, ComplexityModelMapping())
        config = yaml.safe_load(config_path.read_text())
        assert "default_tool" not in config.get("ai", {})
        assert "models" not in config

    def test_with_default_model_and_implement_tool(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        mapping = ComplexityModelMapping(easy="haiku", complex="sonnet")
        _write_config(
            config_path,
            "claude",
            mapping,
            implement_tool="antigravity-cli",
            default_model="gemini-3-pro",
        )
        config = yaml.safe_load(config_path.read_text())
        # default_model written to ai section
        assert config["ai"]["default_model"] == "gemini-3-pro"
        # implement tool written only when different from default_tool
        assert config["ai"]["implement"]["tool"] == "antigravity-cli"
        # models keyed by implement_tool, not default_tool
        assert "antigravity-cli" in config["models"]
        assert "claude" not in config.get("models", {})

    def test_implement_tool_same_as_ai_tool_omits_implement_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        _write_config(config_path, "claude", ComplexityModelMapping(), implement_tool="claude")
        config = yaml.safe_load(config_path.read_text())
        # implement section omitted when tool matches default
        assert "implement" not in config.get("ai", {})


# ---------------------------------------------------------------------------
# _patch_config tests
# ---------------------------------------------------------------------------


class TestPatchConfig:
    def test_force_overwrites_ai_tool(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: antigravity-cli\n")
        _patch_config(config_path, "claude", ComplexityModelMapping(), force=True)
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["default_tool"] == "claude"

    def test_no_force_preserves_ai_tool(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: antigravity-cli\n")
        _patch_config(config_path, "claude", ComplexityModelMapping(), force=False)
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["default_tool"] == "antigravity-cli"

    def test_force_overwrites_default_model(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_model: old-model\n")
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), default_model="new-model", force=True
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["default_model"] == "new-model"

    def test_no_force_preserves_default_model(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_model: old-model\n")
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), default_model="new-model", force=False
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["default_model"] == "old-model"

    def test_force_sets_implement_tool_override(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n")
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            implement_tool="antigravity-cli",
            force=True,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["implement"]["tool"] == "antigravity-cli"

    def test_force_removes_implement_section_when_same_as_default(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  default_tool: claude\n  implement:\n    tool: antigravity-cli\n"
        )
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), implement_tool="claude", force=True
        )
        config = yaml.safe_load(config_path.read_text())
        assert "implement" not in config["ai"]

    def test_force_preserves_other_implement_keys_when_tool_removed(self, tmp_path: Path) -> None:
        """Re-init must strip only the redundant ``tool`` key, not user-set siblings.

        ``ai.implement`` legitimately carries yolo/effort/permission_mode/etc.
        (all honored by the loader); a force re-init where the implement tool
        matches the default previously deleted the whole section, silently
        dropping those keys.
        """
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  default_tool: claude\n"
            "  implement:\n    tool: claude\n    yolo: true\n    effort: xhigh\n"
        )
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), implement_tool="claude", force=True
        )
        config = yaml.safe_load(config_path.read_text())
        # tool (redundant, == default) is removed; effective tool is unchanged
        # (claude → claude), so the tool-specific effort survives alongside yolo.
        assert config["ai"]["implement"] == {"yolo": True, "effort": "xhigh"}

    def test_force_preserves_toolless_implement_section(self, tmp_path: Path) -> None:
        """A hand-authored ``ai.implement`` with no ``tool`` key survives re-init untouched."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  default_tool: claude\n  implement:\n    yolo: true\n"
        )
        _patch_config(config_path, "claude", ComplexityModelMapping(), force=True)
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["implement"] == {"yolo": True}

    def test_force_sets_implement_tool_preserves_siblings(self, tmp_path: Path) -> None:
        """Setting a differing implement tool keeps other implement-scoped keys."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  default_tool: claude\n  implement:\n    yolo: true\n"
        )
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            implement_tool="antigravity-cli",
            force=True,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["implement"] == {"yolo": True, "tool": "antigravity-cli"}

    def test_force_drops_stale_tool_specific_keys_on_tool_change(self, tmp_path: Path) -> None:
        """Tool-specific ``model``/``effort`` are cleared when the impl tool changes.

        Both outrank the freshly-written per-tool mapping (resolve_model /
        resolve_effort precedence) and get rejected as incompatible for the new
        tool (e.g. agy rejects ``xhigh``), so a stale pin silently voids the
        re-init's choice. Portable keys still survive.
        """
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  default_tool: claude\n"
            "  implement:\n    model: claude-opus-4.8\n    effort: xhigh\n    yolo: true\n"
        )
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            implement_tool="antigravity-cli",
            force=True,
        )
        config = yaml.safe_load(config_path.read_text())
        # model + effort (pinned for claude) dropped; yolo (portable) survives; tool set.
        assert config["ai"]["implement"] == {"yolo": True, "tool": "antigravity-cli"}

    def test_force_keeps_tool_specific_keys_when_tool_unchanged(self, tmp_path: Path) -> None:
        """``ai.implement`` model/effort survive re-init when the effective tool is unchanged."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  default_tool: claude\n"
            "  implement:\n    model: claude-opus-4.8\n    effort: xhigh\n    yolo: true\n"
        )
        # implement_tool == default tool → effective tool stays claude → keys kept.
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), implement_tool="claude", force=True
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["implement"] == {
            "model": "claude-opus-4.8",
            "effort": "xhigh",
            "yolo": True,
        }

    def test_force_sets_command_overrides(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n")
        overrides = {"plan": {"tool": "antigravity-cli", "model": "gemini-3-pro"}, "deps": {}}
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), command_overrides=overrides, force=True
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["plan"]["tool"] == "antigravity-cli"
        assert config["ai"]["plan"]["model"] == "gemini-3-pro"
        assert "deps" not in config["ai"]

    def test_force_clears_command_overrides_when_empty(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  default_tool: claude\n  plan:\n    tool: antigravity-cli\n"
        )
        overrides = {"plan": {}, "deps": {}}
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), command_overrides=overrides, force=True
        )
        config = yaml.safe_load(config_path.read_text())
        assert "plan" not in config["ai"]

    def test_no_force_does_not_overwrite_command_overrides(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  default_tool: claude\n  plan:\n    tool: existing-tool\n"
        )
        overrides = {"plan": {"tool": "new-tool"}, "deps": {}}
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            command_overrides=overrides,
            force=False,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["plan"]["tool"] == "existing-tool"

    def test_force_overwrites_models(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  default_tool: claude\nmodels:\n  claude:\n    easy: old-haiku\n"
        )
        mapping = ComplexityModelMapping(easy="new-haiku", complex="sonnet")
        _patch_config(config_path, "claude", mapping, force=True)
        config = yaml.safe_load(config_path.read_text())
        assert config["models"]["claude"]["easy"] == {"model": "new-haiku", "effort": None}
        assert config["models"]["claude"]["complex"] == {"model": "sonnet", "effort": None}

    def test_patch_config_writes_all_four_tiers(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n")
        mapping = ComplexityModelMapping(
            easy="haiku", medium="sonnet", complex="sonnet", very_complex="opus"
        )
        _patch_config(config_path, "claude", mapping, force=True)
        config = yaml.safe_load(config_path.read_text())
        assert config["models"]["claude"]["easy"] == {"model": "haiku", "effort": None}
        assert config["models"]["claude"]["medium"] == {"model": "sonnet", "effort": None}
        assert config["models"]["claude"]["complex"] == {"model": "sonnet", "effort": None}
        assert config["models"]["claude"]["very_complex"] == {"model": "opus", "effort": None}

    def test_no_force_preserves_existing_models(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nai:\n  default_tool: claude\n"
            "models:\n  claude:\n    easy: existing-haiku\n"
        )
        mapping = ComplexityModelMapping(easy="new-haiku", complex="sonnet")
        _patch_config(config_path, "claude", mapping, force=False)
        config = yaml.safe_load(config_path.read_text())
        assert config["models"]["claude"]["easy"] == "existing-haiku"
        assert config["models"]["claude"]["complex"] == {"model": "sonnet", "effort": None}

    def test_models_keyed_by_implement_tool_when_provided(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n")
        mapping = ComplexityModelMapping(easy="haiku", complex="sonnet")
        _patch_config(config_path, "claude", mapping, implement_tool="antigravity-cli", force=True)
        config = yaml.safe_load(config_path.read_text())
        assert "antigravity-cli" in config["models"]
        assert "claude" not in config.get("models", {})

    def test_force_overwrites_project_settings(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nproject:\n  main_branch: master\n  issue_label: old-label\n"
        )
        settings = ProjectSettings(
            main_branch="main",
            issue_label="new-label",
            branch_prefix="feat",
            worktrees_dir="../.worktrees",
        )
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), project_settings=settings, force=True
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["project"]["main_branch"] == "main"
        assert config["project"]["issue_label"] == "new-label"

    def test_no_force_preserves_project_settings(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nproject:\n  main_branch: master\n  issue_label: custom-label\n"
        )
        settings = ProjectSettings(main_branch="main", issue_label="new-label")
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), project_settings=settings, force=False
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["project"]["main_branch"] == "master"
        assert config["project"]["issue_label"] == "custom-label"

    def test_hooks_preserved_through_patch(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nhooks:\n  post_worktree_create: scripts/setup.sh\n")
        _patch_config(config_path, "claude", ComplexityModelMapping(), force=True)
        config = yaml.safe_load(config_path.read_text())
        assert config["hooks"]["post_worktree_create"] == "scripts/setup.sh"

    def test_provider_preserved_through_patch(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nprovider:\n  name: github\n")
        _patch_config(config_path, "claude", ComplexityModelMapping(), force=True)
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["name"] == "github"

    def test_force_sets_mode_and_effort_in_command_overrides(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n")
        overrides = {
            "deps": {"tool": "claude", "mode": "headless"},
            "review_plan": {"tool": "claude", "effort": "low", "mode": "prompt"},
        }
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), command_overrides=overrides, force=True
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["deps"]["mode"] == "headless"
        assert config["ai"]["review_plan"]["effort"] == "low"
        assert config["ai"]["review_plan"]["mode"] == "prompt"

    def test_force_sets_enabled_in_command_overrides(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n")
        overrides = {
            "review_plan": {"enabled": "false"},
            "review_implementation": {"enabled": "true", "tool": "claude", "mode": "prompt"},
            "review_batch": {"enabled": "true", "tool": "copilot", "mode": "headless"},
        }
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), command_overrides=overrides, force=True
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["review_plan"]["enabled"] is False
        assert config["ai"]["review_implementation"]["enabled"] is True
        assert config["ai"]["review_implementation"]["tool"] == "claude"
        assert config["ai"]["review_batch"]["enabled"] is True
        assert config["ai"]["review_batch"]["tool"] == "copilot"
        assert config["ai"]["review_batch"]["mode"] == "headless"

    def test_no_force_sets_enabled_when_section_missing(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n")
        overrides = {
            "review_plan": {"enabled": "false"},
        }
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            command_overrides=overrides,
            force=False,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["review_plan"]["enabled"] is False

    def test_force_sets_review_implementation_overrides(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n")
        overrides = {
            "review_implementation": {"tool": "copilot", "mode": "headless"},
        }
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), command_overrides=overrides, force=True
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["review_implementation"]["tool"] == "copilot"
        assert config["ai"]["review_implementation"]["mode"] == "headless"


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

    def test_update_migrates_old_skills_off_main(self, tmp_git_repo: Path) -> None:
        """update() removes old skill files from main when they exist."""
        from wade.skills.installer import CROSS_TOOL_DIRS, SKILL_FILES

        init(project_root=tmp_git_repo, non_interactive=True)

        # Simulate old installation: manually create skill dirs on main
        skills_dir = tmp_git_repo / ".claude" / "skills"
        for skill_name in SKILL_FILES:
            (skills_dir / skill_name).mkdir(parents=True, exist_ok=True)
            (skills_dir / skill_name / "SKILL.md").write_text("# old\n")
        for cross_dir in CROSS_TOOL_DIRS:
            cross_path = tmp_git_repo / cross_dir
            cross_path.parent.mkdir(parents=True, exist_ok=True)
            cross_path.symlink_to(skills_dir)

        # Run update
        success = update(project_root=tmp_git_repo)
        assert success

        # Verify skills are removed from main
        for skill_name in SKILL_FILES:
            assert not (skills_dir / skill_name).exists()

        # Verify cross-tool symlinks are removed
        for cross_dir in CROSS_TOOL_DIRS:
            assert not (tmp_git_repo / cross_dir).exists()

    def test_init_no_skills_on_main(self, tmp_git_repo: Path) -> None:
        """After init, no skills should be installed on main."""
        from wade.skills.installer import SKILL_FILES

        init(project_root=tmp_git_repo, non_interactive=True)

        skills_dir = tmp_git_repo / ".claude" / "skills"
        for skill_name in SKILL_FILES:
            assert not (skills_dir / skill_name).exists(), (
                f"Skill {skill_name} should NOT be on main after init"
            )


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
        # Simulate an older-style main-checkout pointer (pointer now lives in worktrees)
        write_pointer(tmp_git_repo / "AGENTS.md")
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
        assert (tmp_git_repo / ".wade" / MANIFEST_FILENAME).is_file()
        # AGENTS.md pointer is not written to main during init
        assert not (tmp_git_repo / "AGENTS.md").is_file()

        # Update
        assert update(project_root=tmp_git_repo)

        # Deinit
        assert deinit(project_root=tmp_git_repo, force=True)
        assert not (tmp_git_repo / ".wade.yml").is_file()
        assert not (tmp_git_repo / ".wade" / MANIFEST_FILENAME).is_file()


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

    def test_update_does_not_configure_allowlist_on_main(self, tmp_git_repo: Path) -> None:
        """update() must NOT write project-level allowlist to main — only worktrees get it."""
        with patch(
            "wade.services.init_service.commands.AbstractAITool.detect_installed",
            return_value=[AIToolID.CLAUDE],
        ):
            init(project_root=tmp_git_repo, non_interactive=True)

        with patch("crossby.config.claude_allowlist.configure_allowlist") as mock_allow:
            update(project_root=tmp_git_repo, skip_self_upgrade=True)
            mock_allow.assert_not_called()
        # .claude/settings.json must NOT be created on main during update
        assert not (tmp_git_repo / ".claude" / "settings.json").is_file()

    def test_update_migrates_ai_artifacts_off_main(self, tmp_git_repo: Path) -> None:
        """update() should remove AI tool artifacts from main (migration for older installs)."""
        init(project_root=tmp_git_repo, non_interactive=True)
        # Simulate artifacts left by an older wade version
        from crossby.config.claude_allowlist import configure_allowlist
        from crossby.config.cursor_allowlist import (
            configure_allowlist as configure_cursor_allowlist,
        )

        configure_allowlist(tmp_git_repo, ["wade *"])
        configure_cursor_allowlist(tmp_git_repo, ["wade *"])
        write_pointer(tmp_git_repo / "AGENTS.md")
        (tmp_git_repo / "CLAUDE.md").symlink_to("AGENTS.md")

        assert (tmp_git_repo / ".claude" / "settings.json").is_file()
        assert (tmp_git_repo / ".cursor" / "cli.json").is_file()
        assert (tmp_git_repo / "AGENTS.md").is_file()
        assert (tmp_git_repo / "CLAUDE.md").is_symlink()

        update(project_root=tmp_git_repo, skip_self_upgrade=True)

        # All AI artifacts should be cleaned up from main (both Claude and Cursor)
        assert not (tmp_git_repo / ".claude" / "settings.json").is_file()
        assert not (tmp_git_repo / ".cursor" / "cli.json").is_file()
        assert not (tmp_git_repo / "AGENTS.md").is_file()
        assert not (tmp_git_repo / "CLAUDE.md").exists()

    def test_update_removes_leftover_gemini_artifacts(self, tmp_git_repo: Path) -> None:
        """update() should prune real Gemini-era files left by pre-removal wade versions."""
        init(project_root=tmp_git_repo, non_interactive=True)
        # Simulate concrete files an older wade version wrote under .gemini/
        (tmp_git_repo / ".gemini" / "hooks").mkdir(parents=True)
        (tmp_git_repo / ".gemini" / "policies").mkdir(parents=True)
        (tmp_git_repo / ".gemini" / "hooks" / "plan_write_guard.py").write_text("x")
        (tmp_git_repo / ".gemini" / "hooks" / "worktree_guard.py").write_text("x")
        # wade-owned settings.json carries a PreToolUse hook pointing at the guard script.
        (tmp_git_repo / ".gemini" / "settings.json").write_text(
            '{"hooks": {"PreToolUse": [{"command": ".gemini/hooks/plan_write_guard.py"}]}}'
        )
        (tmp_git_repo / ".gemini" / "policies" / "wade.toml").write_text("x")
        # The cross-tool alias was a symlink (now dangling — its target isn't on main).
        (tmp_git_repo / ".gemini" / "skills").symlink_to("../.claude/skills")

        update(project_root=tmp_git_repo, skip_self_upgrade=True)

        # The concrete files, the alias symlink, and the now-empty .gemini dir
        # should all be gone.
        assert not (tmp_git_repo / ".gemini" / "hooks" / "plan_write_guard.py").exists()
        assert not (tmp_git_repo / ".gemini" / "hooks" / "worktree_guard.py").exists()
        assert not (tmp_git_repo / ".gemini" / "settings.json").exists()
        assert not (tmp_git_repo / ".gemini" / "policies" / "wade.toml").exists()
        assert not (tmp_git_repo / ".gemini" / "skills").is_symlink()
        assert not (tmp_git_repo / ".gemini").exists()

    def test_update_preserves_user_owned_gemini_settings(self, tmp_git_repo: Path) -> None:
        """update() must NOT delete a user's own .gemini/settings.json (no wade hooks)."""
        init(project_root=tmp_git_repo, non_interactive=True)
        gemini = tmp_git_repo / ".gemini"
        gemini.mkdir()
        user_settings = gemini / "settings.json"
        user_content = '{"theme": "dark", "sandbox": true}'
        user_settings.write_text(user_content)

        update(project_root=tmp_git_repo, skip_self_upgrade=True)

        # A generic Gemini CLI config with no wade guard wiring is left untouched,
        # so both the file and its parent .gemini dir survive.
        assert user_settings.is_file()
        assert user_settings.read_text() == user_content
        assert gemini.is_dir()

    def test_update_preserves_non_utf8_gemini_settings(self, tmp_git_repo: Path) -> None:
        """A corrupt/binary .gemini/settings.json must not crash update() and is kept.

        The ownership check reads the file as UTF-8; non-UTF-8 bytes raise
        UnicodeDecodeError (a ValueError, not an OSError). It must be caught so
        `wade update` neither crashes nor deletes a file it cannot prove is ours.
        """
        init(project_root=tmp_git_repo, non_interactive=True)
        gemini = tmp_git_repo / ".gemini"
        gemini.mkdir()
        settings = gemini / "settings.json"
        binary_content = b"\xff\xfe\x00garbage\x80\x81"
        settings.write_bytes(binary_content)

        # Must not raise despite the undecodable bytes.
        update(project_root=tmp_git_repo, skip_self_upgrade=True)

        # Undecodable → not provably wade-owned → preserved untouched.
        assert settings.is_file()
        assert settings.read_bytes() == binary_content
        assert gemini.is_dir()

    def test_skip_self_upgrade_flag(self, tmp_git_repo: Path) -> None:
        """skip_self_upgrade=True should not call _maybe_self_upgrade."""
        init(project_root=tmp_git_repo, non_interactive=True)

        with patch("wade.services.init_service.commands._maybe_self_upgrade") as mock_upgrade:
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
# _maybe_self_upgrade — re-exec loop guard (issue #321)
# ---------------------------------------------------------------------------


class TestMaybeSelfUpgrade:
    """A WADE_SELF_UPGRADE_FROM breadcrumb must cap re-exec at one attempt."""

    def setup_method(self) -> None:
        os.environ.pop("WADE_SELF_UPGRADE_FROM", None)

    def teardown_method(self) -> None:
        os.environ.pop("WADE_SELF_UPGRADE_FROM", None)

    def test_breadcrumb_unchanged_warns_and_clears_env(self) -> None:
        """breadcrumb == current version → no re-exec, a warning, env cleared."""
        from wade import __version__

        os.environ["WADE_SELF_UPGRADE_FROM"] = __version__

        with (
            patch("wade.utils.install.detect_install_method") as mock_detect,
            patch("wade.services.init_service.commands.console") as mock_console,
        ):
            result = _maybe_self_upgrade()

        assert result is False
        mock_console.warn.assert_called_once()
        assert "WADE_SELF_UPGRADE_FROM" not in os.environ
        mock_detect.assert_not_called()

    def test_breadcrumb_differs_clears_env_without_warning(self) -> None:
        """breadcrumb != current version → upgrade succeeded, no re-exec, env cleared."""
        os.environ["WADE_SELF_UPGRADE_FROM"] = "0.0.1"

        with (
            patch("wade.utils.install.detect_install_method") as mock_detect,
            patch("wade.services.init_service.commands.console") as mock_console,
        ):
            result = _maybe_self_upgrade()

        assert result is False
        mock_console.warn.assert_not_called()
        assert "WADE_SELF_UPGRADE_FROM" not in os.environ
        mock_detect.assert_not_called()

    def test_no_breadcrumb_runs_normal_flow(self) -> None:
        """No breadcrumb present → proceeds to the ordinary upgrade check."""
        with patch(
            "wade.utils.install.detect_install_method", return_value=InstallMethod.UNKNOWN
        ) as mock_detect:
            result = _maybe_self_upgrade()

        assert result is False
        mock_detect.assert_called_once()

    def test_newer_version_sets_breadcrumb_before_re_exec(self) -> None:
        """A genuinely newer version sets the breadcrumb before calling re_exec()."""
        from wade import __version__

        captured: dict[str, str] = {}

        def fake_re_exec() -> None:
            captured["breadcrumb"] = os.environ.get("WADE_SELF_UPGRADE_FROM", "")

        with (
            patch("wade.utils.install.detect_install_method", return_value=InstallMethod.UV_TOOL),
            patch("wade.utils.update_check.check_for_update", return_value="99.0.0") as mock_check,
            patch("wade.utils.install.self_upgrade", return_value=True),
            patch("wade.utils.install.re_exec", side_effect=fake_re_exec),
        ):
            result = _maybe_self_upgrade()

        assert result is True
        assert captured["breadcrumb"] == __version__
        mock_check.assert_called_once_with(__version__, force=True)

    def test_at_most_one_re_exec_across_check_upgrade_recheck_chain(self) -> None:
        """End-to-end: check → upgrade → re-exec → post-re-exec re-check triggers
        at most one re_exec call across the whole chain."""

        re_exec_calls = 0

        def fake_re_exec() -> None:
            nonlocal re_exec_calls
            re_exec_calls += 1
            # os.execv inherits the current environment into the "new" process —
            # simulate that by leaving WADE_SELF_UPGRADE_FROM set for the next call.

        with (
            patch("wade.utils.install.detect_install_method", return_value=InstallMethod.UV_TOOL),
            patch("wade.utils.update_check.check_for_update", return_value="99.0.0"),
            patch("wade.utils.install.self_upgrade", return_value=True),
            patch("wade.utils.install.re_exec", side_effect=fake_re_exec),
        ):
            first = _maybe_self_upgrade()  # simulates the pre-upgrade invocation
            second = _maybe_self_upgrade()  # simulates the post-re-exec invocation

        assert first is True
        assert second is False
        assert re_exec_calls == 1
        assert "WADE_SELF_UPGRADE_FROM" not in os.environ


# ---------------------------------------------------------------------------
# Gitignore entries coverage
# ---------------------------------------------------------------------------


class TestGitignoreEntries:
    """Verify GITIGNORE_ENTRIES constants are kept for backward compat (_clean_gitignore)."""

    def test_constants_still_exist(self) -> None:
        """Constants needed by _clean_gitignore for backward compat."""
        assert ".wade.yml" in GITIGNORE_ENTRIES
        assert ".wade/" in GITIGNORE_ENTRIES
        assert ".wade-managed" in GITIGNORE_ENTRIES
        assert GITIGNORE_MARKER_START.startswith("# wade:start")
        assert GITIGNORE_MARKER_END == "# wade:end"


# ---------------------------------------------------------------------------
# _prompt_hooks_setup tests
# ---------------------------------------------------------------------------


class TestPromptHooksSetup:
    def test_non_interactive_returns_defaults(self) -> None:
        result = _prompt_hooks_setup(non_interactive=True)
        assert result["post_worktree_create"] is None
        assert result["copy_to_worktree"] == []

    @patch("wade.ui.prompts.input_prompt")
    def test_interactive_with_values(self, mock_input: MagicMock) -> None:
        mock_input.side_effect = ["scripts/setup-worktree.sh", ".env, .secrets"]
        result = _prompt_hooks_setup(non_interactive=False)
        assert result["post_worktree_create"] == "scripts/setup-worktree.sh"
        assert result["copy_to_worktree"] == [".env", ".secrets"]

    @patch("wade.ui.prompts.input_prompt")
    def test_interactive_empty_skips(self, mock_input: MagicMock) -> None:
        mock_input.side_effect = ["", ""]
        result = _prompt_hooks_setup(non_interactive=False)
        assert result["post_worktree_create"] is None
        assert result["copy_to_worktree"] == []


# ---------------------------------------------------------------------------
# _write_config hooks tests
# ---------------------------------------------------------------------------


class TestWriteConfigHooks:
    def test_write_config_includes_hooks_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        _write_config(config_path, "claude", ComplexityModelMapping())
        config = yaml.safe_load(config_path.read_text())
        assert "hooks" in config

    def test_write_config_with_setup_script(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        hooks = {"post_worktree_create": "scripts/setup.sh", "copy_to_worktree": [".env"]}
        _write_config(config_path, "claude", ComplexityModelMapping(), hooks_setup=hooks)
        config = yaml.safe_load(config_path.read_text())
        assert config["hooks"]["post_worktree_create"] == "scripts/setup.sh"
        assert ".env" in config["hooks"]["copy_to_worktree"]

    def test_write_config_no_commented_hooks(self, tmp_path: Path) -> None:
        """Config should not contain commented-out hooks block."""
        config_path = tmp_path / ".wade.yml"
        _write_config(config_path, "claude", ComplexityModelMapping())
        content = config_path.read_text()
        assert "# hooks:" not in content


# ---------------------------------------------------------------------------
# _patch_config hooks tests
# ---------------------------------------------------------------------------


class TestPatchConfigHooks:
    def test_patch_preserves_existing_hooks(self, tmp_path: Path) -> None:
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
        _patch_config(config_path, "claude", ComplexityModelMapping(), force=True)
        config = yaml.safe_load(config_path.read_text())
        assert ".env" in config["hooks"]["copy_to_worktree"]

    def test_patch_force_sets_hooks_setup(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(yaml.dump({"version": 2}), encoding="utf-8")
        hooks = {"post_worktree_create": "scripts/setup.sh", "copy_to_worktree": [".env"]}
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), hooks_setup=hooks, force=True
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["hooks"]["post_worktree_create"] == "scripts/setup.sh"
        assert ".env" in config["hooks"]["copy_to_worktree"]

    # ---------------------------------------------------------------------------

    def test_init_non_interactive_creates_config(self, tmp_git_repo: Path) -> None:
        """Full init in non-interactive mode should create a valid config."""
        init(project_root=tmp_git_repo, non_interactive=True)

        config = yaml.safe_load((tmp_git_repo / ".wade.yml").read_text())
        assert config["version"] == 2

    @patch("wade.services.init_service.commands._prompt_knowledge_setup")
    def test_init_knowledge_nested_path_not_added_to_copy(
        self,
        mock_knowledge_setup: MagicMock,
        tmp_git_repo: Path,
    ) -> None:
        """init() must NOT add the knowledge file or ratings sidecar to copy_to_worktree.

        #358: they are tracked, so a worktree checks out the committed version;
        copying manufactures the stale snapshot this issue removes.
        """
        mock_knowledge_setup.return_value = {"enabled": True, "path": "docs/LEARNINGS.md"}
        init(project_root=tmp_git_repo, non_interactive=True)

        config = yaml.safe_load((tmp_git_repo / ".wade.yml").read_text())
        copy_list = config["hooks"].get("copy_to_worktree", []) or []
        assert "docs/LEARNINGS.md" not in copy_list
        assert "docs/LEARNINGS.ratings.jsonl" not in copy_list
        assert "docs/LEARNINGS.ratings.yml" not in copy_list


# ---------------------------------------------------------------------------
# Provider setup helpers
# ---------------------------------------------------------------------------


class TestCheckGhAuth:
    @patch("subprocess.run")
    def test_returns_true_when_authenticated(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        assert _check_gh_auth() is True

    @patch(
        "subprocess.run",
        side_effect=__import__("subprocess").CalledProcessError(1, "gh"),
    )
    def test_returns_false_when_not_authenticated(self, _mock_run: MagicMock) -> None:
        assert _check_gh_auth() is False

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_returns_false_when_gh_not_installed(self, _mock_run: MagicMock) -> None:
        assert _check_gh_auth() is False

    @patch(
        "subprocess.run",
        side_effect=__import__("subprocess").TimeoutExpired("gh", 10),
    )
    def test_returns_false_on_timeout(self, _mock_run: MagicMock) -> None:
        assert _check_gh_auth() is False


class TestValidateClickupToken:
    @patch("wade.utils.http.HTTPClient")
    def test_valid_token(self, mock_client_cls: MagicMock) -> None:
        mock_instance = MagicMock()
        mock_client_cls.return_value = mock_instance
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_instance.get.return_value = {"user": {"id": 123}}

        assert _validate_clickup_token("pk_123_abc") is True

    @patch("wade.utils.http.HTTPClient")
    def test_invalid_token(self, mock_client_cls: MagicMock) -> None:
        from wade.utils.http import APIError

        mock_instance = MagicMock()
        mock_client_cls.return_value = mock_instance
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_instance.get.side_effect = APIError(401, "Unauthorized")

        assert _validate_clickup_token("bad_token") is False


class TestSaveTokenToEnv:
    def test_creates_env_file(self, tmp_path: Path) -> None:
        _save_token_to_env(tmp_path, "CLICKUP_API_TOKEN", "pk_123")
        env_path = tmp_path / ".env"
        assert env_path.exists()
        content = env_path.read_text()
        assert "CLICKUP_API_TOKEN=pk_123" in content

    def test_appends_to_existing_env(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("EXISTING_VAR=value\n")
        _save_token_to_env(tmp_path, "CLICKUP_API_TOKEN", "pk_123")
        content = env_path.read_text()
        assert "EXISTING_VAR=value" in content
        assert "CLICKUP_API_TOKEN=pk_123" in content

    def test_skips_if_already_present(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("CLICKUP_API_TOKEN=pk_existing\n")
        _save_token_to_env(tmp_path, "CLICKUP_API_TOKEN", "pk_new")
        content = env_path.read_text()
        # Should NOT have overwritten or duplicated
        assert content.count("CLICKUP_API_TOKEN=") == 1
        assert "pk_existing" in content

    def test_appends_newline_when_file_lacks_trailing_newline(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("FOO=bar")  # no trailing newline
        _save_token_to_env(tmp_path, "CLICKUP_API_TOKEN", "pk_123")
        content = env_path.read_text()
        # Must not merge onto the FOO=bar line
        assert content == "FOO=bar\nCLICKUP_API_TOKEN=pk_123\n"

    def test_no_false_positive_on_substring_env_var(self, tmp_path: Path) -> None:
        """MY_CLICKUP_API_TOKEN= should NOT match CLICKUP_API_TOKEN=."""
        env_path = tmp_path / ".env"
        env_path.write_text("MY_CLICKUP_API_TOKEN=pk_old\n")
        assert _save_token_to_env(tmp_path, "CLICKUP_API_TOKEN", "pk_new") is True
        content = env_path.read_text()
        # Both entries should exist
        assert "MY_CLICKUP_API_TOKEN=pk_old" in content
        assert "CLICKUP_API_TOKEN=pk_new" in content

    def test_returns_false_on_write_failure(self, tmp_path: Path) -> None:
        """Should return False and not crash when .env is not writable."""
        with patch.object(Path, "open", side_effect=OSError("permission denied")):
            result = _save_token_to_env(tmp_path, "CLICKUP_API_TOKEN", "pk_123")
        assert result is False

    def test_returns_true_when_already_present(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("CLICKUP_API_TOKEN=pk_existing\n")
        assert _save_token_to_env(tmp_path, "CLICKUP_API_TOKEN", "pk_new") is True


# ---------------------------------------------------------------------------
# Provider setup prompt
# ---------------------------------------------------------------------------


class TestPromptProviderSetup:
    @pytest.fixture(autouse=True)
    def _no_browser(self) -> None:  # type: ignore[override]
        """Prevent any test from opening a real browser."""
        with patch("webbrowser.open"):
            yield  # type: ignore[misc]

    def test_non_interactive_returns_github(self, tmp_path: Path) -> None:
        result = _prompt_provider_setup(tmp_path, non_interactive=True)
        assert result == {"name": "github"}

    @patch("wade.services.init_service.prompts_setup._check_gh_auth", return_value=True)
    @patch("wade.ui.prompts.select", return_value=0)
    def test_github_already_authed(
        self,
        _mock_select: MagicMock,
        _mock_auth: MagicMock,
        tmp_path: Path,
    ) -> None:
        result = _prompt_provider_setup(tmp_path, non_interactive=False)
        assert result["name"] == "github"

    @patch("wade.services.init_service.prompts_setup._check_gh_auth", return_value=False)
    @patch("wade.ui.prompts.confirm", return_value=False)  # skip auth
    @patch("wade.ui.prompts.select", return_value=0)
    def test_github_not_authed_skip(
        self,
        _mock_select: MagicMock,
        _mock_confirm: MagicMock,
        _mock_auth: MagicMock,
        tmp_path: Path,
    ) -> None:
        result = _prompt_provider_setup(tmp_path, non_interactive=False)
        assert result["name"] == "github"

    @patch("wade.services.init_service.prompts_setup._check_gh_auth")
    @patch("subprocess.run")  # gh auth login
    @patch("wade.ui.prompts.confirm", return_value=True)  # yes, try auth
    @patch("wade.ui.prompts.select", return_value=0)
    def test_github_auth_login_succeeds(
        self,
        _mock_select: MagicMock,
        _mock_confirm: MagicMock,
        _mock_subprocess: MagicMock,
        mock_auth: MagicMock,
        tmp_path: Path,
    ) -> None:
        """User is not authed, runs gh auth login, then _check_gh_auth returns True."""
        mock_auth.side_effect = [False, True]  # first call: not authed, second: authed

        result = _prompt_provider_setup(tmp_path, non_interactive=False)
        assert result["name"] == "github"
        assert mock_auth.call_count == 2

    @patch("wade.services.init_service.prompts_setup._validate_clickup_token", return_value=True)
    @patch("wade.ui.prompts.confirm")
    @patch("wade.ui.prompts.input_prompt")
    @patch("wade.ui.prompts.select", return_value=1)  # ClickUp
    def test_clickup_full_flow(
        self,
        _mock_select: MagicMock,
        mock_input: MagicMock,
        mock_confirm: MagicMock,
        _mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        # input_prompt calls: token, env_var, team_id, list_id, space_id
        mock_input.side_effect = ["pk_123_abc", "CLICKUP_API_TOKEN", "team1", "list1", ""]
        # confirm calls: open browser?, save to .env?
        mock_confirm.side_effect = [False, False]

        result = _prompt_provider_setup(tmp_path, non_interactive=False)

        assert result["name"] == "clickup"
        assert result["api_token_env"] == "CLICKUP_API_TOKEN"
        assert result["settings"]["team_id"] == "team1"
        assert result["settings"]["list_id"] == "list1"
        assert "space_id" not in result["settings"]

    @patch("wade.services.init_service.prompts_setup._validate_clickup_token", return_value=True)
    @patch("wade.ui.prompts.confirm")
    @patch("wade.ui.prompts.input_prompt")
    @patch("wade.ui.prompts.select", return_value=1)
    def test_clickup_with_space_id(
        self,
        _mock_select: MagicMock,
        mock_input: MagicMock,
        mock_confirm: MagicMock,
        _mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_input.side_effect = ["pk_123", "CLICKUP_API_TOKEN", "team1", "list1", "space1"]
        mock_confirm.side_effect = [False, False]

        result = _prompt_provider_setup(tmp_path, non_interactive=False)
        assert result["settings"]["space_id"] == "space1"

    @patch("wade.services.init_service.prompts_setup._validate_clickup_token", return_value=True)
    @patch("wade.services.init_service.prompts_setup._save_token_to_env", return_value=True)
    @patch("wade.ui.prompts.confirm")
    @patch("wade.ui.prompts.input_prompt")
    @patch("wade.ui.prompts.select", return_value=1)
    def test_clickup_save_to_env(
        self,
        _mock_select: MagicMock,
        mock_input: MagicMock,
        mock_confirm: MagicMock,
        mock_save: MagicMock,
        _mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_input.side_effect = ["pk_123", "MY_TOKEN", "team1", "list1", ""]
        mock_confirm.side_effect = [False, True]  # no browser, yes save to .env

        result = _prompt_provider_setup(tmp_path, non_interactive=False)

        assert result["add_env_to_copy"] is True
        mock_save.assert_called_once_with(tmp_path, "MY_TOKEN", "pk_123")

    def test_current_provider_preselects(self, tmp_path: Path) -> None:
        """When current_provider='clickup', default index should be 1."""
        with (
            patch("wade.ui.prompts.select", return_value=1) as mock_select,
            patch(
                "wade.services.init_service.prompts_setup._validate_clickup_token",
                return_value=True,
            ),
            patch("wade.ui.prompts.input_prompt", side_effect=["pk_1", "TOK", "t", "l", ""]),
            patch("wade.ui.prompts.confirm", side_effect=[False, False]),
        ):
            _prompt_provider_setup(tmp_path, non_interactive=False, current_provider="clickup")
            # Verify default=1 was passed (ClickUp pre-selected)
            mock_select.assert_called_once()
            _, kwargs = mock_select.call_args
            assert kwargs.get("default") == 1

    @patch("wade.services.init_service.prompts_setup._validate_clickup_token")
    @patch("wade.ui.prompts.confirm")
    @patch("wade.ui.prompts.input_prompt")
    @patch("wade.ui.prompts.select", return_value=1)
    def test_clickup_token_retry_then_success(
        self,
        _mock_select: MagicMock,
        mock_input: MagicMock,
        mock_confirm: MagicMock,
        mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """First two validation attempts fail, third succeeds."""
        mock_validate.side_effect = [False, False, True]
        # token (attempt 1), token (attempt 2), token (attempt 3), env_var, team, list, space
        mock_input.side_effect = ["bad1", "bad2", "pk_good", "TOK", "t", "l", ""]
        mock_confirm.side_effect = [False, False]

        result = _prompt_provider_setup(tmp_path, non_interactive=False)
        assert result["name"] == "clickup"
        assert mock_validate.call_count == 3

    @patch("wade.services.init_service.prompts_setup._validate_clickup_token", return_value=False)
    @patch("wade.ui.prompts.confirm")
    @patch("wade.ui.prompts.input_prompt")
    @patch("wade.ui.prompts.select", return_value=1)
    def test_clickup_token_all_attempts_fail(
        self,
        _mock_select: MagicMock,
        mock_input: MagicMock,
        mock_confirm: MagicMock,
        _mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """All 3 token validation attempts fail — continues anyway."""
        mock_input.side_effect = ["bad1", "bad2", "bad3", "TOK", "t", "l", ""]
        mock_confirm.side_effect = [False, False]

        result = _prompt_provider_setup(tmp_path, non_interactive=False)
        # Should still return clickup config despite failures
        assert result["name"] == "clickup"

    @patch("wade.services.init_service.prompts_setup._check_gh_auth", return_value=False)
    @patch("wade.ui.prompts.confirm", return_value=True)  # yes, try auth
    @patch("wade.ui.prompts.select", return_value=0)
    def test_github_gh_not_installed_for_login(
        self,
        _mock_select: MagicMock,
        _mock_confirm: MagicMock,
        _mock_auth: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When gh CLI is not installed, gh auth login should not crash."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = _prompt_provider_setup(tmp_path, non_interactive=False)
        assert result["name"] == "github"

    @patch("wade.services.init_service.prompts_setup._validate_clickup_token", return_value=True)
    @patch("wade.ui.prompts.confirm")
    @patch("wade.ui.prompts.input_prompt")
    @patch("wade.ui.prompts.select", return_value=1)
    def test_clickup_invalid_env_var_reprompts(
        self,
        _mock_select: MagicMock,
        mock_input: MagicMock,
        mock_confirm: MagicMock,
        _mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Invalid env var names should be rejected until a valid one is given."""
        # token, bad env var, good env var, team_id, list_id, space_id
        mock_input.side_effect = [
            "pk_123",
            "has spaces!",
            "CLICKUP_TOKEN",
            "team1",
            "list1",
            "",
        ]
        mock_confirm.side_effect = [False, False]

        result = _prompt_provider_setup(tmp_path, non_interactive=False)
        assert result["api_token_env"] == "CLICKUP_TOKEN"

    @patch("wade.services.init_service.prompts_setup._check_gh_auth")
    @patch("subprocess.run")
    @patch("wade.ui.prompts.confirm", return_value=True)
    @patch("wade.ui.prompts.select", return_value=0)
    def test_github_auth_login_recheck_fails(
        self,
        _mock_select: MagicMock,
        _mock_confirm: MagicMock,
        _mock_subprocess: MagicMock,
        mock_auth: MagicMock,
        tmp_path: Path,
    ) -> None:
        """User runs gh auth login but re-check still fails (e.g. user cancelled login)."""
        mock_auth.side_effect = [False, False]

        result = _prompt_provider_setup(tmp_path, non_interactive=False)
        assert result["name"] == "github"
        assert mock_auth.call_count == 2

    @patch("wade.services.init_service.prompts_setup._validate_clickup_token", return_value=True)
    @patch("wade.services.init_service.prompts_setup._save_token_to_env", return_value=False)
    @patch("wade.ui.prompts.confirm")
    @patch("wade.ui.prompts.input_prompt")
    @patch("wade.ui.prompts.select", return_value=1)
    def test_clickup_save_to_env_failure_no_copy(
        self,
        _mock_select: MagicMock,
        mock_input: MagicMock,
        mock_confirm: MagicMock,
        _mock_save: MagicMock,
        _mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When _save_token_to_env returns False, add_env_to_copy should be False."""
        mock_input.side_effect = ["pk_123", "CLICKUP_API_TOKEN", "team1", "list1", ""]
        mock_confirm.side_effect = [False, True]  # no browser, yes save

        result = _prompt_provider_setup(tmp_path, non_interactive=False)
        assert result["add_env_to_copy"] is False

    @patch("wade.services.init_service.prompts_setup._validate_clickup_token", return_value=False)
    @patch("wade.ui.prompts.input_prompt")
    @patch("wade.ui.prompts.select", return_value=1)
    def test_clickup_empty_token_falls_back_to_github(
        self,
        _mock_select: MagicMock,
        mock_input: MagicMock,
        _mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Three empty tokens should fall back to GitHub."""
        mock_input.side_effect = ["", "", ""]

        result = _prompt_provider_setup(tmp_path, non_interactive=False)
        assert result["name"] == "github"


# ---------------------------------------------------------------------------
# Write / patch config provider
# ---------------------------------------------------------------------------


class TestWriteConfigProvider:
    def test_default_github_provider(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        _write_config(config_path, "claude", ComplexityModelMapping())
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["name"] == "github"
        assert "api_token_env" not in config["provider"]
        assert "settings" not in config["provider"]

    def test_github_provider_explicit(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        _write_config(
            config_path, "claude", ComplexityModelMapping(), provider_setup={"name": "github"}
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["name"] == "github"
        assert "api_token_env" not in config["provider"]

    def test_clickup_provider_with_settings(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        provider_setup = {
            "name": "clickup",
            "api_token_env": "CLICKUP_API_TOKEN",
            "settings": {"list_id": "123", "team_id": "456"},
        }
        _write_config(
            config_path, "claude", ComplexityModelMapping(), provider_setup=provider_setup
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["name"] == "clickup"
        assert config["provider"]["api_token_env"] == "CLICKUP_API_TOKEN"
        assert config["provider"]["settings"]["list_id"] == "123"
        assert config["provider"]["settings"]["team_id"] == "456"


class TestPatchConfigProvider:
    def test_patch_adds_provider_when_missing(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\n")
        provider_setup = {
            "name": "clickup",
            "api_token_env": "CLICKUP_API_TOKEN",
            "settings": {"list_id": "123", "team_id": "456"},
        }
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), provider_setup=provider_setup
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["name"] == "clickup"
        assert config["provider"]["settings"]["list_id"] == "123"

    def test_patch_preserves_provider_when_no_setup(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nprovider:\n  name: clickup\n")
        _patch_config(config_path, "claude", ComplexityModelMapping())
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["name"] == "clickup"

    def test_force_overwrites_provider(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nprovider:\n  name: github\n")
        provider_setup = {
            "name": "clickup",
            "api_token_env": "CLICKUP_API_TOKEN",
            "settings": {"list_id": "123", "team_id": "456"},
        }
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            provider_setup=provider_setup,
            force=True,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["name"] == "clickup"

    def test_no_force_preserves_existing_provider(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nprovider:\n  name: github\n")
        provider_setup = {"name": "clickup"}
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            provider_setup=provider_setup,
            force=False,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["name"] == "github"  # preserved

    def test_force_same_provider_preserves_settings(self, tmp_path: Path) -> None:
        """Force re-init of the same provider should NOT delete existing settings."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nprovider:\n  name: clickup\n"
            "  api_token_env: CLICKUP_API_TOKEN\n"
            "  settings:\n    list_id: '123'\n    team_id: '456'\n"
        )
        # Partial setup with same provider name — should not wipe existing keys
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            provider_setup={"name": "clickup"},
            force=True,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["name"] == "clickup"
        assert config["provider"]["api_token_env"] == "CLICKUP_API_TOKEN"
        assert config["provider"]["settings"]["list_id"] == "123"

    def test_force_switch_clickup_to_github_removes_orphan_keys(self, tmp_path: Path) -> None:
        """Switching from ClickUp to GitHub with force should remove api_token_env and settings."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nprovider:\n  name: clickup\n"
            "  api_token_env: CLICKUP_API_TOKEN\n"
            "  settings:\n    list_id: '123'\n    team_id: '456'\n"
        )
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            provider_setup={"name": "github"},
            force=True,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["name"] == "github"
        assert "api_token_env" not in config["provider"]
        assert "settings" not in config["provider"]

    def test_no_force_mismatched_provider_skips_settings(self, tmp_path: Path) -> None:
        """When force=False and existing name is preserved, don't merge mismatched settings."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nprovider:\n  name: github\n")
        provider_setup = {
            "name": "clickup",
            "api_token_env": "CLICKUP_API_TOKEN",
            "settings": {"list_id": "123", "team_id": "456"},
        }
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            provider_setup=provider_setup,
            force=False,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["name"] == "github"  # preserved
        assert "api_token_env" not in config["provider"]  # not backfilled
        assert "settings" not in config["provider"]  # not backfilled

    def test_force_partial_settings_merge(self, tmp_path: Path) -> None:
        """Force-patching with partial settings should overwrite provided keys, preserve others."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nprovider:\n  name: clickup\n"
            "  api_token_env: OLD_TOKEN\n"
            "  settings:\n    list_id: '100'\n    team_id: '200'\n    space_id: '300'\n"
        )
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            provider_setup={
                "name": "clickup",
                "settings": {"list_id": "999"},
            },
            force=True,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["settings"]["list_id"] == "999"  # overwritten
        assert config["provider"]["settings"]["team_id"] == "200"  # preserved
        assert config["provider"]["settings"]["space_id"] == "300"  # preserved
        assert config["provider"]["api_token_env"] == "OLD_TOKEN"  # preserved (not in setup)

    def test_force_update_api_token_env(self, tmp_path: Path) -> None:
        """Force-patching api_token_env should overwrite the existing value."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text(
            "version: 2\nprovider:\n  name: clickup\n  api_token_env: OLD_TOKEN\n"
        )
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            provider_setup={"name": "clickup", "api_token_env": "NEW_TOKEN"},
            force=True,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["api_token_env"] == "NEW_TOKEN"

    def test_patch_handles_scalar_provider(self, tmp_path: Path) -> None:
        """When provider is a scalar (e.g. 'provider: github'), patch should not crash."""
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nprovider: github\n")
        _patch_config(
            config_path,
            "claude",
            ComplexityModelMapping(),
            provider_setup={"name": "clickup", "settings": {"list_id": "1", "team_id": "2"}},
            force=True,
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["provider"]["name"] == "clickup"
        assert config["provider"]["settings"]["list_id"] == "1"


class TestInitProvider:
    def test_init_non_interactive_defaults_to_github(self, tmp_git_repo: Path) -> None:
        init(project_root=tmp_git_repo, non_interactive=True)
        config = yaml.safe_load((tmp_git_repo / ".wade.yml").read_text())
        assert config["provider"]["name"] == "github"


# ---------------------------------------------------------------------------
# _select_or_skip tests
# ---------------------------------------------------------------------------


class TestSelectOrSkip:
    @patch("wade.ui.prompts.select")
    def test_returns_none_when_skip_chosen(self, mock_select: MagicMock) -> None:
        mock_select.return_value = 2  # index of "Skip (use default)" in ["a", "b", "Skip..."]
        result = _select_or_skip("Pick one", ["a", "b"])
        assert result is None

    @patch("wade.ui.prompts.select")
    def test_returns_option_when_chosen(self, mock_select: MagicMock) -> None:
        mock_select.return_value = 1  # "b"
        result = _select_or_skip("Pick one", ["a", "b"])
        assert result == "b"

    @patch("wade.ui.prompts.select")
    def test_current_value_is_pre_selected(self, mock_select: MagicMock) -> None:
        mock_select.return_value = 0  # consume the call
        _select_or_skip("Pick one", ["a", "b", "c"], current="b")
        _, kwargs = mock_select.call_args
        assert kwargs.get("default") == 1  # "b" is index 1 in ["a","b","c","Skip..."]

    @patch("wade.ui.prompts.select")
    def test_skip_is_default_when_no_current(self, mock_select: MagicMock) -> None:
        mock_select.return_value = 2
        _select_or_skip("Pick one", ["a", "b"])
        _, kwargs = mock_select.call_args
        assert kwargs.get("default") == 2  # last index = Skip

    @patch("wade.ui.prompts.select")
    def test_unrecognized_current_defaults_to_skip(self, mock_select: MagicMock) -> None:
        mock_select.return_value = 2
        _select_or_skip("Pick one", ["a", "b"], current="unknown")
        _, kwargs = mock_select.call_args
        assert kwargs.get("default") == 2  # Skip is the fallback


# ---------------------------------------------------------------------------
# _prompt_ai_section yolo + effort tests
# ---------------------------------------------------------------------------


class TestPromptAiSectionYoloEffort:
    @patch("wade.services.init_service.prompts_ai._prompt_default_model", return_value=None)
    @patch("wade.services.init_service.prompts_ai._select_ai_tool", return_value="claude")
    def test_non_interactive_returns_none_extras(
        self, _mock_tool: MagicMock, _mock_model: MagicMock
    ) -> None:
        tool, model, effort, yolo = _prompt_ai_section(None, non_interactive=True)
        assert tool == "claude"
        assert model is None
        assert effort is None
        assert yolo is None

    @patch("wade.ui.prompts.confirm")
    @patch("wade.ui.prompts.select")
    @patch("wade.services.init_service.prompts_ai._prompt_default_model", return_value=None)
    @patch("wade.services.init_service.prompts_ai._select_ai_tool", return_value="claude")
    @patch("wade.services.init_service.prompts_ai.AbstractAITool.get")
    def test_effort_and_yolo_prompted_when_supported(
        self,
        mock_get_tool: MagicMock,
        _mock_tool: MagicMock,
        _mock_model: MagicMock,
        mock_select: MagicMock,
        mock_confirm: MagicMock,
    ) -> None:
        mock_caps = MagicMock()
        mock_caps.supports_effort = True
        mock_caps.supports_yolo = True
        mock_caps.supported_efforts = None
        mock_get_tool.return_value.capabilities.return_value = mock_caps
        mock_select.return_value = 2  # "medium" effort (index 2 = medium in [none, low, medium...])
        mock_confirm.return_value = True  # yolo = True

        tool, _model, effort, yolo = _prompt_ai_section(None, non_interactive=False)
        assert tool == "claude"
        assert effort is not None  # some effort selected
        assert yolo is True

    @patch("wade.ui.prompts.confirm")
    @patch("wade.ui.prompts.select")
    @patch("wade.services.init_service.prompts_ai._prompt_default_model", return_value=None)
    @patch("wade.services.init_service.prompts_ai._select_ai_tool", return_value="claude")
    @patch("wade.services.init_service.prompts_ai.AbstractAITool.get")
    def test_no_effort_or_yolo_when_unsupported(
        self,
        mock_get_tool: MagicMock,
        _mock_tool: MagicMock,
        _mock_model: MagicMock,
        mock_select: MagicMock,
        mock_confirm: MagicMock,
    ) -> None:
        mock_caps = MagicMock()
        mock_caps.supports_effort = False
        mock_caps.supports_yolo = False
        mock_caps.supported_efforts = None
        mock_get_tool.return_value.capabilities.return_value = mock_caps

        _prompt_ai_section(None, non_interactive=False)
        mock_select.assert_not_called()
        mock_confirm.assert_not_called()


# ---------------------------------------------------------------------------
# _prompt_model_mapping per-tier effort tests
# ---------------------------------------------------------------------------


class TestPromptModelMappingPerTierEffort:
    @patch("wade.services.init_service.prompts_ai.AbstractAITool.get")
    @patch("wade.ui.prompts.select")
    def test_effort_prompts_shown_when_tool_supports_effort(
        self, mock_select: MagicMock, mock_get_tool: MagicMock
    ) -> None:
        mock_caps = MagicMock()
        mock_caps.supports_effort = True
        mock_caps.supported_efforts = None
        mock_get_tool.return_value.capabilities.return_value = mock_caps

        mapping = ComplexityModelMapping(
            easy="haiku", medium="haiku", complex="sonnet", very_complex="opus"
        )
        # Always return default index (accept current selection)
        mock_select.side_effect = lambda title, items, default=0, **kw: default

        result = _prompt_model_mapping("claude", mapping, non_interactive=False)

        # 4 model selects + 4 effort selects = 8 calls
        assert mock_select.call_count == 8
        assert result.easy == "haiku"

    @patch("wade.services.init_service.prompts_ai.AbstractAITool.get")
    @patch("wade.ui.prompts.select")
    def test_no_effort_prompts_when_tool_does_not_support(
        self, mock_select: MagicMock, mock_get_tool: MagicMock
    ) -> None:
        mock_caps = MagicMock()
        mock_caps.supports_effort = False
        mock_caps.supported_efforts = None
        mock_get_tool.return_value.capabilities.return_value = mock_caps

        mapping = ComplexityModelMapping(
            easy="haiku", medium="haiku", complex="sonnet", very_complex="opus"
        )
        mock_select.side_effect = lambda title, items, default=0, **kw: default

        _prompt_model_mapping("claude", mapping, non_interactive=False)

        # Only 4 model selects — no effort prompts
        assert mock_select.call_count == 4

    @patch("wade.services.init_service.prompts_ai.AbstractAITool.get")
    @patch("wade.ui.prompts.select")
    def test_effort_value_stored_in_mapping(
        self, mock_select: MagicMock, mock_get_tool: MagicMock
    ) -> None:
        mock_caps = MagicMock()
        mock_caps.supports_effort = True
        mock_caps.supported_efforts = None
        mock_get_tool.return_value.capabilities.return_value = mock_caps

        mapping = ComplexityModelMapping(
            easy="haiku", medium="haiku", complex="sonnet", very_complex="opus"
        )

        from crossby.models.ai import EffortLevel

        effort_values = [e.value for e in EffortLevel]
        call_count = 0

        def fake_select(title: str, items: list[str], default: int = 0, **kw: object) -> int:
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0 and "Effort" in title:
                # Return index 1 = first real effort level (e.g. "low")
                return 1
            return default

        mock_select.side_effect = fake_select

        result = _prompt_model_mapping("claude", mapping, non_interactive=False)
        # At least one tier should have an effort set
        tier_efforts = [
            result.easy_effort,
            result.medium_effort,
            result.complex_effort,
            result.very_complex_effort,
        ]
        assert any(e is not None for e in tier_efforts)
        assert all(e in effort_values or e is None for e in tier_efforts)


# ---------------------------------------------------------------------------
# _show_init_summary tests
# ---------------------------------------------------------------------------


class TestShowInitSummary:
    def test_renders_without_error_minimal(self) -> None:
        with patch("wade.services.init_service.manifest.console") as mock_console:
            _show_init_summary(
                provider_setup={"name": "github"},
                project_settings=ProjectSettings(
                    main_branch="main",
                    branch_prefix="feat",
                    worktrees_dir="../.worktrees",
                ),
                selected_tool=None,
                default_model=None,
                default_effort=None,
                default_yolo=None,
                implementation_setup={},
                command_overrides={},
                hooks_setup={},
                knowledge_setup={},
            )
        kv_calls = {call.args[0]: call.args[1] for call in mock_console.kv.call_args_list}
        assert kv_calls.get("Provider") == "github"
        assert kv_calls.get("Main branch") == "main"
        assert kv_calls.get("AI tool") == "(not set)"
        assert "Default YOLO" not in kv_calls

    def test_renders_with_full_data(self) -> None:
        mapping = ComplexityModelMapping(
            easy="haiku",
            medium="sonnet",
            complex="sonnet",
            very_complex="opus",
            complex_effort="high",
        )
        with patch("wade.services.init_service.manifest.console") as mock_console:
            _show_init_summary(
                provider_setup={"name": "github"},
                project_settings=ProjectSettings(
                    main_branch="main",
                    branch_prefix="fix",
                    worktrees_dir="../trees",
                ),
                selected_tool="claude",
                default_model="claude-sonnet-4-6",
                default_effort="medium",
                default_yolo=True,
                implementation_setup={"tool": "claude", "model_mapping": mapping},
                command_overrides={
                    "plan": {"tool": "antigravity-cli", "model": "gemini-3-pro"},
                    "deps": {"mode": "headless"},
                    "review_plan": {"enabled": "true", "mode": "prompt"},
                },
                hooks_setup={
                    "post_worktree_create": "scripts/setup.sh",
                    "copy_to_worktree": [".env"],
                },
                knowledge_setup={"enabled": True, "path": "docs/KNOWLEDGE.md"},
            )
        kv_calls = {call.args[0]: call.args[1] for call in mock_console.kv.call_args_list}
        assert kv_calls.get("AI tool") == "claude"
        assert kv_calls.get("Default YOLO") == "true"
        assert kv_calls.get("Default effort") == "medium"
        assert kv_calls.get("Knowledge file") == "docs/KNOWLEDGE.md"
        # Verify per-tier effort shows up in implementation mapping output
        assert any("high" in str(v) for v in kv_calls.values())
        # Verify per-command overrides are shown
        plan_entry = kv_calls.get("  plan")
        assert plan_entry is not None and "antigravity-cli" in plan_entry


# ---------------------------------------------------------------------------
# _write_config default_yolo tests
# ---------------------------------------------------------------------------


class TestWriteConfigYolo:
    def test_default_yolo_true_written_to_ai_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        _write_config(config_path, "claude", ComplexityModelMapping(), default_yolo=True)
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["yolo"] is True

    def test_default_yolo_false_written_to_ai_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        _write_config(config_path, "claude", ComplexityModelMapping(), default_yolo=False)
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["yolo"] is False

    def test_default_yolo_none_omitted_from_ai_section(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        _write_config(config_path, "claude", ComplexityModelMapping(), default_yolo=None)
        config = yaml.safe_load(config_path.read_text())
        assert "yolo" not in config["ai"]

    def test_per_command_yolo_in_overrides(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        overrides = {"plan": {"tool": "claude", "yolo": "true"}, "deps": {}}
        _write_config(config_path, "claude", ComplexityModelMapping(), command_overrides=overrides)
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["plan"]["yolo"] is True


# ---------------------------------------------------------------------------
# _patch_config default_yolo tests
# ---------------------------------------------------------------------------


class TestEnsureMarkdownFile:
    """Init's markdown-file write phase."""

    def test_creates_default_when_missing(self, tmp_path: Path) -> None:
        from wade.services.init_service import _ensure_markdown_file

        _ensure_markdown_file(tmp_path, {"path": "ISSUES.md"})
        assert (tmp_path / "ISSUES.md").is_file()
        assert "# Wade Issues" in (tmp_path / "ISSUES.md").read_text(encoding="utf-8")

    def test_no_op_when_file_already_exists(self, tmp_path: Path) -> None:
        from wade.services.init_service import _ensure_markdown_file

        existing = tmp_path / "ISSUES.md"
        existing.write_text("existing content\n", encoding="utf-8")
        _ensure_markdown_file(tmp_path, {"path": "ISSUES.md"})
        assert existing.read_text(encoding="utf-8") == "existing content\n"

    def test_rejects_directory_at_path(self, tmp_path: Path) -> None:
        from wade.services.init_service import _ensure_markdown_file

        # A directory exists where the file should go.
        (tmp_path / "ISSUES.md").mkdir()
        with pytest.raises(ValueError, match="not a regular file"):
            _ensure_markdown_file(tmp_path, {"path": "ISSUES.md"})


class TestPatchConfigYolo:
    def test_force_writes_yolo_true(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n")
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), default_yolo=True, force=True
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["yolo"] is True

    def test_force_overwrites_existing_yolo(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n  yolo: true\n")
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), default_yolo=False, force=True
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["yolo"] is False

    def test_no_force_preserves_existing_yolo(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n  yolo: true\n")
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), default_yolo=False, force=False
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["yolo"] is True  # preserved

    def test_no_force_writes_yolo_when_absent(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nai:\n  default_tool: claude\n")
        _patch_config(
            config_path, "claude", ComplexityModelMapping(), default_yolo=True, force=False
        )
        config = yaml.safe_load(config_path.read_text())
        assert config["ai"]["yolo"] is True
