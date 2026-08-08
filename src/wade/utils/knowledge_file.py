"""Pure knowledge-file helpers — path resolution, parsing, and structural validation.

Extracted from ``knowledge_service`` (#358 review) so lower-level callers — e.g. the
``done`` gate in ``implementation_service`` and worktree ``bootstrap`` — can resolve
and validate a knowledge file without importing a sibling *service* module. This is a
**leaf**: it imports only stdlib, ``pydantic``, and ``models.config``. ``knowledge_service``
re-exports these names, so its public surface is unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from wade.models.config import KnowledgeConfig

# Regex to match entry headings: ## <id> | <date> | <rest> [+N/-M]
# Also matches old-style headings without IDs: ## <date> | <rest>
# ID can be 8-char hex (legacy), alphanumeric with hyphens and underscores, or absent.
_ENTRY_HEADING_RE = re.compile(
    r"^## (?:([a-zA-Z0-9_-]+) \| )?(\d{4}-\d{2}-\d{2}) \| (.+?)(?:\s+\[.*\])?\s*$"
)

# Fallback regex for hand-authored plain headings with no date or ID: ## Title
# Title must start with alphanumeric to avoid matching `## ---` separators.
_PLAIN_ENTRY_HEADING_RE = re.compile(r"^## ([A-Za-z0-9].*?)(?:\s+\[.*\])?\s*$")

# Matches a line that opens a VCS conflict hunk. ``merge=union`` never emits these,
# but a non-union merge of the knowledge file might — cheap to catch here. Anchored to
# the *exact* marker form git writes (``<``/``>`` exactly seven times, optionally
# followed by a space and a label; ``=`` exactly seven times alone) so a decorative
# body separator like ``====================`` is NOT a false positive.
_CONFLICT_MARKER_RE = re.compile(r"^(?:<{7}|>{7})(?: .*)?$|^={7}$", re.MULTILINE)


class ParsedEntry(BaseModel, frozen=True):
    """A parsed knowledge entry from the knowledge file."""

    entry_id: str | None
    date: str | None
    heading_rest: str
    tags: list[str] = []
    content: str
    raw: str


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
    """Derive the append-only vote-log path from the knowledge file path.

    ``KNOWLEDGE.md`` → ``KNOWLEDGE.ratings.jsonl`` (#358). The counter-based
    ``.ratings.yml`` sidecar is superseded by an append-only JSONL vote log so
    concurrent branches never lose a vote at merge time.
    """
    return knowledge_path.with_suffix(".ratings.jsonl")


def parse_entries(text: str) -> list[ParsedEntry]:
    """Parse knowledge file text into individual entries.

    Handles entries with and without IDs. Skips the template header.
    Also handles plain ## Title headings with no date or ID.
    """
    entries: list[ParsedEntry] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        match = _ENTRY_HEADING_RE.match(lines[i])
        plain_match = _PLAIN_ENTRY_HEADING_RE.match(lines[i]) if not match else None

        if match or plain_match:
            if match:
                entry_id: str | None = match.group(1)
                date: str | None = match.group(2)
                heading_rest = match.group(3)
            else:
                assert plain_match is not None
                entry_id = None
                date = None
                heading_rest = plain_match.group(1)
            heading_line = lines[i]

            # Collect content lines until next heading or end
            content_lines: list[str] = []
            i += 1
            while i < len(lines):
                if _ENTRY_HEADING_RE.match(lines[i]) or _PLAIN_ENTRY_HEADING_RE.match(lines[i]):
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


def validate_knowledge_file(path: Path) -> list[str]:
    """Structurally validate a knowledge file — return a list of problems (empty ⇒ OK).

    ``merge=union`` keeps both sides of a conflict with no structural awareness, so
    a rewrite-in-place edit (``tag add/remove`` or a supersede-in-heading) diverging
    from an append elsewhere can silently leave **two copies of one entry heading**
    — a malformed ``KNOWLEDGE.md`` that merges cleanly and would otherwise ship
    undetected. This guard runs where such a file would land (the ``done`` gate /
    pre-push) so it cannot reach main.

    Checks:
      - **duplicate entry IDs** — the union double-heading signal (two copies of one
        heading share its id); this is the reliable structural-corruption detector;
      - **unresolved conflict markers** — a defensive backstop for a non-union merge.

    Deliberately does **not** flag "malformed headings" by re-scanning raw lines:
    ``parse_entries`` accepts any ``## <alnum>…`` line as a plain heading, so a
    heading-shaped line quoted inside an entry *body* is legitimate — flagging it
    would be a false positive that blocks an otherwise-valid PR at ``done``.
    """
    problems: list[str] = []
    if not path.is_file():
        return problems
    text = path.read_text(encoding="utf-8")

    entries = parse_entries(text)
    counts: dict[str, int] = {}
    for entry in entries:
        if entry.entry_id is not None:
            counts[entry.entry_id] = counts.get(entry.entry_id, 0) + 1
    for entry_id, count in sorted(counts.items()):
        if count > 1:
            problems.append(f"Duplicate entry ID '{entry_id}' appears {count} times")

    if _CONFLICT_MARKER_RE.search(text):
        problems.append("Unresolved VCS conflict markers present")

    return problems
