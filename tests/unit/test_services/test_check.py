"""Tests for check service — worktree safety and config validation."""

from __future__ import annotations

from pathlib import Path

from wade.services.check_service import (
    CheckExitCode,
    CheckStatus,
    ConfigExitCode,
    check_worktree,
    validate_config,
)

# ---------------------------------------------------------------------------
# Worktree check tests
# ---------------------------------------------------------------------------


class TestCheckWorktree:
    def test_not_in_git_repo(self, tmp_path: Path) -> None:
        result = check_worktree(tmp_path)
        assert result.status == CheckStatus.NOT_IN_GIT_REPO
        assert result.exit_code == CheckExitCode.NOT_IN_GIT_REPO
        assert "NOT_IN_GIT_REPO" in result.format_output()

    def test_in_main_checkout(self, tmp_git_repo: Path) -> None:
        result = check_worktree(tmp_git_repo)
        assert result.status == CheckStatus.IN_MAIN_CHECKOUT
        assert result.exit_code == CheckExitCode.IN_MAIN_CHECKOUT
        output = result.format_output()
        assert "IN_MAIN_CHECKOUT" in output
        assert "toplevel=" in output
        assert "branch=" in output

    def test_in_worktree(self, tmp_git_repo: Path) -> None:
        import subprocess

        # Create a worktree
        wt_path = tmp_git_repo.parent / "worktree"
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", "test-branch"],
            cwd=tmp_git_repo,
            capture_output=True,
            check=True,
        )

        result = check_worktree(wt_path)
        assert result.status == CheckStatus.IN_WORKTREE
        assert result.exit_code == CheckExitCode.IN_WORKTREE
        output = result.format_output()
        assert "IN_WORKTREE" in output
        assert "toplevel=" in output
        assert "branch=" in output
        assert "gitdir=" in output


# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------


class TestValidateConfig:
    def test_config_not_found(self, tmp_path: Path) -> None:
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.NOT_FOUND
        assert not result.is_valid
        output = result.format_output()
        assert "CONFIG_NOT_FOUND" in output
        assert "wade init" in output

    def test_valid_config(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nproject:\n  main_branch: main\n  merge_strategy: PR\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.VALID
        assert result.is_valid
        output = result.format_output()
        assert "VALID_CONFIG" in output
        assert f"path={config}" in output

    def test_empty_config_is_valid(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("")
        result = validate_config(tmp_path)
        assert result.is_valid

    def test_minimal_version_only(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\n")
        result = validate_config(tmp_path)
        assert result.is_valid

    def test_invalid_version(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 99\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("version" in e for e in result.errors)

    def test_invalid_merge_strategy(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nproject:\n  merge_strategy: squash\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("merge_strategy" in e for e in result.errors)
        assert any("PR" in e and "direct" in e for e in result.errors)

    def test_invalid_ai_tool(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  default_tool: chatgpt\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("default_tool" in e for e in result.errors)

    def test_invalid_command_tool(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  plan:\n    tool: chatgpt\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("ai.plan.tool" in e for e in result.errors)

    def test_unsupported_top_level_key(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nunknown_key: value\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("unsupported key" in e for e in result.errors)

    def test_invalid_models_tool(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nmodels:\n  chatgpt:\n    easy: gpt-4\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("models.chatgpt" in e for e in result.errors)

    def test_invalid_complexity_key(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nmodels:\n  claude:\n    ultra: claude-ultra\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("models.claude.ultra" in e for e in result.errors)

    def test_empty_models_block(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nmodels:\n")
        result = validate_config(tmp_path)
        # Empty models parsed as None by YAML, not as empty dict
        # So this should be valid (models key exists but is null)
        # Actually, yaml.safe_load("models:\n") gives {"models": None}
        # Our validator checks `if models is not None`
        assert result.is_valid

    def test_empty_copy_to_worktree(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nhooks:\n  copy_to_worktree: []\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("copy_to_worktree" in e and "empty" in e for e in result.errors)

    def test_default_model_is_valid_ai_key(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text(
            "version: 2\nai:\n  default_tool: claude\n  default_model: claude-sonnet-4.6\n"
        )
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_valid_full_config(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text(
            "version: 2\n"
            "project:\n"
            "  main_branch: main\n"
            "  issue_label: feature-plan\n"
            "  worktrees_dir: ../.worktrees\n"
            "  branch_prefix: feat\n"
            "  merge_strategy: PR\n"
            "ai:\n"
            "  default_tool: copilot\n"
            "  default_model: claude-sonnet-4.6\n"
            "  plan:\n"
            "    tool: claude\n"
            "    model: ''\n"
            "models:\n"
            "  copilot:\n"
            "    easy: claude-haiku-4.5\n"
            "    medium: claude-haiku-4.5\n"
            "    complex: claude-sonnet-4.6\n"
            "    very_complex: claude-opus-4.6\n"
            "provider:\n"
            "  name: github\n"
            "permissions:\n"
            "  allowed_commands:\n"
            "    - wade *\n"
            "    - ./scripts/check.sh *\n"
            "hooks:\n"
            "  post_worktree_create: scripts/setup.sh\n"
            "  copy_to_worktree:\n"
            "    - .env\n"
        )
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_valid_permissions_section(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text(
            "version: 2\npermissions:\n  allowed_commands:\n"
            "    - wade *\n    - ./scripts/check.sh *\n"
        )
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_invalid_permissions_not_a_list(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\npermissions:\n  allowed_commands: wade\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("allowed_commands" in e and "list" in e for e in result.errors)

    def test_invalid_provider_settings_not_dict(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nprovider:\n  name: github\n  settings:\n    - item1\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("provider.settings" in e and "mapping" in e for e in result.errors)

    def test_valid_provider_with_settings(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text(
            "version: 2\nprovider:\n  name: clickup\n"
            "  api_token_env: CLICKUP_API_TOKEN\n"
            "  settings:\n    list_id: '901'\n    team_id: '123'\n"
        )
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_invalid_permissions_unsupported_key(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\npermissions:\n  forbidden_commands: []\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("permissions.forbidden_commands" in e for e in result.errors)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("{{invalid yaml::")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("YAML" in e or "parse" in e for e in result.errors)

    def test_valid_ai_effort_and_review_keys(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text(
            "version: 2\nai:\n  effort: high\n  review_plan:\n    tool: claude\n"
            "  review_implementation:\n    tool: copilot\n"
        )
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_invalid_review_plan_tool(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  review_plan:\n    tool: nonexistent\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("ai.review_plan.tool" in e for e in result.errors)

    def test_invalid_review_implementation_tool(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  review_implementation:\n    tool: bad\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("ai.review_implementation.tool" in e for e in result.errors)

    def test_output_format_invalid(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 99\n")
        result = validate_config(tmp_path)
        output = result.format_output()
        assert "INVALID_CONFIG" in output
        assert f"path={config}" in output
        assert "error:" in output

    def test_valid_config_with_knowledge_section(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nknowledge:\n  enabled: true\n  path: KNOWLEDGE.md\n")
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_valid_config_with_knowledge_enabled_only(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nknowledge:\n  enabled: true\n")
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_invalid_knowledge_enabled_not_bool(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nknowledge:\n  enabled: 'yes'\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("knowledge.enabled" in e and "boolean" in e for e in result.errors)

    def test_invalid_knowledge_path_not_string(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nknowledge:\n  path: 123\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("knowledge.path" in e and "string" in e for e in result.errors)

    def test_invalid_knowledge_unsupported_key(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nknowledge:\n  enabled: true\n  foo: bar\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("knowledge.foo" in e and "unsupported" in e for e in result.errors)

    def test_invalid_knowledge_not_mapping(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nknowledge: true\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("knowledge" in e and "mapping" in e for e in result.errors)
