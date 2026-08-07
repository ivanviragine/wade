"""Cross-platform advisory file lock — a single implementation used everywhere.

The conditional ``fcntl`` / ``msvcrt`` import lives *inside this module* so that
callers never ``import fcntl`` unconditionally (which raises ``ModuleNotFoundError``
at import time on Windows). Both the markdown provider and the knowledge service
route their locking through :func:`file_lock` so there is exactly one lock
implementation to reason about.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

if sys.platform != "win32":
    import fcntl

    msvcrt = None
else:  # pragma: no cover -- exercised only on Windows
    import msvcrt

    fcntl = None  # type: ignore[assignment]


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive cross-process lock for a read-modify-write cycle on *path*.

    Blocks on a sibling ``.<name>.lock`` file so the whole load+mutate+persist is
    observed as one atomic unit — an atomic file write protects against *torn*
    writes, not against two processes each loading a snapshot and the second
    clobbering the first (lost update).

    The contract is identical across platforms; only the primitive differs:
    ``fcntl.flock`` (POSIX) blocks on the open file description, while
    ``msvcrt.locking`` (Windows) byte-locks the first byte of the lock file in
    blocking-exclusive mode.

    Args:
        path: The file being guarded. The lock is taken on a dedicated lock file in
            the system temp dir, keyed by *path*'s absolute path — never on *path*
            itself (so the guarded file can be created, truncated, or replaced while
            the lock is held) and never as a sibling of it (so it can't dirty a
            tracked file's directory). All processes locking the same absolute path
            still rendezvous on the same lock file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    lock_path = Path(tempfile.gettempdir()) / f"wade-filelock-{digest}.lock"
    # O_RDWR | O_CREAT so we can both create and lock the file.
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:  # pragma: no cover -- Windows path
            # msvcrt locks a byte range; ensure the file has at least 1 byte.
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        yield
    finally:
        with contextlib.suppress(OSError):
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            else:  # pragma: no cover -- Windows path
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)
