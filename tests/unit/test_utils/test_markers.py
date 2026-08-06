"""Unit tests for the sha-keyed completion-marker primitive (#349)."""

from __future__ import annotations

from pathlib import Path

from wade.utils import markers

_SHA_A = "a" * 40
_SHA_B = "b" * 40


class TestShaKeyedMarkers:
    def test_write_then_present(self, tmp_path: Path) -> None:
        assert markers.write_marker(tmp_path, "done", _SHA_A) is True
        assert markers.marker_present(tmp_path, "done", _SHA_A) is True
        assert (tmp_path / ".wade" / f"done@{_SHA_A}").is_file()

    def test_absent_for_different_sha(self, tmp_path: Path) -> None:
        markers.write_marker(tmp_path, "done", _SHA_A)
        # A marker for sha A is not present for sha B — a new commit invalidates it.
        assert markers.marker_present(tmp_path, "done", _SHA_B) is False

    def test_missing_marker_absent(self, tmp_path: Path) -> None:
        assert markers.marker_present(tmp_path, "done", _SHA_A) is False

    def test_write_clears_prior_marker_for_same_name(self, tmp_path: Path) -> None:
        markers.write_marker(tmp_path, "done", _SHA_A)
        markers.write_marker(tmp_path, "done", _SHA_B)
        # Only the current sha's marker survives (bounds .wade/ growth).
        assert markers.marker_present(tmp_path, "done", _SHA_B) is True
        assert markers.marker_present(tmp_path, "done", _SHA_A) is False
        entries = sorted(p.name for p in (tmp_path / ".wade").iterdir())
        assert entries == [f"done@{_SHA_B}"]

    def test_write_does_not_clear_other_names(self, tmp_path: Path) -> None:
        markers.write_marker(tmp_path, "reviewed", _SHA_A)
        markers.write_marker(tmp_path, "done", _SHA_A)
        # Different names are independent.
        assert markers.marker_present(tmp_path, "reviewed", _SHA_A) is True
        assert markers.marker_present(tmp_path, "done", _SHA_A) is True

    def test_clear_markers(self, tmp_path: Path) -> None:
        markers.write_marker(tmp_path, "done", _SHA_A)
        markers.clear_markers(tmp_path, "done")
        assert markers.marker_present(tmp_path, "done", _SHA_A) is False

    def test_no_temp_file_left_behind(self, tmp_path: Path) -> None:
        markers.write_marker(tmp_path, "done", _SHA_A)
        leftovers = [p.name for p in (tmp_path / ".wade").iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_marker_path(self, tmp_path: Path) -> None:
        assert markers.marker_path(tmp_path, "done", _SHA_A) == (
            tmp_path / ".wade" / f"done@{_SHA_A}"
        )


class TestSymlinkSafety:
    def test_symlinked_marker_not_trusted(self, tmp_path: Path) -> None:
        # A planted symlink at the marker path must not read as present.
        wade = tmp_path / ".wade"
        wade.mkdir()
        real = tmp_path / "elsewhere"
        real.write_text("")
        (wade / f"done@{_SHA_A}").symlink_to(real)
        assert markers.marker_present(tmp_path, "done", _SHA_A) is False

    def test_symlinked_wade_dir_not_trusted_on_read(self, tmp_path: Path) -> None:
        # A symlinked .wade dir must not let an outside file count as a marker.
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / f"done@{_SHA_A}").write_text("")
        (tmp_path / ".wade").symlink_to(outside)
        assert markers.marker_present(tmp_path, "done", _SHA_A) is False

    def test_symlinked_wade_dir_not_written_through(self, tmp_path: Path) -> None:
        wt = tmp_path / "wt"
        wt.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (wt / ".wade").symlink_to(outside)
        # The write must refuse rather than land a file outside the worktree.
        assert markers.write_marker(wt, "done", _SHA_A) is False
        assert not (outside / f"done@{_SHA_A}").exists()


class TestFlagMarkers:
    def test_write_and_present(self, tmp_path: Path) -> None:
        assert markers.write_flag_marker(tmp_path, "stop-nudged") is True
        assert markers.flag_marker_present(tmp_path, "stop-nudged") is True

    def test_absent_when_missing(self, tmp_path: Path) -> None:
        assert markers.flag_marker_present(tmp_path, "stop-nudged") is False

    def test_flag_marker_path(self, tmp_path: Path) -> None:
        assert markers.flag_marker_path(tmp_path, "stop-nudged") == (
            tmp_path / ".wade" / "stop-nudged"
        )

    def test_symlinked_flag_not_trusted(self, tmp_path: Path) -> None:
        wade = tmp_path / ".wade"
        wade.mkdir()
        real = tmp_path / "elsewhere"
        real.write_text("")
        (wade / "stop-nudged").symlink_to(real)
        assert markers.flag_marker_present(tmp_path, "stop-nudged") is False
