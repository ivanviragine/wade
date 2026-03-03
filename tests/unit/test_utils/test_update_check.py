"""Tests for background update check — PyPI polling, cache, and nag suppression."""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.utils.update_check import (
    CHECK_INTERVAL_SECS,
    _is_newer,
    _load_cache,
    _save_cache,
    check_for_update,
    maybe_print_update_hint,
)

# ---------------------------------------------------------------------------
# _is_newer
# ---------------------------------------------------------------------------


class TestIsNewer:
    def test_newer_major(self) -> None:
        assert _is_newer("2.0.0", "1.0.0") is True

    def test_newer_minor(self) -> None:
        assert _is_newer("1.1.0", "1.0.0") is True

    def test_newer_patch(self) -> None:
        assert _is_newer("1.0.1", "1.0.0") is True

    def test_same_version(self) -> None:
        assert _is_newer("1.0.0", "1.0.0") is False

    def test_older_version(self) -> None:
        assert _is_newer("0.9.0", "1.0.0") is False

    def test_invalid_version_returns_false(self) -> None:
        assert _is_newer("not-a-version", "1.0.0") is False


# ---------------------------------------------------------------------------
# _load_cache / _save_cache
# ---------------------------------------------------------------------------


class TestCacheRoundtrip:
    def test_save_and_load(self, tmp_path: Path) -> None:
        """A saved cache is correctly loaded back."""
        cache_file = tmp_path / "update-check.json"
        with patch("wade.utils.update_check.CACHE_FILE", cache_file):
            _save_cache("1.2.3", 1000.0)
            result = _load_cache()

        assert result is not None
        assert result["latest_version"] == "1.2.3"
        assert float(str(result["timestamp"])) == pytest.approx(1000.0)

    def test_load_returns_none_when_missing(self, tmp_path: Path) -> None:
        """Returns None when cache file doesn't exist."""
        cache_file = tmp_path / "nonexistent.json"
        with patch("wade.utils.update_check.CACHE_FILE", cache_file):
            result = _load_cache()

        assert result is None

    def test_load_returns_none_on_malformed_json(self, tmp_path: Path) -> None:
        """Returns None when cache file contains invalid JSON."""
        cache_file = tmp_path / "update-check.json"
        cache_file.write_text("not json!", encoding="utf-8")
        with patch("wade.utils.update_check.CACHE_FILE", cache_file):
            result = _load_cache()

        assert result is None

    def test_load_returns_none_when_timestamp_not_numeric(self, tmp_path: Path) -> None:
        """Returns None when timestamp is a non-numeric string."""
        cache_file = tmp_path / "update-check.json"
        cache_file.write_text(
            json.dumps({"timestamp": "not-a-number", "latest_version": "1.0.0"}),
            encoding="utf-8",
        )
        with patch("wade.utils.update_check.CACHE_FILE", cache_file):
            result = _load_cache()

        assert result is None

    def test_load_returns_none_when_version_not_string(self, tmp_path: Path) -> None:
        """Returns None when latest_version is not a string."""
        cache_file = tmp_path / "update-check.json"
        cache_file.write_text(
            json.dumps({"timestamp": 123.0, "latest_version": 42}),
            encoding="utf-8",
        )
        with patch("wade.utils.update_check.CACHE_FILE", cache_file):
            result = _load_cache()

        assert result is None

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        """_save_cache creates parent directories if needed."""
        cache_file = tmp_path / "deeply" / "nested" / "update-check.json"
        with patch("wade.utils.update_check.CACHE_FILE", cache_file):
            _save_cache("1.0.0", 0.0)

        assert cache_file.is_file()


# ---------------------------------------------------------------------------
# check_for_update
# ---------------------------------------------------------------------------


class TestCheckForUpdate:
    def test_returns_none_when_already_current(self, tmp_path: Path) -> None:
        """Returns None when PyPI reports same version as installed."""
        cache_file = tmp_path / "update-check.json"
        now = datetime.now(UTC).timestamp()
        cache_file.write_text(
            json.dumps({"timestamp": now, "latest_version": "1.0.0"}), encoding="utf-8"
        )
        with patch("wade.utils.update_check.CACHE_FILE", cache_file):
            result = check_for_update("1.0.0")

        assert result is None

    def test_returns_latest_when_newer_cached(self, tmp_path: Path) -> None:
        """Returns latest version string when fresh cache shows newer version."""
        cache_file = tmp_path / "update-check.json"
        now = datetime.now(UTC).timestamp()
        cache_file.write_text(
            json.dumps({"timestamp": now, "latest_version": "1.1.0"}), encoding="utf-8"
        )
        with patch("wade.utils.update_check.CACHE_FILE", cache_file):
            result = check_for_update("1.0.0")

        assert result == "1.1.0"

    def test_fetches_when_cache_stale(self, tmp_path: Path) -> None:
        """Fetches from PyPI when cache is older than CHECK_INTERVAL_SECS."""
        cache_file = tmp_path / "update-check.json"
        stale_ts = datetime.now(UTC).timestamp() - CHECK_INTERVAL_SECS - 1
        cache_file.write_text(
            json.dumps({"timestamp": stale_ts, "latest_version": "1.0.0"}), encoding="utf-8"
        )
        with (
            patch("wade.utils.update_check.CACHE_FILE", cache_file),
            patch("wade.utils.update_check._fetch_latest_version", return_value="1.2.0"),
        ):
            result = check_for_update("1.0.0")

        assert result == "1.2.0"

    def test_returns_none_when_fetch_fails(self, tmp_path: Path) -> None:
        """Returns None when PyPI fetch fails and cache is empty."""
        cache_file = tmp_path / "update-check.json"
        with (
            patch("wade.utils.update_check.CACHE_FILE", cache_file),
            patch("wade.utils.update_check._fetch_latest_version", return_value=None),
        ):
            result = check_for_update("1.0.0")

        assert result is None

    def test_uses_fresh_cache_without_network(self, tmp_path: Path) -> None:
        """Fresh cache is used without making a network call."""
        cache_file = tmp_path / "update-check.json"
        now = datetime.now(UTC).timestamp()
        cache_file.write_text(
            json.dumps({"timestamp": now, "latest_version": "2.0.0"}), encoding="utf-8"
        )
        with (
            patch("wade.utils.update_check.CACHE_FILE", cache_file),
            patch("wade.utils.update_check._fetch_latest_version") as mock_fetch,
        ):
            result = check_for_update("1.0.0")

        assert result == "2.0.0"
        mock_fetch.assert_not_called()


# ---------------------------------------------------------------------------
# maybe_print_update_hint
# ---------------------------------------------------------------------------


class TestMaybePrintUpdateHint:
    def test_suppressed_by_env_var(self, tmp_path: Path) -> None:
        """WADE_NO_UPDATE_CHECK=1 suppresses the nag."""
        with (
            patch.dict(os.environ, {"WADE_NO_UPDATE_CHECK": "1"}),
            patch("wade.utils.update_check.check_for_update") as mock_check,
        ):
            maybe_print_update_hint("1.0.0", "task")

        mock_check.assert_not_called()

    def test_suppressed_for_update_subcommand(self) -> None:
        """Nag is suppressed when the invoked subcommand is 'update'."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("wade.utils.update_check.check_for_update") as mock_check,
            patch("sys.stderr", MagicMock(isatty=lambda: True)),
        ):
            maybe_print_update_hint("1.0.0", "update")

        mock_check.assert_not_called()

    def test_suppressed_when_not_tty(self) -> None:
        """Nag is suppressed when stderr is not a TTY (e.g., CI / piped)."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("wade.utils.update_check.check_for_update") as mock_check,
            patch.object(sys, "stderr", io.StringIO()),  # StringIO has isatty() → False
        ):
            maybe_print_update_hint("1.0.0", "task")

        mock_check.assert_not_called()

    def test_prints_nag_when_update_available(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Prints a nag message to stderr when a newer version is found."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("wade.utils.update_check.check_for_update", return_value="1.1.0"),
            patch("sys.stderr") as mock_stderr,
        ):
            mock_stderr.isatty.return_value = True
            maybe_print_update_hint("1.0.0", "task")

        mock_stderr.write.assert_called()
        output = "".join(call.args[0] for call in mock_stderr.write.call_args_list)
        assert "1.1.0" in output
        assert "wade update" in output

    def test_silent_when_no_update(self, tmp_path: Path) -> None:
        """Prints nothing when already on the latest version."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("wade.utils.update_check.check_for_update", return_value=None),
            patch("sys.stderr") as mock_stderr,
        ):
            mock_stderr.isatty.return_value = True
            maybe_print_update_hint("1.0.0", "task")

        mock_stderr.write.assert_not_called()

    def test_silent_on_exception(self) -> None:
        """Any exception during update check is swallowed silently."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("sys.stderr") as mock_stderr,
            patch(
                "wade.utils.update_check.check_for_update",
                side_effect=RuntimeError("network error"),
            ),
        ):
            mock_stderr.isatty.return_value = True
            # Should not raise
            maybe_print_update_hint("1.0.0", "task")
