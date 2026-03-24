"""Knowledge service — append and manage project knowledge entries."""

from __future__ import annotations

import fcntl
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from wade.models.config import KnowledgeConfig

KNOWLEDGE_TEMPLATE = """\
# Project Knowledge

Shared learnings from AI planning and implementation sessions.
Read this at the start of every session. Add new entries via `wade knowledge add`.

---
"""

# Regex to match entry headings: ## <id> | <date> | <session_type> [+N/-M]
# Also matches old-style headings without IDs: ## <date> | <session_type>
_ENTRY_HEADING_RE = re.compile(
    r"^## (?:([0-9a-f]{8}) \| )?(\d{4}-\d{2}-\d{2}) \| (.+?)(?:\s+\[.*\])?\s*$"
)

RatingsDict = dict[str, dict[str, Any]]


@dataclass(frozen=True)
class KnowledgeEntry:
    """Result of appending a knowledge entry."""

    path: Path
    entry_id: str


@dataclass(frozen=True)
class ParsedEntry:
    """A parsed knowledge entry from the knowledge file."""

    entry_id: str | None
    date: str
    heading_rest: str
    content: str
    raw: str


def _generate_entry_id() -> str:
    """Generate a short entry ID (first 8 hex chars of uuid4)."""
    return uuid.uuid4().hex[:8]


def resolve_knowledge_path(project_root: Path, config: KnowledgeConfig) -> Path:
    """Resolve absolute path to the knowledge file from config.

    Rejects absolute paths and paths that escape the project root via ``..``.
    """
    if Path(config.path).is_absolute():
        raise ValueError(f"Invalid knowledge path {config.path!r}: must be inside project root")
    root = project_root.resolve()
    resolved = (root / config.path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(
            f"Invalid knowledge path {config.path!r}: must be inside project root {root}"
        )
    return resolved


def resolve_ratings_path(knowledge_path: Path) -> Path:
    """Derive sidecar ratings file path from knowledge file path.

    ``KNOWLEDGE.md`` → ``KNOWLEDGE.ratings.yml``
    """
    return knowledge_path.with_suffix(".ratings.yml")


def ensure_knowledge_file(project_root: Path, config: KnowledgeConfig) -> Path:
    """Create the knowledge file with a template header if it doesn't exist.

    Returns the path to the knowledge file.
    """
    path = resolve_knowledge_path(project_root, config)
    if path.is_dir():
        raise ValueError(f"Knowledge path {config.path!r} points to a directory, not a file")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(KNOWLEDGE_TEMPLATE, encoding="utf-8")
    return path


def read_knowledge(project_root: Path, config: KnowledgeConfig) -> str | None:
    """Read and return the project knowledge file content.

    Returns None if the file does not exist.
    Does not create the file.
    """
    path = resolve_knowledge_path(project_root, config)
    if not path.exists():
        return None
    if path.is_dir():
        raise ValueError(f"Knowledge path {config.path!r} points to a directory, not a file")
    return path.read_text(encoding="utf-8")


def parse_entries(text: str) -> list[ParsedEntry]:
    """Parse knowledge file text into individual entries.

    Handles entries with and without IDs. Skips the template header.
    """
    entries: list[ParsedEntry] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        match = _ENTRY_HEADING_RE.match(lines[i])
        if match:
            entry_id = match.group(1)  # None for old-style entries
            date = match.group(2)
            heading_rest = match.group(3)
            heading_line = lines[i]

            # Collect content lines until next heading or end
            content_lines: list[str] = []
            i += 1
            while i < len(lines):
                if _ENTRY_HEADING_RE.match(lines[i]):
                    break
                content_lines.append(lines[i])
                i += 1

            raw_block = heading_line + "\n" + "\n".join(content_lines)
            # Strip trailing separator and whitespace from content
            content_text = "\n".join(content_lines).strip()
            if content_text.endswith("---"):
                content_text = content_text[:-3].strip()

            entries.append(
                ParsedEntry(
                    entry_id=entry_id,
                    date=date,
                    heading_rest=heading_rest,
                    content=content_text,
                    raw=raw_block,
                )
            )
        else:
            i += 1
    return entries


def find_entry_id(knowledge_path: Path, entry_id: str) -> bool:
    """Check whether an entry ID exists in the knowledge file."""
    if not knowledge_path.exists():
        return False
    text = knowledge_path.read_text(encoding="utf-8")
    entries = parse_entries(text)
    return any(e.entry_id == entry_id for e in entries)


def read_ratings(ratings_path: Path) -> RatingsDict:
    """Load the sidecar ratings YAML file.

    Returns an empty dict if the file doesn't exist.
    """
    if not ratings_path.exists():
        return {}
    content = ratings_path.read_text(encoding="utf-8")
    if not content.strip():
        return {}
    data: Any = yaml.safe_load(content)
    if isinstance(data, dict):
        return data
    return {}


def _read_modify_write_ratings(
    ratings_path: Path,
    modify_fn: Callable[[RatingsDict], None],
) -> RatingsDict:
    """Read-modify-write the ratings file under an exclusive lock.

    ``modify_fn`` receives the current ratings dict and mutates it in place.
    """
    ratings_path.parent.mkdir(parents=True, exist_ok=True)
    # Open for read+write, creating if needed
    fd = ratings_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        fd.seek(0)
        content = fd.read()
        data: RatingsDict = {}
        if content.strip():
            loaded: Any = yaml.safe_load(content)
            if isinstance(loaded, dict):
                data = loaded

        modify_fn(data)

        fd.seek(0)
        fd.truncate()
        yaml.safe_dump(data, fd, default_flow_style=False, sort_keys=True)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    return data


def record_rating(
    ratings_path: Path,
    entry_id: str,
    direction: str,
) -> None:
    """Increment the up or down counter for an entry in the sidecar file.

    ``direction`` must be ``"up"`` or ``"down"``.
    """
    if direction not in ("up", "down"):
        raise ValueError(f"Invalid direction {direction!r}: must be 'up' or 'down'")

    def _modify(data: RatingsDict) -> None:
        entry = data.setdefault(entry_id, {"up": 0, "down": 0})
        entry.setdefault("up", 0)
        entry.setdefault("down", 0)
        entry[direction] = int(entry[direction]) + 1

    _read_modify_write_ratings(ratings_path, _modify)


def record_supersede(
    ratings_path: Path,
    old_id: str,
    new_id: str,
) -> None:
    """Record a supersedes link: old_id is superseded by new_id."""

    def _modify(data: RatingsDict) -> None:
        entry = data.setdefault(old_id, {"up": 0, "down": 0})
        entry["superseded_by"] = new_id

    _read_modify_write_ratings(ratings_path, _modify)


def get_annotated_knowledge(
    project_root: Path,
    config: KnowledgeConfig,
    min_score: int | None = None,
) -> str | None:
    """Read knowledge file, annotate headings with scores, and optionally filter.

    Returns None if the knowledge file does not exist.
    """
    path = resolve_knowledge_path(project_root, config)
    if not path.exists():
        return None
    if path.is_dir():
        raise ValueError(f"Knowledge path {config.path!r} points to a directory, not a file")

    text = path.read_text(encoding="utf-8")
    entries = parse_entries(text)

    if not entries:
        return text

    ratings_path = resolve_ratings_path(path)
    ratings = read_ratings(ratings_path)

    # Build the header (everything before the first entry)
    first_entry_pos = text.find(entries[0].raw)
    header = text[:first_entry_pos] if first_entry_pos > 0 else ""

    result_parts = [header]
    for entry in entries:
        entry_ratings = ratings.get(entry.entry_id, {}) if entry.entry_id else {}
        up = int(entry_ratings.get("up", 0)) if entry_ratings else 0
        down = int(entry_ratings.get("down", 0)) if entry_ratings else 0
        net_score = up - down
        has_ratings = bool(entry_ratings) and (up > 0 or down > 0)

        # Apply min_score filter
        if min_score is not None and net_score < min_score:
            continue

        # Re-build the heading with score annotation
        heading_match = _ENTRY_HEADING_RE.match(entry.raw.split("\n")[0])
        if heading_match and has_ratings:
            id_part = f"{entry.entry_id} | " if entry.entry_id else ""
            heading = f"## {id_part}{entry.date} | {entry.heading_rest} [+{up}/-{down}]"
            raw_lines = entry.raw.split("\n")
            raw_lines[0] = heading
            result_parts.append("\n".join(raw_lines))
        else:
            result_parts.append(entry.raw)

    output = "".join(result_parts)
    if not output.endswith("\n"):
        output += "\n"
    return output


def append_knowledge(
    project_root: Path,
    config: KnowledgeConfig,
    content: str,
    session_type: str,
    issue_ref: str | None = None,
) -> KnowledgeEntry:
    """Format and append a knowledge entry to the knowledge file.

    Returns a KnowledgeEntry with the path and generated entry ID.
    """
    path = ensure_knowledge_file(project_root, config)

    entry_id = _generate_entry_id()
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    issue_part = f" | Issue #{issue_ref}" if issue_ref else ""
    header = f"## {entry_id} | {timestamp} | {session_type}{issue_part}"

    entry = f"\n{header}\n\n{content.strip()}\n\n---\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)

    return KnowledgeEntry(path=path, entry_id=entry_id)
