"""Detect a project's documentation targets for the doc-update-step skill partial."""

from __future__ import annotations

from pathlib import Path

# Root doc files, in presentation order. Only files that exist are included.
_ROOT_DOC_FILES = ["README.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md"]

# Marker files/dirs (relative to project root) that indicate docs/ is generated
# build output rather than hand-authored source. Deliberately excludes generator
# *config* files (mkdocs.yml, docusaurus.config.js, docs/conf.py, docs/.vitepress,
# docs/book.toml, docs/_config.yml) — under each tool's default convention those
# mark docs/ as hand-authored SOURCE (MkDocs/Docusaurus/Sphinx/VitePress/mdBook/
# Jekyll all build docs/ into a separate output dir by default), so treating them
# as "generated" would exclude docs/ for the common case this feature most needs
# to reach. Add new markers here only when they identify actual build output.
_GENERATED_DOCS_MARKERS = [
    "docs/_build",
]


def detect_doc_targets(project_root: Path) -> list[str]:
    """Detect the documentation files/dirs a project maintains by hand.

    Returns root doc files that exist (in presentation order), plus ``docs/``
    if it exists and is not generated output.

    Generated is decided by a marker file/dir (see ``_GENERATED_DOCS_MARKERS``)
    or a bare ``docs``/``docs/`` entry in ``.gitignore``. The ``.gitignore``
    check is a deliberately narrow heuristic, not exhaustive: it matches only a
    bare ``docs``, ``docs/``, ``/docs``, or ``/docs/`` line and will not catch
    patterns like ``docs/**``, ``**/docs/``, or a nested ``apps/docs/``. Both
    checks are intentionally conservative — a doc-site generator's *config*
    file (``mkdocs.yml``, ``docusaurus.config.js``, ``docs/conf.py``, etc.) is
    NOT treated as a generated marker, because under each tool's default
    convention that config indicates ``docs/`` is hand-authored source, not
    build output.
    """
    targets = [name for name in _ROOT_DOC_FILES if (project_root / name).is_file()]

    docs_dir = project_root / "docs"
    if docs_dir.is_dir() and not _is_generated_docs(project_root):
        targets.append("docs/")

    return targets


def _is_generated_docs(project_root: Path) -> bool:
    """Return True if docs/ looks like generated build output."""
    if any((project_root / marker).exists() for marker in _GENERATED_DOCS_MARKERS):
        return True

    gitignore = project_root / ".gitignore"
    if not gitignore.is_file():
        return False

    for line in gitignore.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped in ("docs", "docs/", "/docs", "/docs/"):
            return True

    return False


def format_doc_targets(targets: list[str]) -> str:
    """Format detected doc targets for insertion into skill text.

    Non-empty input renders as a backtick-quoted, comma-joined list. Empty
    input falls back to generic wording so the step still reads correctly in
    a project with no detected documentation.
    """
    if not targets:
        return "the project's documentation, if it has any"
    return ", ".join(f"`{target}`" for target in targets)
