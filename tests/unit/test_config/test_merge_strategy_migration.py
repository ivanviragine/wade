"""Regression tests for retiring ``merge_strategy: direct`` (issue #357, C1).

A config that still carries the retired ``direct`` strategy must migrate to
``PR`` (with a warning) on load, and ``wade check-config`` must reject it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from structlog.testing import capture_logs

from wade.config.loader import ConfigError, parse_config_file
from wade.models.session import MergeStrategy
from wade.services.check_service import ConfigExitCode, validate_config


def _write_config(dir_path: Path, merge_strategy: str) -> Path:
    path = dir_path / ".wade.yml"
    path.write_text(
        f"version: 2\nproject:\n  main_branch: main\n  merge_strategy: {merge_strategy}\n",
        encoding="utf-8",
    )
    return path


class TestLoaderMigratesDirect:
    def test_direct_is_migrated_to_pr(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, "direct")
        with capture_logs() as logs:
            config = parse_config_file(config_path)
        assert config.project.merge_strategy == MergeStrategy.PR
        # The migration must not be silent — a warning event is emitted so the
        # user can see the retired value was upgraded.
        assert any(
            log.get("event") == "config.merge_strategy_direct_retired"
            and log.get("log_level") == "warning"
            for log in logs
        )

    def test_pr_is_preserved(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, "PR")
        config = parse_config_file(config_path)
        assert config.project.merge_strategy == MergeStrategy.PR

    def test_absent_defaults_to_pr(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".wade.yml"
        config_path.write_text("version: 2\nproject:\n  main_branch: main\n", encoding="utf-8")
        config = parse_config_file(config_path)
        assert config.project.merge_strategy == MergeStrategy.PR

    def test_unknown_strategy_still_raises_config_error(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, "banana")
        with pytest.raises(ConfigError):
            parse_config_file(config_path)


class TestCheckConfigRejectsDirect:
    def test_check_config_errors_on_direct(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "direct")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.INVALID
        assert any("direct" in e and "retired" in e for e in result.errors)

    def test_check_config_accepts_pr(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "PR")
        result = validate_config(tmp_path)
        assert result.exit_code == ConfigExitCode.VALID
