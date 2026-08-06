"""SHA-keyed completion markers under a worktree's ``.wade/`` directory.

A **marker** is a zero-byte file at ``.wade/<name>@<sha>`` whose presence means
"the gates named ``<name>`` passed for commit ``<sha>``". Because it is keyed to
a sha, a new commit changes the sha and every prior marker is automatically
stale — **missing or stale ⇒ not done**. This is exactly the fact the ``done``
completion gate, the pre-push backstop, and the Stop hook all want to verify.

Pure-stdlib leaf module (only ``structlog``) so the lean ``wade-hook`` entry
point can import it without paying the crossby/CLI cold-start cost. It also
generalizes the single-shot ``.wade/stop-nudged`` mechanism (see
``flag_marker_*``), so the two implementations can no longer drift.

**Race-safety.** All reads/writes go through a ``.wade`` directory handle opened
with ``O_DIRECTORY | O_NOFOLLOW`` and operate *relative to that handle* without
following symlinks (a symlinked ``.wade`` fails outright). Using a handle rather
than re-resolving the path closes the TOCTOU window where ``.wade`` is swapped
for a symlink between the check and the read/write. On platforms without dir-fd
support a marker is treated as absent (never followed unsafely) — the same
policy the old ``stop_nudge_present`` used.
"""

from __future__ import annotations

import contextlib
import os
import stat as stat_module
from pathlib import Path

import structlog

logger = structlog.get_logger()

_WADE_DIRNAME = ".wade"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _wade_dir(worktree_root: Path) -> Path:
    return worktree_root / _WADE_DIRNAME


def marker_path(worktree_root: Path, name: str, sha: str) -> Path:
    """Absolute path of the sha-keyed ``.wade/<name>@<sha>`` marker."""
    return _wade_dir(worktree_root) / f"{name}@{sha}"


def flag_marker_path(worktree_root: Path, name: str) -> Path:
    """Absolute path of a single-shot (non-sha) ``.wade/<name>`` flag marker."""
    return _wade_dir(worktree_root) / name


# ---------------------------------------------------------------------------
# dir-fd capability probes
# ---------------------------------------------------------------------------


def _read_dir_fd_supported() -> bool:
    return hasattr(os, "O_DIRECTORY") and os.stat in os.supports_dir_fd


def _write_dir_fd_supported() -> bool:
    return hasattr(os, "O_DIRECTORY") and os.open in os.supports_dir_fd


# ---------------------------------------------------------------------------
# Low-level, relname-scoped primitives (shared by sha-keyed + flag markers)
# ---------------------------------------------------------------------------


def _present(worktree_root: Path, relname: str) -> bool:
    """True if ``.wade/<relname>`` is a *trusted* regular file (race-safe)."""
    if not _read_dir_fd_supported():
        return False
    dir_fd = None
    try:
        dir_fd = os.open(_wade_dir(worktree_root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        st = os.stat(relname, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False
    finally:
        if dir_fd is not None:
            os.close(dir_fd)
    return stat_module.S_ISREG(st.st_mode)


def _touch(worktree_root: Path, relname: str) -> bool:
    """Create an empty ``.wade/<relname>`` marker (0o600). Best-effort."""
    if not _write_dir_fd_supported():
        return False
    wade_dir = _wade_dir(worktree_root)
    try:
        wade_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    dir_fd = None
    try:
        dir_fd = os.open(wade_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = os.open(
            relname,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        os.close(fd)
        return True
    except OSError:
        return False
    finally:
        if dir_fd is not None:
            os.close(dir_fd)


def _atomic_write(worktree_root: Path, relname: str) -> bool:
    """Create the (zero-byte) ``.wade/<relname>`` marker safely; return success.

    All I/O is relative to an ``O_DIRECTORY | O_NOFOLLOW`` handle on ``.wade`` so
    a symlinked ``.wade`` can never redirect the write outside the worktree. When
    the platform supports ``renameat`` (``os.replace`` with dir-fds) the marker
    is written to ``*.tmp`` then atomically renamed onto the final name; when it
    does not, the zero-byte marker is created directly with a single ``O_CREAT``
    (atomic enough for a presence flag). There is deliberately **no** path-based
    fallback — following the symlink is worse than not writing the marker, so an
    unsupported platform returns ``False`` (fail toward absent, matching the read
    side).
    """
    wade_dir = _wade_dir(worktree_root)
    try:
        wade_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    if not _write_dir_fd_supported():
        return False  # no safe no-follow write path available

    tmp_name = f"{relname}.tmp"
    dir_fd = None
    try:
        dir_fd = os.open(wade_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if os.replace in os.supports_dir_fd:
            fd = os.open(
                tmp_name,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
                dir_fd=dir_fd,
            )
            os.close(fd)
            os.replace(tmp_name, relname, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        else:
            fd = os.open(
                relname,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
                dir_fd=dir_fd,
            )
            os.close(fd)
        return True
    except OSError:
        if dir_fd is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name, dir_fd=dir_fd)
        return False
    finally:
        if dir_fd is not None:
            os.close(dir_fd)


def _clear_prefix(worktree_root: Path, prefix: str) -> None:
    """Remove every ``.wade/<prefix>*`` entry. Best-effort; ignores errors."""
    wade_dir = _wade_dir(worktree_root)
    if _write_dir_fd_supported() and os.listdir in os.supports_fd:
        dir_fd = None
        try:
            dir_fd = os.open(wade_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for entry in os.listdir(dir_fd):
                if entry.startswith(prefix):
                    with contextlib.suppress(OSError):
                        os.unlink(entry, dir_fd=dir_fd)
        except OSError:
            return
        finally:
            if dir_fd is not None:
                os.close(dir_fd)
        return
    # Fallback: plain path iteration.
    try:
        for path_entry in wade_dir.iterdir():
            if path_entry.name.startswith(prefix):
                with contextlib.suppress(OSError):
                    path_entry.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Public sha-keyed API
# ---------------------------------------------------------------------------


def marker_present(worktree_root: Path, name: str, sha: str) -> bool:
    """True if a trusted ``.wade/<name>@<sha>`` marker exists (race-safe)."""
    return _present(worktree_root, f"{name}@{sha}")


def write_marker(worktree_root: Path, name: str, sha: str) -> bool:
    """Atomically write ``.wade/<name>@<sha>``; return whether it succeeded.

    Clears any prior ``<name>@*`` marker first so only the current sha's marker
    exists — this bounds ``.wade/`` growth over a long session and makes
    "is there *any* ``<name>`` marker" unambiguous. Best-effort: a failed write
    returns ``False`` and callers treat that as "marker absent" (fail toward
    reminding / re-gating).
    """
    clear_markers(worktree_root, name)
    ok = _atomic_write(worktree_root, f"{name}@{sha}")
    if not ok:
        logger.debug("markers.write_failed", name=name, sha=sha)
    return ok


def clear_markers(worktree_root: Path, name: str) -> None:
    """Remove every ``.wade/<name>@*`` marker for ``name``. Best-effort."""
    _clear_prefix(worktree_root, f"{name}@")


# ---------------------------------------------------------------------------
# Public single-shot flag API (generalizes ``.wade/stop-nudged``)
# ---------------------------------------------------------------------------


def flag_marker_present(worktree_root: Path, name: str) -> bool:
    """True if a trusted single-shot ``.wade/<name>`` flag marker exists."""
    return _present(worktree_root, name)


def write_flag_marker(worktree_root: Path, name: str) -> bool:
    """Create the single-shot ``.wade/<name>`` flag marker. Best-effort."""
    return _touch(worktree_root, name)
