"""Tests for session sub-apps, worktree sub-app, wade cd, and short aliases.

Covers the new CLI modules introduced in #109:
- implementation-session (check, sync, done)
- review-pr-comments-session (check, sync, done, fetch, resolve)
- plan-session (check, done)
- deps-session (check)
- worktree (list, remove, cd)
- top-level cd
- hidden short aliases (p, i, r)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from wade.cli.main import app

runner = CliRunner()


def _assert_missing_required_argument(output: str, parameter_name: str) -> None:
    assert "Usage:" in output
    assert parameter_name in output


# ---------------------------------------------------------------------------
# Implementation session sub-app
# ---------------------------------------------------------------------------


class TestImplementationSessionSubApp:
    """Tests for ``wade implementation-session`` sub-app."""

    def test_check_not_in_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["implementation-session", "check"])
        assert result.exit_code == 1
        assert "NOT_IN_GIT_REPO" in result.output

    def test_check_delegates_resolution_to_the_service(self) -> None:
        """The CLI stays thin dispatch — resolution lives in ``check_service``."""
        from wade.services.check_service import CheckExitCode, CheckResult, CheckStatus

        ready = CheckResult(status=CheckStatus.IN_WORKTREE, exit_code=CheckExitCode.IN_WORKTREE)
        with patch(
            "wade.services.check_service.resolve_session_readiness",
            return_value=ready,
        ) as mock_resolve:
            result = runner.invoke(app, ["implementation-session", "check"])

        assert result.exit_code == 0
        mock_resolve.assert_called_once_with("implementation", None)

    def test_sync_not_in_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["implementation-session", "sync"])
        # Not in a worktree → preflight failure (exit 4)
        assert result.exit_code == 4
        assert "NOT_IN_GIT_REPO" in result.output

    def test_done_no_issue(self) -> None:
        with (
            patch("wade.cli.session_shared.require_ready"),
            patch("wade.git.repo.get_current_branch", return_value="main"),
        ):
            result = runner.invoke(app, ["implementation-session", "done"])
        assert result.exit_code == 1
        assert "Cannot extract issue number" in result.output

    def test_done_blocks_before_completion_when_runtime_loses_github_access(self) -> None:
        """A stale first-action check must not let `done` reach a push or PR write."""
        from wade.services.check_service import CheckExitCode, CheckResult, CheckStatus

        blocked = CheckResult(
            status=CheckStatus.GITHUB_AUTH_BLOCKED,
            exit_code=CheckExitCode.GITHUB_AUTH_BLOCKED,
        )
        with (
            patch(
                "wade.services.check_service.check_session_readiness",
                return_value=blocked,
            ),
            patch("wade.services.implementation_service.done") as mock_done,
        ):
            result = runner.invoke(app, ["implementation-session", "done"])

        assert result.exit_code == 1
        assert "GITHUB_AUTH_BLOCKED" in result.output
        mock_done.assert_not_called()

    def test_done_checks_the_resolved_worktree(self, tmp_path: Path) -> None:
        """`done <worktree>` from main must gate on that worktree (#462 review).

        The recovery/finalization forms (`done <issue|worktree>` and
        `done --plan`) resolve and switch to another worktree, so checking the
        caller's cwd would always report IN_MAIN_CHECKOUT and make them unusable.
        """
        from wade.services.check_service import CheckExitCode, CheckResult, CheckStatus

        target_worktree = tmp_path / "wt-42"
        target_worktree.mkdir()
        seen: dict[str, Path] = {}

        def _readiness(phase: object, path: Path, *args: object, **kwargs: object) -> CheckResult:
            seen["path"] = path
            return CheckResult(status=CheckStatus.IN_WORKTREE, exit_code=CheckExitCode.IN_WORKTREE)

        with (
            patch(
                "wade.services.implementation_service.resolve_done_worktree",
                return_value=target_worktree,
            ),
            patch(
                "wade.services.check_service.check_session_readiness",
                side_effect=_readiness,
            ),
            patch("wade.services.implementation_service.done", return_value=True) as mock_done,
        ):
            result = runner.invoke(app, ["implementation-session", "done", "42"])

        assert result.exit_code == 0
        assert seen["path"] == target_worktree
        mock_done.assert_called_once()

    def test_help_shows_all_commands(self) -> None:
        result = runner.invoke(app, ["implementation-session", "--help"])
        assert result.exit_code == 0
        for cmd in ("check", "sync", "done"):
            assert cmd in result.output


# ---------------------------------------------------------------------------
# Review PR comments session sub-app
# ---------------------------------------------------------------------------


class TestReviewPrCommentsSessionSubApp:
    """Tests for ``wade review-pr-comments-session`` sub-app."""

    def test_check_not_in_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["review-pr-comments-session", "check"])
        assert result.exit_code == 1
        assert "NOT_IN_GIT_REPO" in result.output

    def test_sync_not_in_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["review-pr-comments-session", "sync"])
        assert result.exit_code == 4
        assert "NOT_IN_GIT_REPO" in result.output

    def test_done_no_issue(self) -> None:
        with (
            patch("wade.cli.session_shared.require_ready"),
            patch("wade.git.repo.get_current_branch", return_value="main"),
        ):
            result = runner.invoke(app, ["review-pr-comments-session", "done"])
        assert result.exit_code == 1
        assert "Cannot extract issue number" in result.output

    def test_done_checks_the_resolved_worktree(self, tmp_path: Path) -> None:
        """Same resolved-target readiness contract as the implementation endpoint."""
        from wade.services.check_service import CheckExitCode, CheckResult, CheckStatus

        target_worktree = tmp_path / "wt-42"
        target_worktree.mkdir()
        seen: dict[str, Path] = {}

        def _readiness(phase: object, path: Path, *args: object, **kwargs: object) -> CheckResult:
            seen["path"] = path
            return CheckResult(status=CheckStatus.IN_WORKTREE, exit_code=CheckExitCode.IN_WORKTREE)

        with (
            patch(
                "wade.services.implementation_service.resolve_done_worktree",
                return_value=target_worktree,
            ),
            patch(
                "wade.services.check_service.check_session_readiness",
                side_effect=_readiness,
            ),
            patch("wade.services.implementation_service.done", return_value=False),
        ):
            result = runner.invoke(app, ["review-pr-comments-session", "done", "42"])

        assert result.exit_code == 1
        assert seen["path"] == target_worktree

    def test_fetch_requires_target(self) -> None:
        result = runner.invoke(app, ["review-pr-comments-session", "fetch"])
        assert result.exit_code != 0
        _assert_missing_required_argument(result.output, "TARGET")

    def test_resolve_requires_thread_id(self) -> None:
        result = runner.invoke(app, ["review-pr-comments-session", "resolve"])
        assert result.exit_code != 0
        _assert_missing_required_argument(result.output, "THREAD_ID")

    def test_help_shows_all_commands(self) -> None:
        result = runner.invoke(app, ["review-pr-comments-session", "--help"])
        assert result.exit_code == 0
        for cmd in ("check", "sync", "done", "fetch", "resolve"):
            assert cmd in result.output


# ---------------------------------------------------------------------------
# Plan session sub-app
# ---------------------------------------------------------------------------


class TestPlanSessionSubApp:
    """Tests for ``wade plan-session`` sub-app."""

    def test_done_requires_plan_dir(self) -> None:
        result = runner.invoke(app, ["plan-session", "done"])
        assert result.exit_code != 0
        _assert_missing_required_argument(result.output, "PLAN_DIR")

    def test_done_nonexistent_dir(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"
        result = runner.invoke(app, ["plan-session", "done", str(missing)])
        assert result.exit_code == 1

    def test_help_shows_check_and_done(self) -> None:
        result = runner.invoke(app, ["plan-session", "--help"])
        assert result.exit_code == 0
        for cmd in ("check", "done"):
            assert cmd in result.output

    def test_check_reports_plan_dir_only_in_the_worktree_less_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from wade.models.readiness import PLAN_DIR_ENV_VAR

        plan_dir = tmp_path / "wade-plan-xyz"
        plan_dir.mkdir()
        caller = tmp_path / "caller"
        caller.mkdir()
        monkeypatch.chdir(caller)
        monkeypatch.setenv(PLAN_DIR_ENV_VAR, str(plan_dir))

        result = runner.invoke(app, ["plan-session", "check"])

        assert result.exit_code == 0
        assert "PLAN_DIR_ONLY" in result.output
        assert f"plandir={plan_dir}" in result.output


class TestRequireReadyJsonContract:
    """A ``--json`` caller must never receive the human-readable readiness block."""

    def test_blocked_sync_emits_a_single_json_error_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json

        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["implementation-session", "sync", "--json"])

        assert result.exit_code == 4
        # CliRunner merges the stderr version banner into ``output``; real
        # ``--json`` consumers read stdout only.
        lines = [
            line
            for line in result.output.splitlines()
            if line.strip() and not line.strip().startswith("wade v")
        ]
        assert lines
        for line in lines:
            assert line.lstrip().startswith("{"), f"non-JSON leaked: {line!r}"
        events = [json.loads(line) for line in lines]
        assert events[0]["event"] == "error"
        assert events[0]["reason"] == "not_in_git_repo"
        assert events[0]["phase"] == "implementation"

    def test_blocked_sync_without_json_keeps_the_readable_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["implementation-session", "sync"])

        assert result.exit_code == 4
        assert "NOT_IN_GIT_REPO" in result.output


# ---------------------------------------------------------------------------
# Detached dependency-analysis session sub-app
# ---------------------------------------------------------------------------


class TestDepsSessionSubApp:
    """Tests for ``wade deps-session`` (agent-side offline preflight)."""

    def test_check_not_in_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["deps-session", "check"])
        assert result.exit_code == 1
        assert "NOT_IN_GIT_REPO" in result.output

    def test_help_shows_check(self) -> None:
        result = runner.invoke(app, ["deps-session", "--help"])
        assert result.exit_code == 0
        assert "check" in result.output


# ---------------------------------------------------------------------------
# Worktree sub-app
# ---------------------------------------------------------------------------


class TestWorktreeSubApp:
    """Tests for ``wade worktree`` sub-app."""

    @patch("wade.services.implementation_service.list_sessions", return_value=[])
    def test_list_empty(self, _mock: MagicMock) -> None:
        result = runner.invoke(app, ["worktree", "list"])
        assert result.exit_code == 0
        _mock.assert_called_once_with(show_all=False, json_output=False)

    def test_cd_requires_target(self) -> None:
        result = runner.invoke(app, ["worktree", "cd"])
        assert result.exit_code != 0
        _assert_missing_required_argument(result.output, "TARGET")

    def test_help_shows_all_commands(self) -> None:
        result = runner.invoke(app, ["worktree", "--help"])
        assert result.exit_code == 0
        for cmd in ("list", "remove", "cd"):
            assert cmd in result.output


# ---------------------------------------------------------------------------
# Top-level cd command
# ---------------------------------------------------------------------------


class TestTopLevelCd:
    """Tests for ``wade cd``."""

    def test_cd_requires_target(self) -> None:
        result = runner.invoke(app, ["cd"])
        assert result.exit_code != 0
        _assert_missing_required_argument(result.output, "TARGET")

    @patch("wade.services.implementation_service.find_worktree_path", return_value=Path("/tmp/wt"))
    def test_cd_prints_path_when_worktree_exists(self, _mock: MagicMock) -> None:
        result = runner.invoke(app, ["cd", "42"])
        assert result.exit_code == 0
        assert "/tmp/wt" in result.output


# ---------------------------------------------------------------------------
# Hidden short aliases
# ---------------------------------------------------------------------------


class TestShortAliases:
    """Tests for hidden short aliases ``wade p``, ``wade i``, ``wade r``."""

    def test_p_alias_invokes_plan(self) -> None:
        from wade.models.config import ProjectConfig

        with (
            patch("wade.services.plan_service.load_config", return_value=ProjectConfig()),
            patch("crossby.ai_tools.base.AbstractAITool.detect_installed", return_value=[]),
        ):
            result = runner.invoke(app, ["p"])
        assert result.exit_code == 1  # no AI tool → exits 1
        assert "No AI tool specified and none detected" in result.output

    def test_i_alias_invokes_implement(self) -> None:
        from wade.services.implementation_service import ImplementResult

        with patch(
            "wade.services.implementation_service.start",
            return_value=ImplementResult(success=True),
        ) as mock_start:
            result = runner.invoke(app, ["i", "42"])
        assert result.exit_code == 0
        mock_start.assert_called_once()
        assert mock_start.call_args.kwargs.get("target") == "42"

    def test_r_alias_invokes_review_pr_comments(self) -> None:
        with patch("wade.services.review_service.start", return_value=True) as mock_start:
            result = runner.invoke(app, ["r", "42"])
        assert result.exit_code == 0
        mock_start.assert_called_once()
        assert mock_start.call_args.kwargs.get("target") == "42"

    def test_aliases_hidden_in_help(self) -> None:
        """Short aliases should NOT appear in the help output."""
        import re

        result = runner.invoke(app, ["--help"])
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        # The single-letter commands should not have their own help entry
        # (they are hidden). Check they don't appear as standalone commands.
        lines = plain.splitlines()
        command_lines = [
            line.strip() for line in lines if line.strip().startswith(("p ", "i ", "r "))
        ]
        assert len(command_lines) == 0
