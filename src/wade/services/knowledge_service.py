"""Knowledge service — append and manage project knowledge entries."""

from __future__ import annotations

import fcntl
import math
import re
import statistics
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from wade.models.config import KnowledgeConfig

KNOWLEDGE_TEMPLATE = """\
# Project Knowledge

Shared learnings from AI planning and implementation sessions.
Read this at the start of every session. Add new entries via `wade knowledge add`.

---
"""

# Regex to match entry headings: ## <id> | <date> | <rest> [+N/-M]
# Also matches old-style headings without IDs: ## <date> | <rest>
# ID can be 8-char hex (legacy), alphanumeric with hyphens and underscores, or absent.
_ENTRY_HEADING_RE = re.compile(
    r"^## (?:([a-zA-Z0-9_-]+) \| )?(\d{4}-\d{2}-\d{2}) \| (.+?)(?:\s+\[.*\])?\s*$"
)

# Tag validation: lowercase kebab-case, max 30 chars
_TAG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_TAG_MAX_LEN = 30


class KnowledgeEntry(BaseModel, frozen=True):
    """Result of appending a knowledge entry."""

    path: Path
    entry_id: str


class ParsedEntry(BaseModel, frozen=True):
    """A parsed knowledge entry from the knowledge file."""

    entry_id: str | None
    date: str
    heading_rest: str
    tags: list[str] = []
    content: str
    raw: str


class EntryRating(BaseModel):
    """Rating data for a single knowledge entry."""

    up: int = 0
    down: int = 0
    superseded_by: str | None = None


def _generate_entry_id() -> str:
    """Generate a short entry ID (first 8 hex chars of uuid4)."""
    return uuid.uuid4().hex[:8]


def validate_tag(tag: str) -> str | None:
    """Validate a tag string. Returns error message or None if valid."""
    if not tag:
        return "Tag cannot be empty"
    if len(tag) > _TAG_MAX_LEN:
        return f"Tag '{tag}' exceeds {_TAG_MAX_LEN} characters"
    if not _TAG_RE.match(tag):
        return f"Tag '{tag}' must be lowercase kebab-case (alphanumeric and hyphens)"
    return None


def _parse_tags_from_heading_rest(heading_rest: str) -> list[str]:
    """Extract tags from the heading_rest field.

    heading_rest examples:
      "plan" → []
      "plan | tags: git, worktree" → ["git", "worktree"]
      "plan | tags: git, worktree | Issue #7" → ["git", "worktree"]
    """
    parts = [p.strip() for p in heading_rest.split("|")]
    for part in parts:
        if part.startswith("tags:"):
            raw_tags = part[5:].strip()
            if not raw_tags:
                return []
            return [t.strip() for t in raw_tags.split(",") if t.strip()]
    return []


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

            tags = _parse_tags_from_heading_rest(heading_rest)
            entries.append(
                ParsedEntry(
                    entry_id=entry_id,
                    date=date,
                    heading_rest=heading_rest,
                    tags=tags,
                    content=content_text,
                    raw=raw_block,
                )
            )
        else:
            i += 1
    return entries


def find_entry_id(knowledge_path: Path, entry_id: str) -> bool:
    """Check whether an entry ID exists in the knowledge file."""
    if not knowledge_path.is_file():
        return False
    text = knowledge_path.read_text(encoding="utf-8")
    entries = parse_entries(text)
    return any(e.entry_id == entry_id for e in entries)


def read_ratings(ratings_path: Path) -> dict[str, EntryRating]:
    """Load the sidecar ratings YAML file.

    Returns an empty dict if the file doesn't exist.
    Acquires a shared read lock to prevent observing a partial write.
    """
    if not ratings_path.exists():
        return {}
    if ratings_path.is_dir():
        raise ValueError(f"Ratings path {ratings_path!s} points to a directory, not a file")
    fd = ratings_path.open("r", encoding="utf-8")
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        content = fd.read()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    if not content.strip():
        return {}
    data: Any = yaml.safe_load(content)
    if not isinstance(data, dict):
        return {}
    return {k: EntryRating(**v) if isinstance(v, dict) else EntryRating() for k, v in data.items()}


def _read_modify_write_ratings(
    ratings_path: Path,
    modify_fn: Callable[[dict[str, EntryRating]], None],
) -> dict[str, EntryRating]:
    """Read-modify-write the ratings file under an exclusive lock.

    ``modify_fn`` receives the current ratings dict and mutates it in place.
    """
    ratings_path.parent.mkdir(parents=True, exist_ok=True)
    if ratings_path.exists() and ratings_path.is_dir():
        raise ValueError(f"Ratings path {ratings_path!s} points to a directory, not a file")
    # Open for read+write, creating if needed
    with ratings_path.open("a+", encoding="utf-8") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            fd.seek(0)
            content = fd.read()
            data: dict[str, EntryRating] = {}
            if content.strip():
                loaded: Any = yaml.safe_load(content)
                if isinstance(loaded, dict):
                    data = {
                        k: EntryRating(**v) if isinstance(v, dict) else EntryRating()
                        for k, v in loaded.items()
                    }

            modify_fn(data)

            fd.seek(0)
            fd.truncate()
            raw = {k: v.model_dump(exclude_none=True) for k, v in data.items()}
            yaml.safe_dump(raw, fd, default_flow_style=False, sort_keys=True)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
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

    def _modify(data: dict[str, EntryRating]) -> None:
        entry = data.setdefault(entry_id, EntryRating())
        if direction == "up":
            entry.up += 1
        else:
            entry.down += 1

    _read_modify_write_ratings(ratings_path, _modify)


def record_supersede(
    ratings_path: Path,
    old_id: str,
    new_id: str,
) -> None:
    """Record a supersedes link: old_id is superseded by new_id."""

    def _modify(data: dict[str, EntryRating]) -> None:
        entry = data.setdefault(old_id, EntryRating())
        entry.superseded_by = new_id

    _read_modify_write_ratings(ratings_path, _modify)


def compute_auto_filter_threshold(
    entries: list[ParsedEntry],
    ratings: dict[str, EntryRating],
) -> float | None:
    """Compute statistical auto-filter threshold.

    Returns None if there isn't enough data (fewer than 3 entries with >= 5 votes).
    """
    # Collect net scores for entries with >= 5 total votes
    qualifying_scores: list[float] = []
    for entry in entries:
        if not entry.entry_id:
            continue
        r = ratings.get(entry.entry_id)
        if not r:
            continue
        total_votes = r.up + r.down
        if total_votes >= 5:
            qualifying_scores.append(float(r.up - r.down))

    if len(qualifying_scores) < 3:
        return None

    mean = statistics.mean(qualifying_scores)
    if len(qualifying_scores) < 2:
        return mean  # pragma: no cover — already checked >= 3
    stdev = statistics.stdev(qualifying_scores)

    # p10: 10th percentile
    sorted_scores = sorted(qualifying_scores)
    p10_idx = math.ceil(len(sorted_scores) * 0.1) - 1
    p10_idx = max(0, p10_idx)
    p10 = sorted_scores[p10_idx]

    return max(p10, mean - 2 * stdev)


def get_annotated_knowledge(
    project_root: Path,
    config: KnowledgeConfig,
    min_score: int | None = None,
    search_query: str | None = None,
    filter_tags: list[str] | None = None,
    no_filter: bool = False,
) -> str | None:
    """Read knowledge file, annotate headings with scores, and optionally filter.

    Filtering modes (mutually exclusive, checked in order):
    - ``no_filter=True``: no score filtering at all
    - ``min_score`` set: hard cutoff on net score
    - default: statistical auto-filter (prunes low-rated entries with sufficient votes)

    ``search_query`` and ``filter_tags`` combine with OR: an entry passes if it
    matches the search OR has any of the requested tags.

    Returns None if the knowledge file does not exist.
    """
    from wade.services.knowledge_search import evaluate_query, parse_query

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

    # Pre-parse search query
    parsed_query = parse_query(search_query) if search_query else None

    # Compute auto-filter threshold if using default filtering
    auto_threshold: float | None = None
    if min_score is None and not no_filter:
        auto_threshold = compute_auto_filter_threshold(entries, ratings)

    # Build the header (everything before the first entry)
    first_entry_pos = text.find(entries[0].raw)
    header = text[:first_entry_pos] if first_entry_pos > 0 else ""

    result_parts = [header]
    for entry in entries:
        entry_rating = ratings.get(entry.entry_id) if entry.entry_id else None
        up = entry_rating.up if entry_rating else 0
        down = entry_rating.down if entry_rating else 0
        net_score = up - down
        total_votes = up + down
        should_annotate = entry.entry_id is not None

        # Score filtering
        if not no_filter:
            if min_score is not None:
                # Hard cutoff mode
                if net_score < min_score:
                    continue
            elif (
                auto_threshold is not None
                and entry.entry_id is not None
                and total_votes >= 5
                and net_score < auto_threshold
            ):
                continue

        # Search/tag filtering (OR semantics)
        if parsed_query is not None or filter_tags:
            matches_search = False
            matches_tag = False
            if parsed_query is not None:
                searchable = entry.raw.split("\n")[0] + "\n" + entry.content
                matches_search = evaluate_query(parsed_query, searchable)
            if filter_tags:
                matches_tag = bool(set(entry.tags) & set(filter_tags))
            if not matches_search and not matches_tag:
                continue

        # Re-build the heading with score annotation
        heading_match = _ENTRY_HEADING_RE.match(entry.raw.split("\n")[0])
        if heading_match and should_annotate:
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
    tags: list[str] | None = None,
) -> KnowledgeEntry:
    """Format and append a knowledge entry to the knowledge file.

    Returns a KnowledgeEntry with the path and generated entry ID.
    """
    if tags:
        for tag in tags:
            err = validate_tag(tag)
            if err:
                raise ValueError(err)

    path = ensure_knowledge_file(project_root, config)

    existing_ids = {
        parsed.entry_id
        for parsed in parse_entries(path.read_text(encoding="utf-8"))
        if parsed.entry_id is not None
    }
    entry_id = _generate_entry_id()
    while entry_id in existing_ids:
        entry_id = _generate_entry_id()
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    tags_part = f" | tags: {', '.join(tags)}" if tags else ""
    issue_part = f" | Issue #{issue_ref}" if issue_ref else ""
    header = f"## {entry_id} | {timestamp} | {session_type}{tags_part}{issue_part}"

    entry = f"\n{header}\n\n{content.strip()}\n\n---\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)

    return KnowledgeEntry(path=path, entry_id=entry_id)


def _rebuild_heading_line(
    entry_id: str | None,
    date: str,
    session_type: str,
    tags: list[str],
    issue_part: str,
) -> str:
    """Reconstruct a heading line from its components."""
    id_part = f"{entry_id} | " if entry_id else ""
    tags_part = f" | tags: {', '.join(tags)}" if tags else ""
    issue_str = f" | {issue_part}" if issue_part else ""
    return f"## {id_part}{date} | {session_type}{tags_part}{issue_str}"


def _decompose_heading_rest(heading_rest: str) -> tuple[str, list[str], str]:
    """Split heading_rest into (session_type, tags, issue_part).

    Returns the session type, list of tags, and the issue part (e.g. "Issue #7")
    or empty string if no issue.
    """
    parts = [p.strip() for p in heading_rest.split("|")]
    session_type = parts[0]
    tags: list[str] = []
    issue_part = ""
    for part in parts[1:]:
        if part.startswith("tags:"):
            raw_tags = part[5:].strip()
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif part.startswith("Issue"):
            issue_part = part
    return session_type, tags, issue_part


def add_tag_to_entry(
    knowledge_path: Path,
    entry_id: str,
    tag: str,
) -> None:
    """Add a tag to an existing entry's heading (in-place file edit with locking)."""
    err = validate_tag(tag)
    if err:
        raise ValueError(err)

    with knowledge_path.open("r+", encoding="utf-8") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            content = fd.read()
            entries = parse_entries(content)
            target = next((e for e in entries if e.entry_id == entry_id), None)
            if target is None:
                raise ValueError(f"Entry ID '{entry_id}' not found")

            if tag in target.tags:
                return  # Already has the tag

            session_type, tags, issue_part = _decompose_heading_rest(target.heading_rest)
            tags.append(tag)
            new_heading = _rebuild_heading_line(
                entry_id, target.date, session_type, tags, issue_part
            )

            old_heading_line = target.raw.split("\n")[0]
            content = content.replace(old_heading_line, new_heading, 1)

            fd.seek(0)
            fd.truncate()
            fd.write(content)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def remove_tag_from_entry(
    knowledge_path: Path,
    entry_id: str,
    tag: str,
) -> None:
    """Remove a tag from an existing entry's heading (in-place file edit with locking)."""
    with knowledge_path.open("r+", encoding="utf-8") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            content = fd.read()
            entries = parse_entries(content)
            target = next((e for e in entries if e.entry_id == entry_id), None)
            if target is None:
                raise ValueError(f"Entry ID '{entry_id}' not found")

            if tag not in target.tags:
                raise ValueError(f"Tag '{tag}' not found on entry {entry_id}")

            session_type, tags, issue_part = _decompose_heading_rest(target.heading_rest)
            tags.remove(tag)
            new_heading = _rebuild_heading_line(
                entry_id, target.date, session_type, tags, issue_part
            )

            old_heading_line = target.raw.split("\n")[0]
            content = content.replace(old_heading_line, new_heading, 1)

            fd.seek(0)
            fd.truncate()
            fd.write(content)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def list_tags(
    knowledge_path: Path,
    entry_id: str | None = None,
) -> list[str] | dict[str, list[str]]:
    """List tags for a specific entry, or all unique tags across the knowledge file."""
    if not knowledge_path.is_file():
        if entry_id:
            raise ValueError(f"Knowledge file not found: {knowledge_path}")
        return []

    text = knowledge_path.read_text(encoding="utf-8")
    entries = parse_entries(text)

    if entry_id:
        target = next((e for e in entries if e.entry_id == entry_id), None)
        if target is None:
            raise ValueError(f"Entry ID '{entry_id}' not found")
        return target.tags

    all_tags: set[str] = set()
    for entry in entries:
        all_tags.update(entry.tags)
    return sorted(all_tags)


def enable_knowledge(project_root: Path, path: str | None = None) -> None:
    """Enable knowledge capture and optionally set a custom knowledge file path.

    Sets ``knowledge.enabled: true`` in .wade.yml, optionally sets ``knowledge.path``,
    and creates the knowledge file if it doesn't exist.

    Args:
        project_root: Root directory of the project (where .wade.yml is located).
        path: Optional custom path for the knowledge file (relative to project root).
              If provided, validates that it's a safe relative path.

    Raises:
        FileNotFoundError: If .wade.yml doesn't exist.
        ValueError: If the provided path is invalid (absolute or contains `..`).
    """
    from wade.config.loader import find_config_file

    config_path = find_config_file(project_root)
    if config_path is None:
        raise FileNotFoundError(".wade.yml not found — project not initialized")

    # Load current config
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {config_path}: {e}") from e

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Config must be a YAML mapping")

    # Validate and set the path if provided
    if path is not None:
        # Validate path using existing function
        temp_config = KnowledgeConfig(enabled=True, path=path)
        resolve_knowledge_path(project_root, temp_config)

    # Update knowledge section
    knowledge_dict = raw.get("knowledge", {}) or {}
    if not isinstance(knowledge_dict, dict):
        knowledge_dict = {}

    knowledge_dict["enabled"] = True
    if path is not None:
        knowledge_dict["path"] = path
    elif "path" not in knowledge_dict:
        # Set default path if not already configured
        knowledge_dict["path"] = "KNOWLEDGE.md"
    raw["knowledge"] = knowledge_dict

    # Write updated config
    config_path.write_text(
        yaml.safe_dump(raw, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    # Create knowledge file if it doesn't exist
    knowledge_path_str = knowledge_dict.get("path", "KNOWLEDGE.md")
    knowledge_config = KnowledgeConfig(enabled=True, path=knowledge_path_str)
    ensure_knowledge_file(project_root, knowledge_config)


def disable_knowledge(project_root: Path) -> None:
    """Disable knowledge capture.

    Sets ``knowledge.enabled: false`` in .wade.yml. Does not delete the knowledge file.

    Args:
        project_root: Root directory of the project (where .wade.yml is located).

    Raises:
        FileNotFoundError: If .wade.yml doesn't exist.
        ValueError: If config is invalid.
    """
    from wade.config.loader import find_config_file

    config_path = find_config_file(project_root)
    if config_path is None:
        raise FileNotFoundError(".wade.yml not found — project not initialized")

    # Load current config
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {config_path}: {e}") from e

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Config must be a YAML mapping")

    # Update knowledge section
    knowledge_dict = raw.get("knowledge", {}) or {}
    if not isinstance(knowledge_dict, dict):
        knowledge_dict = {}

    knowledge_dict["enabled"] = False
    raw["knowledge"] = knowledge_dict

    # Write updated config
    config_path.write_text(
        yaml.safe_dump(raw, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
