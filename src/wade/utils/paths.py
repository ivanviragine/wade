"""Small path helpers shared across layers (leaf — imports only stdlib)."""

from __future__ import annotations

import posixpath


def collapse_relative_path(path: str) -> str | None:
    """Canonicalize a POSIX-style relative path for equality comparison.

    Collapses a leading ``./``, interior ``.`` segments, duplicate/trailing
    slashes **and** interior ``..`` segments so equivalent spellings compare
    equal — e.g. ``./KNOWLEDGE.md``, ``docs/../KNOWLEDGE.md`` and ``KNOWLEDGE.md``
    all canonicalize to ``KNOWLEDGE.md``. Returns ``None`` for an absolute path or
    one that escapes its base (a leading ``..`` survives the fold): such a path can
    never name a file inside the project root, so it has no in-root form to
    compare. This is the single policy both the copy-exclusion (bootstrap) and the
    ``copy_to_worktree`` migration apply, so a redundant-``..`` spelling cannot slip
    past one and re-copy main's knowledge file. Comparison-only — never use the
    result for filesystem operations.
    """
    if path.startswith("/"):
        return None
    collapsed = posixpath.normpath(path)
    if collapsed == ".." or collapsed.startswith("../"):
        return None
    return collapsed
