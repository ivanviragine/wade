"""Tests for atomic behaviour of run_all_migrations().

Verifies that when any migration step raises, the original file content is
restored and a RuntimeError (with "restored" in the message) is re-raised.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from wade.config.migrations import run_all_migrations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VERSIONLESS_CONFIG: dict[str, object] = {
    "ai": {"default_tool": "claude"},
    "project": {"main_branch": "main"},
}

_VALID_V2_CONFIG: dict[str, object] = {
    "version": 2,
    "ai": {"default_tool": "claude"},
    "project": {"main_branch": "main"},
}


def _write_yaml(path: Path, data: dict[str, object]) -> str:
    """Write *data* as YAML to *path* and return the written text."""
    text = yaml.dump(data, default_flow_style=False, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------------------
# Atomic restore tests
# ---------------------------------------------------------------------------


class TestRunAllMigrationsAtomic:
    def test_migration_failure_restores_file(self, tmp_path: Path) -> None:
        """If any migration raises, the original file bytes must be unchanged."""
        config_path = tmp_path / ".wade.yml"
        original_text = _write_yaml(config_path, _VALID_V2_CONFIG)

        with (
            patch(
                "wade.config.migrations.ensure_version",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            run_all_migrations(config_path)

        assert config_path.read_text(encoding="utf-8") == original_text

    def test_migration_failure_raises_runtime_error(self, tmp_path: Path) -> None:
        """RuntimeError must be raised and its message must contain 'restored'."""
        config_path = tmp_path / ".wade.yml"
        _write_yaml(config_path, _VALID_V2_CONFIG)

        with (
            patch(
                "wade.config.migrations.ensure_version",
                side_effect=ValueError("bad version"),
            ),
            pytest.raises(RuntimeError, match="restored"),
        ):
            run_all_migrations(config_path)

    def test_migration_failure_message_contains_original_error(self, tmp_path: Path) -> None:
        """The RuntimeError message must embed the original exception text."""
        config_path = tmp_path / ".wade.yml"
        _write_yaml(config_path, _VALID_V2_CONFIG)

        boom_message = "unexpected_sentinel_error_xyz"
        with (
            patch(
                "wade.config.migrations.ensure_version",
                side_effect=OSError(boom_message),
            ),
            pytest.raises(RuntimeError, match=boom_message),
        ):
            run_all_migrations(config_path)

    def test_migration_success_writes_file(self, tmp_path: Path) -> None:
        """Regression guard: successful migrations still update the file."""
        config_path = tmp_path / ".wade.yml"
        original_text = _write_yaml(config_path, _VERSIONLESS_CONFIG)

        result = run_all_migrations(config_path)

        assert result is True
        updated_text = config_path.read_text(encoding="utf-8")
        assert updated_text != original_text
        migrated = yaml.safe_load(updated_text)
        assert migrated["version"] == 2

    def test_migration_failure_chained_cause(self, tmp_path: Path) -> None:
        """The RuntimeError must chain the original exception as __cause__."""
        config_path = tmp_path / ".wade.yml"
        _write_yaml(config_path, _VALID_V2_CONFIG)

        original_exc = TypeError("chain_check")
        with (
            patch(
                "wade.config.migrations.ensure_version",
                side_effect=original_exc,
            ),
            pytest.raises(RuntimeError) as exc_info,
        ):
            run_all_migrations(config_path)

        assert exc_info.value.__cause__ is original_exc
