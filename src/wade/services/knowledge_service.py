"""Knowledge service — append and manage project knowledge entries."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from wade.models.config import KnowledgeConfig

KNOWLEDGE_TEMPLATE = """\
# Project Knowledge

Shared learnings from AI planning and implementation sessions.
Read this at the start of every session. Add new entries via `wade knowledge add`.

---
"""


def resolve_knowledge_path(project_root: Path, config: KnowledgeConfig) -> Path:
    """Resolve absolute path to the knowledge file from config.

    Rejects absolute paths and paths that escape the project root via ``..``.
    """
    root = project_root.resolve()
    resolved = (root / config.path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(
            f"Invalid knowledge path {config.path!r}: must be inside project root {root}"
        )
    return resolved


def ensure_knowledge_file(project_root: Path, config: KnowledgeConfig) -> Path:
    """Create the knowledge file with a template header if it doesn't exist.

    Returns the path to the knowledge file.
    """
    path = resolve_knowledge_path(project_root, config)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(KNOWLEDGE_TEMPLATE, encoding="utf-8")
    return path


def append_knowledge(
    project_root: Path,
    config: KnowledgeConfig,
    content: str,
    session_type: str,
    issue_ref: str | None = None,
) -> Path:
    """Format and append a knowledge entry to the knowledge file.

    Returns the path to the knowledge file.
    """
    path = ensure_knowledge_file(project_root, config)

    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    issue_part = f" | Issue #{issue_ref}" if issue_ref else ""
    header = f"## {timestamp} | {session_type}{issue_part}"

    entry = f"\n{header}\n\n{content.strip()}\n\n---\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)

    return path
