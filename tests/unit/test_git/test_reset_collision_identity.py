"""Windows path-comparison model against a case-sensitive filesystem inventory.

This runs on any host. It does not claim actual Windows/Git runtime coverage.
"""

from __future__ import annotations

import stat
from contextlib import contextmanager
from pathlib import PureWindowsPath
from types import SimpleNamespace

import pytest

from wade.git import branch


@pytest.mark.parametrize("nested", [False, True])
def test_case_sensitive_occupancy_is_not_conflated_by_windows_path_equality(
    monkeypatch, nested: bool
) -> None:
    root = PureWindowsPath("C:/work")
    # Different directory entries may share an inode (hard links). Neither
    # Python's path equality nor inode equality makes an untracked name tracked.
    nodes = (
        {
            "Dir": (stat.S_IFDIR, 2),
            "Dir/tracked": (stat.S_IFREG, 4),
            "dir": (stat.S_IFDIR, 3),
            "dir/precious": (stat.S_IFREG, 4),
        }
        if nested
        else {"Foo": (stat.S_IFREG, 2), "foo": (stat.S_IFREG, 3)}
    )
    inventory = {str(root / name): value for name, value in nodes.items()}
    inventory[str(root)] = (stat.S_IFDIR, 1)

    class CaseSensitiveWindowsPath(PureWindowsPath):
        def lstat(self):
            try:
                mode, inode = inventory[str(self)]
            except KeyError:
                raise FileNotFoundError(str(self)) from None
            return SimpleNamespace(st_mode=mode, st_ino=inode, st_dev=1)

    @contextmanager
    def scandir(directory):
        # Resolve entries by exact filesystem spelling, independently of
        # PureWindowsPath's case-folded equality and hashing.
        yield [
            SimpleNamespace(path=str(root / name))
            for name in nodes
            if str((root / name).parent) == str(directory)
        ]

    tracked = "Dir/tracked" if nested else "Foo"
    precious = "dir/precious" if nested else "foo"
    monkeypatch.setattr(branch, "Path", CaseSensitiveWindowsPath)
    monkeypatch.setattr(branch.os, "scandir", scandir)
    monkeypatch.setattr(
        branch,
        "_reset_inventory",
        lambda worktree, *args: (
            (tracked,) if args[0] == "ls-files" else (f"100644 blob {'1' * 40}\t{precious}",)
        ),
    )
    assert CaseSensitiveWindowsPath(root / "Foo") == CaseSensitiveWindowsPath(root / "foo")
    assert branch._reset_collisions(CaseSensitiveWindowsPath(root), "destination") == (precious,)
