"""Canonical conventional-commit **title** validation — pure, import-cheap.

This is the single Python source of truth for the conventional-commit *type*
list and the "is this a valid title?" check. It is shared by:

- :mod:`wade.utils.plan_validation` — the ``wade plan`` issue-creation gate.
- :mod:`wade.services.task_service` — ``create_task`` / ``create_from_plan_file``
  hard-enforcement and ``create_interactive`` re-prompting.
- :mod:`wade.services.implementation_service.done` — the PR-title validate/sync
  step (PR title is derived from the issue title, so both must be conventional
  or the ``PR Title Lint`` CI check fails).

**Import cost matters.** Like :mod:`wade.utils.plan_validation` (which imports
from here), this module must stay stdlib-only — no ``crossby``/UI imports — so
the lean ``wade-hook`` Stop path keeps its low cold-start cost. Only ``re`` is
imported here.

**Canonical list.** :data:`CONVENTIONAL_COMMIT_TYPES` below is the source of
truth for the *Python* call sites. The other conventional-commit definitions
(the bash ``templates/hooks/commit-msg`` hook, the ``pr-title-lint.yml`` /
``auto-version.yml`` YAML lists, ``scripts/changelog.py``) are intentionally
**not** consolidated here — they live in different languages/concerns. When the
canonical set changes, sync those by hand and keep this module as the reference.
"""

from __future__ import annotations

import re

# The canonical conventional-commit type list for wade's Python call sites.
# Order mirrors the historical regex in ``plan_validation.py`` and the
# ``pr-title-lint.yml`` CI check (12 types, including ``update`` which earns a
# minor version bump). Keep in sync with ``templates/hooks/commit-msg``.
CONVENTIONAL_COMMIT_TYPES: tuple[str, ...] = (
    "feat",
    "fix",
    "docs",
    "refactor",
    "chore",
    "style",
    "perf",
    "test",
    "ci",
    "build",
    "revert",
    "update",
)

# ``<type>[(scope)][!]: <description>`` with at least one non-space character
# after the colon+space. Byte-for-byte equivalent to the regex previously
# defined inline in ``plan_validation.py`` — behavior for ``wade plan`` is
# unchanged.
CONVENTIONAL_COMMIT_RE = re.compile(rf"^({'|'.join(CONVENTIONAL_COMMIT_TYPES)})(\(.+\))?!?:\s+\S")

# Shared guidance appended to every "bad title" message so the error text stays
# identical across the plan gate, task-creation raise, and done() PR-title gate.
CONVENTIONAL_TITLE_HELP = (
    "Use: feat, fix, docs, refactor, chore, style, perf, test, ci, build, "
    "revert, update. Example: 'feat: add retry logic to task provider'."
)


def is_conventional_title(title: str) -> bool:
    """Return True when *title* starts with a conventional-commit prefix."""
    return bool(CONVENTIONAL_COMMIT_RE.match(title))


def conventional_title_error(title: str) -> str:
    """Build the canonical actionable error string for a non-conventional title."""
    return (
        f"Title '{title}' does not start with a conventional commit prefix. "
        + CONVENTIONAL_TITLE_HELP
    )


class ConventionalTitleError(ValueError):
    """Raised when a title is not a valid conventional-commit title.

    Subclasses :class:`ValueError` so callers can catch it precisely (rather
    than a bare ``ValueError``) at the task-creation boundaries and surface a
    clean, actionable CLI message instead of a traceback.
    """

    def __init__(self, title: str) -> None:
        self.title: str = title
        super().__init__(conventional_title_error(title))
