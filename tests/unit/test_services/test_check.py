"""Tests for check service — worktree safety and config validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from wade.models.config import (
    AI_COMMAND_NAMES,
    AICommandConfig,
    AIConfig,
    CommitMsgConfig,
    HooksConfig,
    PostToolUseConfig,
    PreCommitConfig,
)
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


def _add_worktree(repo: Path, name: str = "worktree", branch: str = "probe-branch") -> Path:
    import subprocess

    wt_path = repo.parent / name
    subprocess.run(
        ["git", "worktree", "add", str(wt_path), "-b", branch],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return wt_path


def _probe_artefacts(*dirs: Path) -> list[Path]:
    """Return any lingering ``.wade-write-probe-*`` files under *dirs*."""
    found: list[Path] = []
    for d in dirs:
        if d.is_dir():
            found.extend(d.glob(".wade-write-probe-*"))
    return found


class TestWorktreeGitReadinessProbe:
    """The IN_WORKTREE branch probes that out-of-root git metadata is writable."""

    def test_writable_worktree_passes_and_leaves_no_artefacts(self, tmp_git_repo: Path) -> None:
        from wade.git import repo as git_repo

        wt_path = _add_worktree(tmp_git_repo)
        result = check_worktree(wt_path)
        assert result.status == CheckStatus.IN_WORKTREE
        assert result.exit_code == CheckExitCode.IN_WORKTREE
        assert result.blocked_paths == []

        # Cleanup ran — the created probe files were removed from both git dirs.
        private = Path(git_repo.get_git_dir(wt_path) or "")
        common = Path(git_repo.get_git_common_dir(wt_path) or "")
        if not common.is_absolute():
            common = wt_path / common
        assert _probe_artefacts(private, common) == []

    def test_blocked_when_write_denied_names_path_and_leaves_no_artefacts(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate an OS-sandbox denial by making the probe WRITE raise — not a
        # chmod (CI often runs as root, where mode bits don't block writes).
        wt_path = _add_worktree(tmp_git_repo)

        orig_write_text = Path.write_text

        def deny_probe_write(self: Path, *args: object, **kwargs: object) -> int:
            if self.name.startswith(".wade-write-probe-"):
                raise OSError("Operation not permitted")
            return orig_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "write_text", deny_probe_write)

        result = check_worktree(wt_path)
        assert result.status == CheckStatus.WORKTREE_GIT_BLOCKED
        assert result.exit_code == CheckExitCode.WORKTREE_GIT_BLOCKED
        assert result.exit_code == 3  # documented free exit code (0/1/2 taken)
        assert result.blocked_paths, "must name at least one blocked metadata dir"

        output = result.format_output()
        assert "WORKTREE_GIT_BLOCKED" in output
        # Every blocked path is surfaced, plus an actionable relaunch hint.
        for blocked in result.blocked_paths:
            assert f"blocked={blocked}" in output
        assert "relaunch" in output.lower()

        # No probe artefacts left behind (the write raised before creating one).
        monkeypatch.undo()
        from wade.git import repo as git_repo

        private = Path(git_repo.get_git_dir(wt_path) or "")
        common = Path(git_repo.get_git_common_dir(wt_path) or "")
        if not common.is_absolute():
            common = wt_path / common
        assert _probe_artefacts(private, common) == []

    def test_main_checkout_is_not_probed(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A main checkout never runs the probe, so a would-be-denying write patch
        # cannot flip it to WORKTREE_GIT_BLOCKED.
        def boom(self: Path, *args: object, **kwargs: object) -> int:
            if self.name.startswith(".wade-write-probe-"):
                raise AssertionError("probe must not run in a main checkout")
            raise AssertionError("unexpected probe write in a main checkout")

        monkeypatch.setattr(Path, "write_text", boom)

        result = check_worktree(tmp_git_repo)
        assert result.status == CheckStatus.IN_MAIN_CHECKOUT
        assert result.exit_code == CheckExitCode.IN_MAIN_CHECKOUT


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
        # PR is the only allowed strategy now that `direct` is retired (#357).
        assert any("PR" in e for e in result.errors)

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

    def test_removed_gemini_tool_gives_actionable_message(self, tmp_path: Path) -> None:
        """A stale ``default_tool: gemini`` yields a clear switch-to hint, not a crash."""
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  default_tool: gemini\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("no longer supported" in e and "antigravity-cli" in e for e in result.errors)

    def test_valid_command_timeout(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  review_plan:\n    timeout: 300\n")
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_invalid_command_timeout(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  review_plan:\n    timeout: 0\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("ai.review_plan.timeout" in e for e in result.errors)

    def test_valid_network_access_global_and_command(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text(
            "version: 2\nai:\n  network_access: true\n  implement:\n    network_access: false\n"
        )
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_invalid_global_network_access(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  network_access: sometimes\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("ai.network_access: must be true or false" in e for e in result.errors)

    def test_invalid_command_network_access(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  implement:\n    network_access: 1\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("ai.implement.network_access: must be true or false" in e for e in result.errors)

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

    def test_valid_done_section(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text(
            "version: 2\n"
            "done:\n"
            "  require_pr_summary: true\n"
            "  require_sync: false\n"
            "  require_review: true\n"
            "  require_resolved_threads: false\n"
            "  pre_push_backstop: true\n"
        )
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_unknown_done_key_rejected(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\ndone:\n  require_everything: true\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("done.require_everything" in e for e in result.errors)

    def test_non_bool_done_value_rejected(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\ndone:\n  require_sync: sometimes\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("done.require_sync" in e and "true or false" in e for e in result.errors)

    def test_null_done_value_rejected(self, tmp_path: Path) -> None:
        # An explicit null (`require_sync:` with no value) is a user mistake, not
        # an unset default — `wade check` must flag it as a non-bool.
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\ndone:\n  require_sync:\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("done.require_sync" in e and "true or false" in e for e in result.errors)

    def test_max_review_passes_positive_int_accepted(self, tmp_path: Path) -> None:
        # `max_review_passes` is an int, not a bool — a positive int must pass
        # `wade check` (the old bool-only validator would have flagged it).
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\ndone:\n  max_review_passes: 3\n")
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_max_review_passes_zero_rejected(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\ndone:\n  max_review_passes: 0\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("done.max_review_passes" in e and "positive integer" in e for e in result.errors)

    def test_max_review_passes_bool_rejected(self, tmp_path: Path) -> None:
        # `true` is an int subclass — it must NOT sneak through as a valid count.
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\ndone:\n  max_review_passes: true\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("done.max_review_passes" in e and "positive integer" in e for e in result.errors)

    def test_valid_hooks_quality_gate_sections(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text(
            "version: 2\n"
            "hooks:\n"
            "  pre_commit:\n"
            "    lint: ./scripts/check.sh --lint\n"
            "    test: ./scripts/test.sh\n"
            "  commit_msg:\n"
            "    conventional: true\n"
            "  post_tool_use:\n"
            "    enabled: true\n"
            "    lint_cmd: ruff check\n"
            "    timeout: 15\n"
        )
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_pre_commit_lint_must_be_string(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nhooks:\n  pre_commit:\n    lint: true\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("hooks.pre_commit.lint" in e for e in result.errors)

    def test_commit_msg_conventional_must_be_bool(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nhooks:\n  commit_msg:\n    conventional: yes-please\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any(
            "hooks.commit_msg.conventional" in e and "true or false" in e for e in result.errors
        )

    def test_post_tool_use_timeout_must_be_positive_int(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nhooks:\n  post_tool_use:\n    timeout: -3\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any(
            "hooks.post_tool_use.timeout" in e and "positive integer" in e for e in result.errors
        )

    def test_post_tool_use_timeout_rejects_bool(self, tmp_path: Path) -> None:
        # bool is an int subclass — a YAML `true` must not sneak through as 1.
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nhooks:\n  post_tool_use:\n    timeout: true\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("hooks.post_tool_use.timeout" in e for e in result.errors)

    def test_unknown_pre_commit_key_rejected(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nhooks:\n  pre_commit:\n    lynt: x\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("hooks.pre_commit.lynt" in e and "unsupported key" in e for e in result.errors)

    def test_hooks_subsection_must_be_mapping(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nhooks:\n  pre_commit: just-a-string\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("hooks.pre_commit" in e and "mapping" in e for e in result.errors)

    def test_hooks_valid_keys_stay_in_sync_with_model(self, tmp_path: Path) -> None:
        """Every ``HooksConfig`` field must be an accepted top-level ``hooks`` key.

        The validator derives ``_VALID_HOOKS_KEYS`` from the model, so a field
        added to ``HooksConfig`` must never be rejected as unsupported (#368 /
        knowledge ca245d6a — config-key validity in three places must not drift).
        """
        config = tmp_path / ".wade.yml"
        for field in HooksConfig.model_fields:
            config.write_text(f"version: 2\nhooks:\n  {field}: null\n")
            result = validate_config(tmp_path)
            assert not any(f"hooks.{field}: unsupported key" in e for e in result.errors), (
                f"model field '{field}' rejected as an unsupported hooks key"
            )

    def test_hooks_subsection_keys_stay_in_sync_with_models(self, tmp_path: Path) -> None:
        """Each nested subsection's allowlist is derived from its Pydantic model."""
        cases = {
            "pre_commit": PreCommitConfig,
            "commit_msg": CommitMsgConfig,
            "post_tool_use": PostToolUseConfig,
        }
        config = tmp_path / ".wade.yml"
        for section, model in cases.items():
            for field in model.model_fields:
                config.write_text(f"version: 2\nhooks:\n  {section}:\n    {field}: null\n")
                result = validate_config(tmp_path)
                assert not any(
                    f"hooks.{section}.{field}: unsupported key" in e for e in result.errors
                ), f"model field '{field}' rejected as unsupported hooks.{section} key"

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

    def test_valid_review_batch_and_yolo_keys(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text(
            "version: 2\n"
            "ai:\n"
            "  yolo: true\n"
            "  review_batch:\n"
            "    tool: claude\n"
            "    mode: headless\n"
            "    enabled: false\n"
            "    yolo: true\n"
        )
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_valid_review_pr_comments_keys(self, tmp_path: Path) -> None:
        """The dedicated ``ai.review_pr_comments`` section (#389) is accepted.

        Adding the section name to ``AI_COMMAND_NAMES`` + the field to
        ``AIConfig`` is enough — the validator derives its allowlists from the
        models (#368), so ``wade check`` needs no ``check_service`` change.
        """
        config = tmp_path / ".wade.yml"
        config.write_text(
            "version: 2\n"
            "ai:\n"
            "  review_pr_comments:\n"
            "    tool: claude\n"
            "    model: claude-sonnet-5\n"
            "    effort: high\n"
            "    mode: interactive\n"
            "    permission_mode: yolo\n"
            "    yolo: true\n"
            "    enabled: true\n"
            "    timeout: 600\n"
        )
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_invalid_review_pr_comments_tool(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  review_pr_comments:\n    tool: nonexistent\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("ai.review_pr_comments.tool" in e for e in result.errors)

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

    def test_invalid_review_batch_mode(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  review_batch:\n    mode: invalid\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("ai.review_batch.mode" in e for e in result.errors)

    def test_invalid_ai_yolo_type(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  yolo: sometimes\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("ai.yolo" in e for e in result.errors)

    def test_invalid_ai_command_unknown_key(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  review_batch:\n    unexpected: true\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("ai.review_batch.unexpected" in e for e in result.errors)

    def test_invalid_ai_top_level_unknown_key(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  bogus_setting: true\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("ai.bogus_setting: unsupported key" in e for e in result.errors)

    def test_valid_command_permission_mode(self, tmp_path: Path) -> None:
        """``ai.<cmd>.permission_mode`` is a supported key (issue #368).

        ``wade init`` writes this per-command, so validating it must not emit a
        spurious "unsupported key" warning.
        """
        config = tmp_path / ".wade.yml"
        config.write_text(
            "version: 2\n"
            "ai:\n"
            "  plan:\n"
            "    permission_mode: accept-edits\n"
            "  review_batch:\n"
            "    permission_mode: yolo\n"
        )
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_valid_top_level_permission_mode(self, tmp_path: Path) -> None:
        """The global ``ai.permission_mode`` key is supported (issue #368)."""
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nai:\n  permission_mode: auto\n")
        result = validate_config(tmp_path)
        assert result.is_valid, f"Errors: {result.errors}"

    def test_ai_command_valid_keys_stay_in_sync_with_model(self, tmp_path: Path) -> None:
        """Every ``AICommandConfig`` field must be an accepted per-command key.

        Guards against the validator's allowlist drifting from the Pydantic
        schema (issue #368): a field added to the model must not be rejected as
        an "unsupported key". Uses a null value so only key acceptance — not
        per-field value validation — is exercised.
        """
        config = tmp_path / ".wade.yml"
        for field in AICommandConfig.model_fields:
            config.write_text(f"version: 2\nai:\n  plan:\n    {field}: null\n")
            result = validate_config(tmp_path)
            assert not any(f"ai.plan.{field}: unsupported key" in e for e in result.errors), (
                f"model field '{field}' rejected as an unsupported per-command key"
            )

    def test_ai_top_level_valid_keys_stay_in_sync_with_model(self, tmp_path: Path) -> None:
        """Every top-level ``AIConfig`` scalar field must be an accepted ``ai`` key.

        Companion to the per-command sync test (issue #368): the scalar keys
        (``AIConfig`` fields minus the per-command subsections) are derived from
        the model, so a newly added scalar field can't silently drift.
        """
        config = tmp_path / ".wade.yml"
        scalar_fields = set(AIConfig.model_fields) - set(AI_COMMAND_NAMES)
        for field in scalar_fields:
            config.write_text(f"version: 2\nai:\n  {field}: null\n")
            result = validate_config(tmp_path)
            assert not any(f"ai.{field}: unsupported key" in e for e in result.errors), (
                f"model field '{field}' rejected as an unsupported top-level ai key"
            )

    def test_rejects_duplicate_canonical_and_legacy_ai_sections(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text(
            "version: 2\nai:\n  implement:\n    tool: claude\n  work:\n    tool: codex\n"
        )
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("duplicates ai.implement" in e for e in result.errors)

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
        config.write_text("version: 2\nknowledge:\n  enabled: true\n  path: docs/KNOWLEDGE.md\n")
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

    def test_invalid_knowledge_path_escape(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nknowledge:\n  enabled: true\n  path: ../KNOWLEDGE.md\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("knowledge.path" in e and "inside the project root" in e for e in result.errors)

    def test_invalid_knowledge_unsupported_key(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nknowledge:\n  enabled: true\n  mode: shared\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("knowledge.mode" in e and "unsupported" in e for e in result.errors)

    def test_invalid_knowledge_not_mapping(self, tmp_path: Path) -> None:
        config = tmp_path / ".wade.yml"
        config.write_text("version: 2\nknowledge: true\n")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("knowledge" in e and "mapping" in e for e in result.errors)
