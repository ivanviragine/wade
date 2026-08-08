"""Small path helpers shared across layers (leaf — imports only stdlib)."""

from __future__ import annotations

from pathlib import PurePosixPath


def normalize_relative_path(path: str) -> str:
    """Normalize a POSIX-style relative path for equality comparison.

    Collapses a leading ``./``, interior ``.`` segments, and duplicate/trailing
    slashes so equivalent spellings compare equal — e.g. ``./KNOWLEDGE.md`` and
    ``KNOWLEDGE.md`` both normalize to ``KNOWLEDGE.md``. Does **not** resolve ``..``
    (ambiguous without a base directory); paths that escape the root are rejected
    elsewhere. Comparison-only — never use the result for filesystem operations.
    """
    return str(PurePosixPath(path))
