"""Tests for the ``.wade/stale_base`` marker leaf (#407)."""

from __future__ import annotations

from pathlib import Path

from wade.utils import stale_base


class TestRoundTrip:
    def test_write_then_read(self, tmp_path: Path) -> None:
        assert stale_base.write_stale_base(tmp_path, 24, stale_base.REASON_UNTRACKED_CONFLICT)
        marker = stale_base.read_stale_base(tmp_path)
        assert marker is not None
        assert marker.behind == 24
        assert marker.reason == stale_base.REASON_UNTRACKED_CONFLICT

    def test_write_creates_wade_dir(self, tmp_path: Path) -> None:
        assert not (tmp_path / ".wade").exists()
        assert stale_base.write_stale_base(tmp_path, 3, stale_base.REASON_MERGE_CONFLICT)
        assert (tmp_path / ".wade" / "stale_base").is_file()

    def test_clear_removes_marker(self, tmp_path: Path) -> None:
        stale_base.write_stale_base(tmp_path, 5, stale_base.REASON_SKIP_WORKTREE)
        stale_base.clear_stale_base(tmp_path)
        assert stale_base.read_stale_base(tmp_path) is None

    def test_clear_absent_is_noop(self, tmp_path: Path) -> None:
        # No marker and no .wade dir — must not raise.
        stale_base.clear_stale_base(tmp_path)
        assert stale_base.read_stale_base(tmp_path) is None


class TestReadRobustness:
    def test_missing_returns_none(self, tmp_path: Path) -> None:
        assert stale_base.read_stale_base(tmp_path) is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / ".wade").mkdir()
        (tmp_path / ".wade" / "stale_base").write_text("", encoding="utf-8")
        assert stale_base.read_stale_base(tmp_path) is None

    def test_non_integer_count_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / ".wade").mkdir()
        (tmp_path / ".wade" / "stale_base").write_text("abc untracked_conflict\n", encoding="utf-8")
        assert stale_base.read_stale_base(tmp_path) is None

    def test_count_only_defaults_reason_unknown(self, tmp_path: Path) -> None:
        (tmp_path / ".wade").mkdir()
        (tmp_path / ".wade" / "stale_base").write_text("7\n", encoding="utf-8")
        marker = stale_base.read_stale_base(tmp_path)
        assert marker is not None
        assert marker.behind == 7
        assert marker.reason == stale_base.REASON_UNKNOWN


class TestWriteNormalisation:
    def test_reason_reduced_to_single_token(self, tmp_path: Path) -> None:
        stale_base.write_stale_base(tmp_path, 2, "merge conflict with spaces")
        marker = stale_base.read_stale_base(tmp_path)
        assert marker is not None
        assert marker.reason == "merge"  # only the first whitespace-delimited token

    def test_empty_reason_falls_back_to_unknown(self, tmp_path: Path) -> None:
        stale_base.write_stale_base(tmp_path, 1, "")
        marker = stale_base.read_stale_base(tmp_path)
        assert marker is not None
        assert marker.reason == stale_base.REASON_UNKNOWN

    def test_long_reason_is_capped(self, tmp_path: Path) -> None:
        stale_base.write_stale_base(tmp_path, 1, "x" * 200)
        marker = stale_base.read_stale_base(tmp_path)
        assert marker is not None
        assert len(marker.reason) <= 40

    def test_marker_is_single_line(self, tmp_path: Path) -> None:
        stale_base.write_stale_base(tmp_path, 9, stale_base.REASON_SKIP_WORKTREE)
        raw = (tmp_path / ".wade" / "stale_base").read_text(encoding="utf-8")
        assert raw.count("\n") == 1
        assert raw.strip() == "9 skip_worktree"
