"""Descriptor-relative, no-follow I/O for completion-gate state.

Every operation opens ``.wade`` and its requested child directories through
directory descriptors. Unsupported platforms and any unsafe or malformed path
fail closed; callers interpret ``None``/``False`` as absent state.
"""

from __future__ import annotations

import contextlib
import os
import stat
import uuid
from pathlib import Path

MAX_STATE_FILE_BYTES = 256 * 1024


def _supported(*, write: bool = False) -> bool:
    base = (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
    )
    if not write:
        return base
    replace_supported = os.replace in os.supports_dir_fd or os.rename in os.supports_dir_fd
    return base and os.mkdir in os.supports_dir_fd and replace_supported


def _close(fd: int | None) -> None:
    if fd is not None:
        with contextlib.suppress(OSError):
            os.close(fd)


def _open_directory(parent_fd: int, name: str, *, create: bool) -> int:
    if create:
        with contextlib.suppress(FileExistsError):
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    return os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=parent_fd,
    )


def _open_nested(root: Path, directories: tuple[str, ...], *, create: bool) -> int | None:
    if any(not name or "/" in name or name in {".", ".."} for name in directories):
        return None
    if not _supported(write=create):
        return None
    wade_path = root / ".wade"
    if create:
        try:
            wade_path.mkdir(mode=0o700, parents=False, exist_ok=True)
        except OSError:
            return None
    fd: int | None = None
    try:
        fd = os.open(wade_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for name in directories:
            child = _open_directory(fd, name, create=create)
            os.close(fd)
            fd = child
        return fd
    except OSError:
        _close(fd)
        return None


def read_state_file(
    root: Path,
    directories: tuple[str, ...],
    filename: str,
    *,
    max_bytes: int = MAX_STATE_FILE_BYTES,
) -> bytes | None:
    """Read one trusted regular file, returning ``None`` on every unsafe path."""

    if not filename or "/" in filename or filename in {".", ".."}:
        return None
    dir_fd = _open_nested(root, directories, create=False)
    if dir_fd is None:
        return None
    file_fd: int | None = None
    try:
        file_fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > max_bytes:
            return None
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(file_fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        return data if len(data) <= max_bytes else None
    except OSError:
        return None
    finally:
        _close(file_fd)
        _close(dir_fd)


def list_state_files(root: Path, directories: tuple[str, ...]) -> tuple[str, ...] | None:
    """List a trusted state directory, failing closed when fd listing is unavailable."""

    if os.listdir not in os.supports_fd:
        return None
    dir_fd = _open_nested(root, directories, create=False)
    if dir_fd is None:
        return None
    try:
        return tuple(sorted(os.listdir(dir_fd)))
    except OSError:
        return None
    finally:
        _close(dir_fd)


def state_directory_present(root: Path, directories: tuple[str, ...]) -> bool:
    """Whether state exists here, including unsafe or symlinked state.

    This is conservative by design: an existing but unopenable ``.wade``
    counts as present so callers never fall back to trusting legacy state.
    """

    try:
        root_stat = os.lstat(root / ".wade")
    except OSError:
        return False
    if not stat.S_ISDIR(root_stat.st_mode):
        return True
    if not directories:
        return True
    parent_fd: int | None = None
    observed = False
    try:
        parent_fd = os.open(root / ".wade", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for name in directories:
            child_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            observed = True
            if not stat.S_ISDIR(child_stat.st_mode):
                return True
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            os.close(parent_fd)
            parent_fd = child_fd
        return True
    except OSError:
        return observed
    finally:
        _close(parent_fd)


def atomic_write_state_file(
    root: Path,
    directories: tuple[str, ...],
    filename: str,
    data: bytes,
) -> bool:
    """Atomically replace one trusted state file relative to a no-follow dir fd."""

    if (
        not filename
        or "/" in filename
        or filename in {".", ".."}
        or len(data) > MAX_STATE_FILE_BYTES
    ):
        return False
    dir_fd = _open_nested(root, directories, create=True)
    if dir_fd is None:
        return False
    tmp_name = f".{filename}.{uuid.uuid4().hex}.tmp"
    file_fd: int | None = None
    try:
        file_fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        view = memoryview(data)
        while view:
            written = os.write(file_fd, view)
            view = view[written:]
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = None
        if os.replace in os.supports_dir_fd:
            os.replace(tmp_name, filename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        else:
            os.rename(tmp_name, filename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
        return True
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name, dir_fd=dir_fd)
        return False
    finally:
        _close(file_fd)
        _close(dir_fd)
