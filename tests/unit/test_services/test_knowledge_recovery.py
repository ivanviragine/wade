"""Tests for the retained-vote recovery sweep and its reporting."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wade.models.config import KnowledgeConfig, ProjectConfig
from wade.services.knowledge_recovery import report_retained_vote_recovery
from wade.services.knowledge_service import StagedRatingsFlushResult
from wade.ui.console import Console


class TestReportRetainedVoteRecovery:
    def test_no_sweep_when_knowledge_is_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sweep = MagicMock()
        monkeypatch.setattr("wade.services.knowledge_service.flush_retained_staged_ratings", sweep)

        report_retained_vote_recovery(tmp_path, ProjectConfig())

        sweep.assert_not_called()

    def test_silent_when_nothing_was_retained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        console = MagicMock()
        monkeypatch.setattr(
            "wade.services.knowledge_service.flush_retained_staged_ratings",
            lambda *_args: [],
        )
        monkeypatch.setattr("wade.services.knowledge_recovery.console", console)

        report_retained_vote_recovery(tmp_path, ProjectConfig(knowledge=_enabled()))

        console.info.assert_not_called()
        console.warn.assert_not_called()

    def test_reports_recovered_votes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        console = MagicMock()
        console.escape_markup.side_effect = lambda text: text
        monkeypatch.setattr(
            "wade.services.knowledge_service.flush_retained_staged_ratings",
            lambda *_args: [
                StagedRatingsFlushResult(
                    success=True,
                    staged_count=2,
                    appended_count=2,
                    worktree=tmp_path / "plan-abc",
                )
            ],
        )
        monkeypatch.setattr("wade.services.knowledge_recovery.console", console)

        report_retained_vote_recovery(tmp_path, ProjectConfig(knowledge=_enabled()))

        message = console.info.call_args.args[0]
        assert "Recovered 2 staged knowledge vote(s)" in message
        assert "plan-abc" in message

    def test_reports_a_still_failing_handoff_instead_of_going_quiet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        console = MagicMock()
        console.escape_markup.side_effect = lambda text: text
        monkeypatch.setattr(
            "wade.services.knowledge_service.flush_retained_staged_ratings",
            lambda *_args: [
                StagedRatingsFlushResult(
                    success=False,
                    message="main checkout is read-only",
                    worktree=tmp_path / "plan-abc",
                )
            ],
        )
        monkeypatch.setattr("wade.services.knowledge_recovery.console", console)

        report_retained_vote_recovery(tmp_path, ProjectConfig(knowledge=_enabled()))

        assert "main checkout is read-only" in console.warn.call_args.args[0]

    def test_reports_markup_like_recovery_values_literally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(
            "wade.services.knowledge_service.flush_retained_staged_ratings",
            lambda *_args: [
                StagedRatingsFlushResult(
                    success=True,
                    appended_count=1,
                    worktree=tmp_path / "plan-[/]",
                ),
                StagedRatingsFlushResult(
                    success=False,
                    message="handoff [/] blocked",
                    worktree=tmp_path / "deps-[/]",
                ),
            ],
        )
        monkeypatch.setenv("COLUMNS", "500")
        monkeypatch.setattr("wade.services.knowledge_recovery.console", Console())

        report_retained_vote_recovery(tmp_path, ProjectConfig(knowledge=_enabled()))

        captured = capsys.readouterr()
        assert "plan-[/]" in captured.out
        assert "deps-[/]" in captured.err
        assert "handoff [/] blocked" in captured.err

    def test_a_sweep_failure_never_blocks_the_session_about_to_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*_args: object) -> list[StagedRatingsFlushResult]:
            raise RuntimeError("unexpected")

        monkeypatch.setattr("wade.services.knowledge_service.flush_retained_staged_ratings", boom)

        report_retained_vote_recovery(tmp_path, ProjectConfig(knowledge=_enabled()))


def _enabled() -> KnowledgeConfig:
    return KnowledgeConfig(enabled=True)
