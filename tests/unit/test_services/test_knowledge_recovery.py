"""Tests for the retained-vote recovery sweep and its reporting."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wade.models.config import KnowledgeConfig, ProjectConfig
from wade.services.knowledge_recovery import report_retained_vote_recovery
from wade.services.knowledge_service import StagedRatingsFlushResult


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

    def test_a_sweep_failure_never_blocks_the_session_about_to_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*_args: object) -> list[StagedRatingsFlushResult]:
            raise RuntimeError("unexpected")

        monkeypatch.setattr("wade.services.knowledge_service.flush_retained_staged_ratings", boom)

        report_retained_vote_recovery(tmp_path, ProjectConfig(knowledge=_enabled()))


def _enabled() -> KnowledgeConfig:
    return KnowledgeConfig(enabled=True)
