"""Tests for check service — worktree safety and config validation."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wade.config.loader import ConfigError
from wade.models.config import (
    AI_COMMAND_NAMES,
    AICommandConfig,
    AIConfig,
    CommitMsgConfig,
    HooksConfig,
    PostToolUseConfig,
    PreCommitConfig,
    ProjectConfig,
    ProviderConfig,
    ProviderID,
)
from wade.models.readiness import (
    PLAN_DIR_ENV_VAR,
    READINESS_REQUIREMENTS,
    ReadinessFailure,
    ReadinessPhase,
)
from wade.services.check_service import (
    READINESS_PROBE_TIMEOUT_SECONDS,
    CheckExitCode,
    CheckResult,
    CheckStatus,
    ConfigExitCode,
    _github_api_reachable,
    _github_auth_available,
    _github_cli_available,
    check_session_readiness,
    check_worktree,
    resolve_session_readiness,
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
        # Simulate an OS-sandbox denial by making the probe OPEN raise — not a
        # chmod (CI often runs as root, where mode bits don't block writes). The
        # probe now creates its file via os.open (O_NOFOLLOW), so intercept that.
        wt_path = _add_worktree(tmp_git_repo)

        orig_open = os.open

        def deny_probe_open(path: object, *args: object, **kwargs: object) -> int:
            if os.path.basename(os.fsdecode(path)).startswith(".wade-write-probe-"):  # type: ignore[arg-type]
                raise OSError("Operation not permitted")
            return orig_open(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(os, "open", deny_probe_open)

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

        # No probe artefacts left behind (the open raised before creating one).
        monkeypatch.undo()
        from wade.git import repo as git_repo

        private = Path(git_repo.get_git_dir(wt_path) or "")
        common = Path(git_repo.get_git_common_dir(wt_path) or "")
        if not common.is_absolute():
            common = wt_path / common
        assert _probe_artefacts(private, common) == []

    def test_probe_survives_platform_without_o_nofollow(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # On Windows ``os.O_NOFOLLOW`` is undefined. The probe must resolve the
        # flag defensively (getattr → 0) rather than raise ``AttributeError`` and
        # break every linked-worktree session check. Simulate the missing symbol.
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)

        wt_path = _add_worktree(tmp_git_repo)
        result = check_worktree(wt_path)

        assert result.status == CheckStatus.IN_WORKTREE
        assert result.exit_code == CheckExitCode.IN_WORKTREE
        assert result.blocked_paths == []

    def test_main_checkout_is_not_probed(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A main checkout never runs the probe, so a would-be-denying open patch
        # cannot flip it to WORKTREE_GIT_BLOCKED.
        orig_open = os.open

        def boom(path: object, *args: object, **kwargs: object) -> int:
            if os.path.basename(os.fsdecode(path)).startswith(".wade-write-probe-"):  # type: ignore[arg-type]
                raise AssertionError("probe must not run in a main checkout")
            return orig_open(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(os, "open", boom)

        result = check_worktree(tmp_git_repo)
        assert result.status == CheckStatus.IN_MAIN_CHECKOUT
        assert result.exit_code == CheckExitCode.IN_MAIN_CHECKOUT


class TestSessionReadiness:
    """Phase-aware probes preserve the legacy worktree contract and name failures."""

    def test_github_cli_failure_is_distinct_from_authentication(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree = _add_worktree(tmp_git_repo, name="cli-worktree", branch="cli-branch")
        monkeypatch.setattr("wade.services.check_service._github_cli_available", lambda _: False)

        def auth_must_not_run(_: Path) -> bool:
            raise AssertionError("auth probe must not mask an unavailable gh executable")

        monkeypatch.setattr("wade.services.check_service._github_auth_available", auth_must_not_run)

        result = check_session_readiness(ReadinessPhase.IMPLEMENTATION, worktree, ProjectConfig())

        assert result.status == CheckStatus.GITHUB_CLI_BLOCKED
        assert result.exit_code == CheckExitCode.GITHUB_CLI_BLOCKED
        assert result.failure == ReadinessFailure.GITHUB_CLI_EXECUTABLE
        assert "reason=github_cli_executable" in result.format_output()
        assert "PATH" in result.format_output()

    def test_github_auth_failure_has_stable_reason(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree = _add_worktree(tmp_git_repo, name="auth-worktree", branch="auth-branch")
        monkeypatch.setattr("wade.services.check_service._github_cli_available", lambda _: True)
        monkeypatch.setattr("wade.services.check_service._github_auth_available", lambda _: False)

        result = check_session_readiness(ReadinessPhase.IMPLEMENTATION, worktree, ProjectConfig())

        assert result.status == CheckStatus.GITHUB_AUTH_BLOCKED
        assert result.exit_code == CheckExitCode.GITHUB_AUTH_BLOCKED
        assert result.failure == ReadinessFailure.GITHUB_AUTHENTICATION
        assert "reason=github_authentication" in result.format_output()

    def test_github_api_failure_runs_after_auth(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree = _add_worktree(tmp_git_repo, name="api-worktree", branch="api-branch")
        monkeypatch.setattr("wade.services.check_service._github_cli_available", lambda _: True)
        monkeypatch.setattr("wade.services.check_service._github_auth_available", lambda _: True)
        monkeypatch.setattr("wade.services.check_service._github_api_reachable", lambda _: False)

        result = check_session_readiness(ReadinessPhase.IMPLEMENTATION, worktree, ProjectConfig())

        assert result.status == CheckStatus.GITHUB_API_BLOCKED
        assert result.exit_code == CheckExitCode.GITHUB_API_BLOCKED
        assert result.failure == ReadinessFailure.GITHUB_API_REACHABILITY

    @pytest.mark.parametrize(
        "probe",
        [_github_cli_available, _github_auth_available, _github_api_reachable],
    )
    def test_github_probes_are_bounded_and_treat_timeout_as_unavailable(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        probe: Callable[[Path], bool],
    ) -> None:
        """Packet-dropping sandboxes cannot hang the first-action check forever."""
        run = MagicMock(side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=5))
        monkeypatch.setattr("wade.services.check_service.subprocess.run", run)
        monkeypatch.setattr("wade.services.check_service.repo.get_remote_url", lambda _: None)

        assert probe(tmp_path) is False
        assert run.call_args.kwargs["timeout"] == READINESS_PROBE_TIMEOUT_SECONDS

    def test_pr_sessions_require_github_even_for_non_github_task_provider(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree = _add_worktree(tmp_git_repo, name="markdown-worktree", branch="markdown-branch")
        monkeypatch.setattr("wade.services.check_service._github_cli_available", lambda _: True)
        monkeypatch.setattr("wade.services.check_service._github_auth_available", lambda _: False)
        config = ProjectConfig(provider=ProviderConfig(name=ProviderID.MARKDOWN))

        result = check_session_readiness(ReadinessPhase.IMPLEMENTATION, worktree, config)

        # Task providers are pluggable, but implementation/review completion
        # still creates and updates GitHub PRs. Do not let Markdown/ClickUp
        # sessions discover their missing `gh` authority only at `done`.
        assert result.status == CheckStatus.GITHUB_AUTH_BLOCKED

    def test_github_probes_do_not_depend_on_a_supplied_config(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whether a phase needs GitHub comes from the phase, never from config.

        ``config`` is optional, so gating the probes on it would let a caller
        that omits it pass a GitHub-requiring phase without a single check.
        """
        worktree = _add_worktree(tmp_git_repo, name="no-config", branch="no-config")
        monkeypatch.setattr("wade.services.check_service._github_cli_available", lambda _: False)

        result = check_session_readiness(ReadinessPhase.IMPLEMENTATION, worktree)

        assert result.status == CheckStatus.GITHUB_CLI_BLOCKED

    @pytest.mark.parametrize("phase", [ReadinessPhase.PLAN, ReadinessPhase.DEPS])
    def test_detached_analysis_phases_skip_github_probes(
        self,
        tmp_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        phase: ReadinessPhase,
    ) -> None:
        worktree = _add_worktree(
            tmp_git_repo,
            name=f"{phase.value}-worktree",
            branch=f"{phase.value}-branch",
        )

        def must_not_probe(_: Path) -> bool:
            raise AssertionError("offline detached analysis must not probe GitHub")

        monkeypatch.setattr("wade.services.check_service._github_auth_available", must_not_probe)
        monkeypatch.setattr("wade.services.check_service._github_api_reachable", must_not_probe)
        monkeypatch.setattr("wade.services.check_service._github_cli_available", must_not_probe)

        result = check_session_readiness(phase, worktree, ProjectConfig())

        assert result.status == CheckStatus.IN_WORKTREE

    @pytest.mark.parametrize("phase", [ReadinessPhase.PLAN, ReadinessPhase.DEPS])
    def test_detached_analysis_does_not_probe_git_metadata_writes(
        self,
        tmp_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        phase: ReadinessPhase,
    ) -> None:
        worktree = _add_worktree(
            tmp_git_repo,
            name=f"{phase.value}-git-blocked",
            branch=f"{phase.value}-git-blocked",
        )

        def metadata_probe_must_not_run(_: Path) -> list[str]:
            raise AssertionError("plan/deps must not attempt an irrelevant gitdir write probe")

        monkeypatch.setattr(
            "wade.services.check_service._blocked_git_metadata_dirs",
            metadata_probe_must_not_run,
        )

        result = check_session_readiness(phase, worktree, ProjectConfig())

        assert result.status == CheckStatus.IN_WORKTREE
        assert result.failure is None
        assert result.blocked_paths == []

    def test_detached_stage_failure_is_named_before_planning(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree = _add_worktree(tmp_git_repo, name="plan-worktree", branch="plan-branch")
        config = ProjectConfig(
            provider=ProviderConfig(name=ProviderID.MARKDOWN),
            knowledge={"enabled": True},
        )
        monkeypatch.setattr(
            "wade.services.knowledge_service.is_throwaway_knowledge_session", lambda _: True
        )
        monkeypatch.setattr("wade.services.check_service._probe_staging_path", lambda _: False)

        result = check_session_readiness(ReadinessPhase.PLAN, worktree, config)

        assert result.status == CheckStatus.KNOWLEDGE_STAGING_BLOCKED
        assert result.exit_code == CheckExitCode.KNOWLEDGE_STAGING_BLOCKED
        assert result.failure == ReadinessFailure.KNOWLEDGE_VOTE_STAGING


class TestGitHubAuthProbe:
    """Only the account this session will actually use decides authentication."""

    def test_restricts_the_status_check_to_the_active_account(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""))
        monkeypatch.setattr("wade.services.check_service.subprocess.run", run)
        monkeypatch.delenv("GH_HOST", raising=False)

        assert _github_auth_available(tmp_path) is True
        # Without --active a single stale secondary login exits 1 and would
        # block every session, even though gh operations use the active one.
        assert "--active" in run.call_args.args[0]

    def test_does_not_invent_a_host_when_origin_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No origin leaves gh's normal default-host behavior intact."""
        run = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""))
        monkeypatch.setattr("wade.services.check_service.subprocess.run", run)
        monkeypatch.delenv("GH_HOST", raising=False)

        assert _github_auth_available(tmp_path) is True
        assert "--hostname" not in run.call_args.args[0]

    @pytest.mark.parametrize(
        "remote_url",
        [
            "https://ghe.example.com/organization/project.git",
            "git@ghe.example.com:organization/project.git",
        ],
    )
    def test_probes_the_origin_hostname_for_auth_and_api(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remote_url: str
    ) -> None:
        """Bare `gh api` would otherwise default to github.com on Enterprise."""
        run = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""))
        monkeypatch.setattr("wade.services.check_service.subprocess.run", run)
        monkeypatch.setattr("wade.services.check_service.repo.get_remote_url", lambda _: remote_url)
        monkeypatch.delenv("GH_HOST", raising=False)

        assert _github_auth_available(tmp_path) is True
        assert _github_api_reachable(tmp_path) is True
        for call in run.call_args_list:
            args = call.args[0]
            assert args[args.index("--hostname") + 1] == "ghe.example.com"

    def test_forwards_an_explicit_gh_host(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""))
        monkeypatch.setattr("wade.services.check_service.subprocess.run", run)
        monkeypatch.setenv("GH_HOST", "ghe.example.com")

        assert _github_auth_available(tmp_path) is True
        args = run.call_args.args[0]
        assert args[args.index("--hostname") + 1] == "ghe.example.com"

    def test_falls_back_when_the_installed_gh_rejects_active(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            if "--active" in args:
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr="unknown flag: --active"
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr("wade.services.check_service.subprocess.run", fake_run)
        monkeypatch.setattr("wade.services.check_service.repo.get_remote_url", lambda _: None)

        # An older gh must degrade to the unfiltered probe, not turn a working
        # login into a hard session block.
        assert _github_auth_available(tmp_path) is True
        assert len(calls) == 2

    def test_a_genuine_auth_failure_is_not_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = MagicMock(
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="not logged in")
        )
        monkeypatch.setattr("wade.services.check_service.subprocess.run", run)
        monkeypatch.setattr("wade.services.check_service.repo.get_remote_url", lambda _: None)

        assert _github_auth_available(tmp_path) is False
        assert run.call_count == 1


class TestPlanDirFallbackReadiness:
    """`wade plan`'s supported worktree-less fallback stays usable."""

    def test_plan_dir_mode_reports_ready_outside_a_worktree(
        self, tmp_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan_dir = tmp_path / "wade-plan-abc"
        plan_dir.mkdir()
        monkeypatch.setenv(PLAN_DIR_ENV_VAR, str(plan_dir))

        result = check_session_readiness(ReadinessPhase.PLAN, tmp_git_repo, ProjectConfig())

        assert result.status == CheckStatus.PLAN_DIR_ONLY
        assert result.exit_code == CheckExitCode.IN_WORKTREE
        assert result.failure is None
        assert f"plandir={plan_dir}" in result.format_output()

    def test_plan_dir_mode_works_outside_a_git_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan_dir = tmp_path / "wade-plan-def"
        plan_dir.mkdir()
        caller = tmp_path / "not-a-repo"
        caller.mkdir()
        monkeypatch.setenv(PLAN_DIR_ENV_VAR, str(plan_dir))

        result = check_session_readiness(ReadinessPhase.PLAN, caller, ProjectConfig())

        assert result.status == CheckStatus.PLAN_DIR_ONLY
        assert result.exit_code == CheckExitCode.IN_WORKTREE

    def test_unwritable_plan_dir_is_named_not_silently_ready(
        self, tmp_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan_dir = tmp_path / "wade-plan-ghi"
        plan_dir.mkdir()
        monkeypatch.setenv(PLAN_DIR_ENV_VAR, str(plan_dir))
        monkeypatch.setattr("wade.services.check_service._probe_dir_writable", lambda _: False)

        result = check_session_readiness(ReadinessPhase.PLAN, tmp_git_repo, ProjectConfig())

        assert result.status == CheckStatus.PLAN_DIR_BLOCKED
        assert result.exit_code == CheckExitCode.PLAN_DIR_BLOCKED
        assert result.failure == ReadinessFailure.PLAN_OUTPUT_WRITE
        assert "reason=plan_output_write" in result.format_output()

    def test_a_main_checkout_without_the_marker_still_fails(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Only `wade plan` sets the marker, so a plain misplaced agent must not
        # be able to talk its way into planning from the main checkout.
        monkeypatch.delenv(PLAN_DIR_ENV_VAR, raising=False)

        result = check_session_readiness(ReadinessPhase.PLAN, tmp_git_repo, ProjectConfig())

        assert result.status == CheckStatus.IN_MAIN_CHECKOUT

    def test_the_marker_never_masks_a_real_worktree_failure(
        self, tmp_git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        worktree = _add_worktree(tmp_git_repo, name="plan-marker", branch="plan-marker")
        plan_dir = tmp_path / "wade-plan-jkl"
        plan_dir.mkdir()
        monkeypatch.setenv(PLAN_DIR_ENV_VAR, str(plan_dir))
        config = ProjectConfig(knowledge={"enabled": True})
        monkeypatch.setattr(
            "wade.services.knowledge_service.is_throwaway_knowledge_session", lambda _: True
        )
        monkeypatch.setattr("wade.services.check_service._probe_staging_path", lambda _: False)

        result = check_session_readiness(ReadinessPhase.PLAN, worktree, config)

        assert result.status == CheckStatus.KNOWLEDGE_STAGING_BLOCKED


class TestResolveSessionReadiness:
    """Config + AI-tool resolution for a phase lives in the service, not the CLI."""

    def test_resolves_the_phase_specific_ai_tool(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = ProjectConfig(
            ai=AIConfig(
                default_tool="claude",
                review_pr_comments=AICommandConfig(tool="cursor"),
            )
        )
        seen: dict[str, object] = {}

        def _check(
            phase: ReadinessPhase,
            cwd: Path | None = None,
            cfg: ProjectConfig | None = None,
            tool: str | None = None,
        ) -> CheckResult:
            seen["phase"], seen["cwd"], seen["tool"] = phase, cwd, tool
            return CheckResult(status=CheckStatus.IN_WORKTREE, exit_code=CheckExitCode.IN_WORKTREE)

        monkeypatch.setattr("wade.services.check_service.load_config", lambda _: config)
        monkeypatch.setattr("wade.services.check_service.check_session_readiness", _check)

        result = resolve_session_readiness("review-pr-comments", tmp_git_repo)

        assert result.exit_code == CheckExitCode.IN_WORKTREE
        assert seen == {
            "phase": ReadinessPhase.REVIEW_PR_COMMENTS,
            "cwd": tmp_git_repo,
            "tool": "cursor",
        }

    def test_every_phase_maps_to_a_real_ai_config_section(self) -> None:
        # A typo would fall back to ``ai.default_tool`` instead of raising, so a
        # phase silently losing its per-command tool override is caught here.
        for requirements in READINESS_REQUIREMENTS.values():
            assert requirements.ai_command in AI_COMMAND_NAMES
            assert isinstance(getattr(AIConfig(), requirements.ai_command, None), AICommandConfig)

    def test_does_not_silently_ignore_invalid_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Defaults are for an absent config, never for a malformed existing one."""

        def _raise(_: Path) -> ProjectConfig:
            raise ConfigError("invalid config")

        monkeypatch.setattr("wade.services.check_service.load_config", _raise)

        with pytest.raises(ConfigError, match="invalid config"):
            resolve_session_readiness(ReadinessPhase.IMPLEMENTATION, tmp_path)


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
