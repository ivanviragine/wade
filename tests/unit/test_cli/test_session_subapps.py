"""Tests for session sub-apps, worktree sub-app, wade cd, and short aliases.

Covers the new CLI modules introduced in #109:
- implementation-session (check, sync, done)
- review-pr-comments-session (check, sync, done, fetch, resolve)
- plan-session (done)
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

    def test_sync_not_in_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["implementation-session", "sync"])
        # Not in a worktree → preflight failure (exit 4)
        assert result.exit_code == 4

    def test_done_no_issue(self) -> None:
        with patch("wade.git.repo.get_current_branch", return_value="main"):
            result = runner.invoke(app, ["implementation-session", "done"])
        assert result.exit_code == 1
        assert "Cannot extract issue number" in result.output

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

    def test_done_no_issue(self) -> None:
        with patch("wade.git.repo.get_current_branch", return_value="main"):
            result = runner.invoke(app, ["review-pr-comments-session", "done"])
        assert result.exit_code == 1
        assert "Cannot extract issue number" in result.output

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

    def test_help_shows_done(self) -> None:
        result = runner.invoke(app, ["plan-session", "--help"])
        assert result.exit_code == 0
        assert "done" in result.output


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
            patch("wade.ai_tools.base.AbstractAITool.detect_installed", return_value=[]),
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
