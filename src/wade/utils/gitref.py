"""Lightweight git ref-name validation (no subprocess).

A pure, import-cheap approximation of ``git check-ref-format`` used to validate a
user-declared plan base branch before it ever reaches ``git``/``gh``. It is
deliberately conservative: it rejects clearly-malformed refs (whitespace,
forbidden characters, bad boundaries) so a bad value fails the plan-done gate
with an actionable message instead of surfacing later as a raw git error.

Stdlib-only so the lean :mod:`wade.utils.plan_validation` module (and its
``wade-hook`` Stop-path importer) can use it without pulling in heavier deps.
"""

from __future__ import annotations

import re

# Characters git forbids anywhere in a ref name, plus ASCII control chars and
# DEL. The ``\x00-\x20`` range already covers all control chars, tab, newline,
# and space (0x20); ``\x7f`` adds DEL; the rest are git's explicitly-disallowed
# ref characters.
_FORBIDDEN_RE = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def is_valid_git_ref(ref: str) -> bool:
    """Return ``True`` if *ref* is a plausibly well-formed git branch ref name.

    Approximates ``git check-ref-format --branch`` without shelling out. A
    ``True`` result does **not** guarantee the ref *exists* — only that it is
    syntactically usable as a branch name (existence is checked separately when
    the draft PR is created).
    """
    if not ref:
        return False
    if _FORBIDDEN_RE.search(ref):
        return False
    if ".." in ref or "//" in ref or "@{" in ref:
        return False
    if ref.startswith(("/", "-", ".")) or ref.endswith(("/", ".", ".lock")):
        return False
    # No path component may be empty or end in ``.lock``.
    return all(part and not part.endswith(".lock") for part in ref.split("/"))
