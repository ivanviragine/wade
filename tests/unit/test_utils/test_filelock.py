"""Tests for the shared cross-platform file lock."""

from __future__ import annotations

import glob
import tempfile
from pathlib import Path

from wade.utils.filelock import file_lock


def test_lock_creates_no_sibling_and_allows_write(tmp_path: Path) -> None:
    # The lock must NOT appear next to the guarded (possibly tracked) file — it lives
    # in the system temp dir, so it can never dirty a tracked file's directory.
    target = tmp_path / "KNOWLEDGE.md"
    with file_lock(target):
        target.write_text("content\n", encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "content\n"
    assert not (tmp_path / ".KNOWLEDGE.md.lock").exists()
    assert list(tmp_path.glob("*.lock")) == []


def test_same_path_maps_to_same_lock_file(tmp_path: Path) -> None:
    # All processes/callers locking the same absolute path rendezvous on one lock.
    target = tmp_path / "sub" / "K.md"

    def _lock_name(p: Path) -> str:
        import hashlib

        p.parent.mkdir(parents=True, exist_ok=True)
        return f"wade-filelock-{hashlib.sha256(str(p.resolve()).encode()).hexdigest()[:16]}.lock"

    expected = Path(tempfile.gettempdir()) / _lock_name(target)
    with file_lock(target):
        assert expected.exists()


def test_creates_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "FILE.md"
    with file_lock(target):
        pass
    assert target.parent.is_dir()


def test_can_lock_a_lexical_path_without_creating_its_parent(tmp_path: Path) -> None:
    """Safe-state callers validate/create directories themselves after locking."""
    target = tmp_path / "missing" / "state.json"

    with file_lock(target, create_parent=False, resolve_path=False):
        pass

    assert not target.parent.exists()


def test_lock_is_reentrant_across_sequential_calls(tmp_path: Path) -> None:
    target = tmp_path / "F.md"
    for i in range(3):
        with file_lock(target):
            target.write_text(str(i), encoding="utf-8")
    assert target.read_text(encoding="utf-8") == "2"
    # No lock residue in the guarded directory.
    assert glob.glob(str(tmp_path / "*.lock")) == []
