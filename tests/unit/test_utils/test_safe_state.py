"""Strict and compatibility contracts for descriptor-relative state reads."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from wade.utils import safe_state
from wade.utils.safe_state import (
    StateFileAccessError,
    StateFileIOError,
    StateFileUnsafeError,
    list_state_files_strict,
    read_state_file,
    read_state_file_strict,
)


@pytest.fixture
def state_root(tmp_path: Path) -> Path:
    plans = tmp_path / ".wade/plans"
    plans.mkdir(parents=True)
    (plans / "state.json").write_text("{}")
    return tmp_path


@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EPERM])
@pytest.mark.parametrize("denied_component", [".wade", "plans", "state.json"])
def test_strict_read_preserves_permission_denial_at_each_open(
    state_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
    denied_component: str,
) -> None:
    original_open = os.open

    def denied_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        component = Path(path).name
        if component == denied_component:
            raise OSError(error_number, os.strerror(error_number), os.fspath(path))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(safe_state.os, "open", denied_open)

    with pytest.raises(StateFileAccessError, match="access denied"):
        read_state_file_strict(state_root, ("plans",), "state.json")
    # Existing callers retain the historical fail-closed compatibility contract.
    assert read_state_file(state_root, ("plans",), "state.json") is None


@pytest.mark.parametrize("denied_component", [".wade", "plans"])
def test_strict_listing_preserves_permission_denial(
    state_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    denied_component: str,
) -> None:
    original_open = os.open

    def denied_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if Path(path).name == denied_component:
            raise PermissionError(errno.EACCES, "denied", os.fspath(path))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(safe_state.os, "open", denied_open)
    with pytest.raises(StateFileAccessError):
        list_state_files_strict(state_root, ("plans",))


def test_strict_read_rejects_missing_symlink_non_regular_and_oversized_state(
    state_root: Path,
) -> None:
    plans = state_root / ".wade/plans"
    with pytest.raises(StateFileUnsafeError):
        read_state_file_strict(state_root, ("plans",), "missing.json")

    (plans / "outside.json").write_text("{}")
    (plans / "link.json").symlink_to(plans / "outside.json")
    with pytest.raises(StateFileUnsafeError):
        read_state_file_strict(state_root, ("plans",), "link.json")

    (plans / "directory.json").mkdir()
    with pytest.raises(StateFileUnsafeError):
        read_state_file_strict(state_root, ("plans",), "directory.json")

    (plans / "large.json").write_bytes(b"1234")
    with pytest.raises(StateFileUnsafeError, match="exceeds"):
        read_state_file_strict(state_root, ("plans",), "large.json", max_bytes=3)


def test_strict_read_does_not_collapse_other_os_failures(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_open = os.open

    def failed_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if Path(path).name == "state.json":
            raise OSError(errno.EIO, "I/O error", os.fspath(path))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(safe_state.os, "open", failed_open)
    with pytest.raises(StateFileIOError, match="I/O failed"):
        read_state_file_strict(state_root, ("plans",), "state.json")
