"""Descriptor-relative, no-follow I/O for completion-gate state.

Every operation opens ``.wade`` and its requested child directories through
directory descriptors. Unsupported platforms and any unsafe or malformed path
fail closed. Compatibility callers interpret ``None``/``False`` as absent state;
strict callers receive typed unsafe, permission, or I/O failures.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import uuid
from pathlib import Path

MAX_STATE_FILE_BYTES = 256 * 1024
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_MKDIR_SUPPORTS_DIR_FD = os.mkdir in os.supports_dir_fd
_REPLACE_SUPPORTS_DIR_FD = os.replace in os.supports_dir_fd
_RENAME_SUPPORTS_DIR_FD = os.rename in os.supports_dir_fd


class StateFileError(ValueError):
    """A strict state operation could not prove that its target was safe."""


class StateFileAccessError(StateFileError):
    """The OS denied access to an otherwise requested state path."""

    def __init__(self, path: Path, cause: OSError) -> None:
        self.path = path
        self.errno = cause.errno
        detail = os.strerror(cause.errno) if cause.errno is not None else str(cause)
        super().__init__(f"Filesystem access denied for WADE state at {path}: {detail}")


class StateFileIOError(StateFileError):
    """The OS failed a state operation for a reason other than path safety."""

    def __init__(self, path: Path, cause: OSError) -> None:
        self.path = path
        self.errno = cause.errno
        super().__init__(f"Filesystem I/O failed for WADE state at {path}: {cause}")


class StateFileUnsafeError(StateFileError):
    """State is absent, structurally unsafe, unsupported, or outside its bounds."""


def _strict_error(path: Path, exc: OSError) -> StateFileError:
    if exc.errno in {errno.EACCES, errno.EPERM}:
        return StateFileAccessError(path, exc)
    if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EINVAL}:
        return StateFileUnsafeError(f"WADE state is absent or unsafe at {path}: {exc}")
    return StateFileIOError(path, exc)


def _supported(*, write: bool = False) -> bool:
    base = (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and _OPEN_SUPPORTS_DIR_FD
        and _STAT_SUPPORTS_DIR_FD
    )
    if not write:
        return base
    replace_supported = _REPLACE_SUPPORTS_DIR_FD or _RENAME_SUPPORTS_DIR_FD
    return base and _MKDIR_SUPPORTS_DIR_FD and replace_supported


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


def _open_nested_strict(root: Path, directories: tuple[str, ...]) -> int:
    """Open a state directory while preserving permission-denied failures."""

    if any(not name or "/" in name or name in {".", ".."} for name in directories):
        raise StateFileUnsafeError("WADE state contains an invalid directory component")
    if not _supported():
        raise StateFileUnsafeError("Safe descriptor-relative state reads are unsupported")
    current_path = root / ".wade"
    fd: int | None = None
    try:
        try:
            fd = os.open(current_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise _strict_error(current_path, exc) from exc
        for name in directories:
            current_path /= name
            try:
                child = _open_directory(fd, name, create=False)
            except OSError as exc:
                raise _strict_error(current_path, exc) from exc
            os.close(fd)
            fd = child
        result = fd
        fd = None
        return result
    finally:
        _close(fd)


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


def read_state_file_strict(
    root: Path,
    directories: tuple[str, ...],
    filename: str,
    *,
    max_bytes: int = MAX_STATE_FILE_BYTES,
) -> bytes:
    """Read trusted state and distinguish OS access denial from unsafe state.

    This is intentionally additive.  Most state consumers retain the historical
    fail-closed ``None`` contract from :func:`read_state_file`; trusted parent
    handoffs use this strict form when an access denial is recoverable and must
    not be misreported as an absent artifact.
    """

    if not filename or "/" in filename or filename in {".", ".."}:
        raise StateFileUnsafeError("WADE state contains an invalid filename")
    dir_fd = _open_nested_strict(root, directories)
    path = root / ".wade" / Path(*directories) / filename
    file_fd: int | None = None
    try:
        try:
            file_fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
            file_stat = os.fstat(file_fd)
        except OSError as exc:
            raise _strict_error(path, exc) from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise StateFileUnsafeError(f"WADE state is not a regular file: {path}")
        if file_stat.st_size > max_bytes:
            raise StateFileUnsafeError(f"WADE state exceeds its {max_bytes}-byte limit: {path}")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            try:
                chunk = os.read(file_fd, min(65_536, remaining))
            except OSError as exc:
                raise _strict_error(path, exc) from exc
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise StateFileUnsafeError(f"WADE state exceeds its {max_bytes}-byte limit: {path}")
        return data
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


def list_state_files_strict(root: Path, directories: tuple[str, ...]) -> tuple[str, ...]:
    """List trusted state while preserving permission-denied failures."""

    if os.listdir not in os.supports_fd:
        raise StateFileUnsafeError("Safe descriptor-relative state listing is unsupported")
    dir_fd = _open_nested_strict(root, directories)
    path = root / ".wade" / Path(*directories)
    try:
        try:
            return tuple(sorted(os.listdir(dir_fd)))
        except OSError as exc:
            raise _strict_error(path, exc) from exc
    finally:
        _close(dir_fd)


def state_file_present(root: Path, directories: tuple[str, ...], filename: str) -> bool:
    """Whether a state file exists, treating an unsafe path as present.

    This is intentionally more conservative than :func:`read_state_file`: a
    caller that cannot safely read an existing state file must distinguish that
    condition from a genuinely absent file, so it can fall back rather than
    silently create or trust replacement state.
    """

    if not filename or "/" in filename or filename in {".", ".."}:
        return False
    dir_fd = _open_nested(root, directories, create=False)
    if dir_fd is None:
        return state_directory_present(root, directories)
    try:
        os.stat(filename, dir_fd=dir_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True
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
    *,
    max_bytes: int = MAX_STATE_FILE_BYTES,
) -> bool:
    """Atomically replace one trusted state file relative to a no-follow dir fd."""

    if not filename or "/" in filename or filename in {".", ".."} or len(data) > max_bytes:
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


def exclusive_write_state_file(
    root: Path, directories: tuple[str, ...], filename: str, content: str
) -> bool:
    """Create a private artifact without following links or replacing any existing file."""
    if not filename or "/" in filename or filename in {".", ".."}:
        return False
    dir_fd = _open_nested(root, directories, create=True)
    if dir_fd is None:
        return False
    try:
        fd = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        return True
    except OSError:
        return False
    finally:
        _close(dir_fd)


def delete_state_file(root: Path, directories: tuple[str, ...], filename: str) -> bool:
    """Safely delete one state file, without following a path component or file link."""

    if not filename or "/" in filename or filename in {".", ".."}:
        return False
    dir_fd = _open_nested(root, directories, create=False)
    if dir_fd is None:
        return False
    try:
        entry = os.stat(filename, dir_fd=dir_fd, follow_symlinks=False)
        if not stat.S_ISREG(entry.st_mode):
            return False
        os.unlink(filename, dir_fd=dir_fd)
        os.fsync(dir_fd)
        return True
    except OSError:
        return False
    finally:
        _close(dir_fd)
