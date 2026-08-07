"""Knowledge service — append and manage project knowledge entries."""

from __future__ import annotations

import json
import math
import re
import statistics
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel

from wade.models.config import KnowledgeConfig
from wade.utils.filelock import file_lock

logger = structlog.get_logger()

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

# Fallback regex for hand-authored plain headings with no date or ID: ## Title
# Title must start with alphanumeric to avoid matching `## ---` separators.
_PLAIN_ENTRY_HEADING_RE = re.compile(r"^## ([A-Za-z0-9].*?)(?:\s+\[.*\])?\s*$")

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
    date: str | None
    heading_rest: str
    tags: list[str] = []
    content: str
    raw: str


class AnnotatedKnowledgeResult(BaseModel, frozen=True):
    """Result of get_annotated_knowledge with match information."""

    content: str | None
    entries_count: int


class EntryRating(BaseModel):
    """Rating data for a single knowledge entry."""

    up: int = 0
    down: int = 0
    stale: int = 0
    superseded_by: str | None = None


class KnowledgeStatus(BaseModel, frozen=True):
    """Uncommitted state of the knowledge files on the resolved root.

    ``dirty_paths`` holds ``git status --porcelain`` lines **scoped to only the
    knowledge and ratings paths** — never the whole checkout, so unrelated dirt is
    not mislabeled "knowledge state". ``legacy_migration_pending`` is True when a
    pre-#358 ``.ratings.yml`` is still on disk with no ``.ratings.jsonl`` yet (it
    converts on the next ratings write).
    """

    root: Path
    dirty_paths: list[str] = []
    legacy_migration_pending: bool = False


def _generate_entry_id() -> str:
    """Generate a short entry ID (first 12 hex chars of uuid4).

    Widened from 8 to 12 hex chars (32→48 bits) for #358: knowledge files are now
    worktree-local, so the ID-uniqueness read+append lock only guards writers
    *within one worktree*; cross-worktree uniqueness rests on ``uuid4`` not
    colliding. 48 bits keeps that collision probability negligible. Legacy 8-char
    IDs keep working — ``_ENTRY_HEADING_RE`` matches variable-length IDs.
    """
    return uuid.uuid4().hex[:12]


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


def _canonical_project_root(project_root: Path) -> Path:
    """Return main worktree path if project_root is inside a linked worktree.

    Falls back to project_root on expected operational failures (ImportError,
    GitError, OSError). Unexpected exceptions propagate to the caller.
    """
    try:
        from wade.git.repo import GitError, get_main_worktree_path
    except ImportError:
        return project_root
    try:
        main = get_main_worktree_path(project_root)
        if main is not None and main != project_root:
            return main
    except (GitError, OSError):
        pass
    return project_root


def _resolve_knowledge_root(project_root: Path) -> Path:
    """Resolve where knowledge reads/writes land, keyed on the HEAD-attachment state.

    Knowledge is worktree-local (#358): the file is tracked, so a branch-backed
    worktree edits its *own* checkout and the change rides in the PR. Only a
    **throwaway detached-HEAD worktree** (a ``wade plan`` or ``wade task deps``
    session — both created via ``create_detached_worktree``) is redirected to the
    main checkout, because such a worktree is deleted at session end and any write
    there would be lost.

    - ``is_head_attached(project_root)`` True (branch-backed worktree *or* the main
      checkout) → return *project_root* unchanged: edit the file you stand in.
    - detached HEAD (plan / task deps throwaway) → redirect to the main checkout.

    Falls back to *project_root* if the git state can't be determined.
    """
    try:
        from wade.git.repo import is_head_attached
    except ImportError:
        return project_root
    try:
        if is_head_attached(project_root):
            return project_root
    except OSError:
        return project_root
    return _canonical_project_root(project_root)


def resolve_canonical_knowledge_path(project_root: Path, config: KnowledgeConfig) -> Path:
    """Resolve the knowledge path for the current session's resolved root.

    In a branch-backed worktree (or the main checkout) this is the local file; in a
    throwaway detached-HEAD (plan / task deps) worktree it redirects to main. See
    :func:`_resolve_knowledge_root`.
    """
    return resolve_knowledge_path(_resolve_knowledge_root(project_root), config)


def resolve_ratings_path(knowledge_path: Path) -> Path:
    """Derive the append-only vote-log path from the knowledge file path.

    ``KNOWLEDGE.md`` → ``KNOWLEDGE.ratings.jsonl`` (#358). The counter-based
    ``.ratings.yml`` sidecar is superseded by an append-only JSONL vote log so
    concurrent branches never lose a vote at merge time.
    """
    return knowledge_path.with_suffix(".ratings.jsonl")


def _legacy_ratings_path(ratings_path: Path) -> Path:
    """Derive the legacy counter-YAML path from the JSONL vote-log path.

    ``KNOWLEDGE.ratings.jsonl`` → ``KNOWLEDGE.ratings.yml``. Used only to fold a
    pre-#358 sidecar into the same scores on read (in memory) and to materialize it
    to JSONL on the first ratings write.
    """
    return ratings_path.with_suffix(".yml")


def knowledge_status(project_root: Path, config: KnowledgeConfig) -> KnowledgeStatus:
    """Report uncommitted knowledge/ratings changes on the resolved root.

    Resolves the root the same way reads/writes do (:func:`_resolve_knowledge_root`),
    then scopes ``git status --porcelain`` to **only** the knowledge file and its
    ratings siblings, so unrelated working-tree dirt is never reported as knowledge
    state. Surfaces pending throwaway-session votes (in the main checkout) and any
    legacy ``.ratings.yml`` still awaiting on-disk migration.
    """
    from wade.git import repo as git_repo

    root = _resolve_knowledge_root(project_root)
    knowledge_path = resolve_knowledge_path(root, config)
    ratings_path = resolve_ratings_path(knowledge_path)
    legacy_path = _legacy_ratings_path(ratings_path)
    legacy_pending = legacy_path.is_file() and not ratings_path.exists()

    dirty = git_repo.status_porcelain_paths(
        root, str(knowledge_path), str(ratings_path), str(legacy_path)
    )

    return KnowledgeStatus(root=root, dirty_paths=dirty, legacy_migration_pending=legacy_pending)


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


def find_entry_id(knowledge_path: Path, entry_id: str) -> bool:
    """Check whether an entry ID exists in the knowledge file."""
    if not knowledge_path.is_file():
        return False
    text = knowledge_path.read_text(encoding="utf-8")
    entries = parse_entries(text)
    return any(e.entry_id == entry_id for e in entries)


# Matches a line that opens a VCS conflict hunk. ``merge=union`` never emits these,
# but a non-union merge of the knowledge file might — cheap to catch here.
_CONFLICT_MARKER_RE = re.compile(r"^(<{7}|={7}|>{7})", re.MULTILINE)


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


def _read_legacy_yaml_ratings(legacy_path: Path) -> dict[str, EntryRating]:
    """Fold a pre-#358 counter-YAML sidecar into ``dict[str, EntryRating]``.

    Pure read — never writes. Returns an empty dict on absent/empty/malformed
    content so a legacy file can never crash a read.
    """
    if not legacy_path.is_file():
        return {}
    content = legacy_path.read_text(encoding="utf-8")
    if not content.strip():
        return {}
    data: Any = yaml.safe_load(content)
    if not isinstance(data, dict):
        return {}
    return {k: EntryRating(**v) if isinstance(v, dict) else EntryRating() for k, v in data.items()}


def _fold_jsonl_ratings(ratings_path: Path) -> dict[str, EntryRating]:
    """Reduce the append-only JSONL vote log into per-entry rating totals.

    Fold rule per record (skipping any malformed line defensively — a union merge
    can concatenate imperfectly, and one bad line must never crash a read):

    - ``dir`` (``up``/``down``/``stale``) → +1 to that counter.
    - a seed record (``seed: true``) → add its integer ``up``/``down``/``stale``
      fields **once per entry id**. Seeds are byte-deterministic and idempotent, so
      a duplicate seed line (e.g. produced when two branches migrate the same
      legacy ``.yml`` and union-merge) is folded exactly once — never double-counted.
    - ``superseded_by`` → set the link (last-seen wins).
    """
    data: dict[str, EntryRating] = {}
    seeded: set[str] = set()
    for raw_line in ratings_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record: Any = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            logger.debug("knowledge.ratings_malformed_line", line=line)
            continue
        if not isinstance(record, dict):
            continue
        entry_id = record.get("id")
        if not isinstance(entry_id, str):
            continue
        entry = data.setdefault(entry_id, EntryRating())
        if record.get("seed") is True:
            if entry_id in seeded:
                continue  # idempotent — dedupe duplicate seed lines from union merges
            seeded.add(entry_id)
            for counter in ("up", "down", "stale"):
                val = record.get(counter)
                if isinstance(val, int) and not isinstance(val, bool):
                    setattr(entry, counter, getattr(entry, counter) + val)
            sb = record.get("superseded_by")
            if isinstance(sb, str):
                entry.superseded_by = sb
            continue
        direction = record.get("dir")
        if direction in ("up", "down", "stale"):
            setattr(entry, direction, getattr(entry, direction) + 1)
        sb = record.get("superseded_by")
        if isinstance(sb, str):
            entry.superseded_by = sb
    return data


def read_ratings(ratings_path: Path) -> dict[str, EntryRating]:
    """Fold the append-only vote log into per-entry rating totals.

    Reads are **pure** — they never write. Resolution order:

    1. ``.ratings.jsonl`` present → fold every line (see :func:`_fold_jsonl_ratings`).
    2. Only a legacy ``.ratings.yml`` present → fold its counters **in memory** and
       return them without writing anything. The on-disk ``.yml``→``.jsonl``
       conversion is a write-path concern (:func:`_materialize_migration_locked`),
       so a ``get``/``status`` in the main checkout can never dirty main.
    3. Neither present → empty dict.

    Return type (``dict[str, EntryRating]``) is unchanged, so
    ``compute_auto_filter_threshold`` and ``get_annotated_knowledge`` consume it as
    before.
    """
    if ratings_path.is_dir():
        raise ValueError(f"Ratings path {ratings_path!s} points to a directory, not a file")
    if ratings_path.exists():
        # .jsonl wins over a still-present legacy .yml, never folding both. The
        # write-path migration git-rm's the .yml when it seeds the .jsonl, so a
        # both-present state is only a transient/botched migration commit — and there
        # the .jsonl is authoritative (it already carries the .yml's counts as a
        # seed). Folding both would double-count that seed.
        return _fold_jsonl_ratings(ratings_path)
    return _read_legacy_yaml_ratings(_legacy_ratings_path(ratings_path))


def _git_rm(path: Path) -> None:
    """Stage the removal of *path* (``git rm``), falling back to a plain unlink.

    Used when converting a legacy ``.ratings.yml`` to JSONL: staging the delete
    (rather than renaming to ``.migrated``) makes two branches that migrate the
    same diverged ``.yml`` merge cleanly — delete/delete plus a byte-identical
    add/add ``.jsonl`` has no conflict, whereas a ``.migrated`` file would fall
    outside the ``merge=union`` block. Best-effort: outside a git repo (tests) or
    for an untracked file, just remove it from disk.
    """
    from wade.git import repo as git_repo

    if git_repo.rm_file(path.parent, path.name):
        return
    path.unlink(missing_ok=True)


def _materialize_migration_locked(ratings_path: Path) -> None:
    """Convert a legacy ``.ratings.yml`` to a seeded ``.ratings.jsonl`` on first write.

    Caller must hold ``file_lock(ratings_path)``. No-op when ``.jsonl`` already
    exists or no legacy ``.yml`` is present. The seed block is **byte-deterministic**
    — one record per entry, sorted by id, ``json.dumps(sort_keys=True)``, and
    crucially **no ``ts``/wall-clock field** — so two branches migrating the same
    legacy file independently emit identical bytes that union-merge as a no-op. The
    legacy ``.yml`` is then ``git rm``'d so the conversion rides in the branch.
    """
    if ratings_path.exists():
        return
    legacy = _legacy_ratings_path(ratings_path)
    if not legacy.is_file():
        return
    ratings = _read_legacy_yaml_ratings(legacy)
    seed_lines = [
        json.dumps(
            {
                "id": entry_id,
                "seed": True,
                "up": r.up,
                "down": r.down,
                "stale": r.stale,
                "superseded_by": r.superseded_by,
            },
            sort_keys=True,
        )
        for entry_id, r in sorted(ratings.items())
    ]
    ratings_path.write_text(
        "".join(f"{line}\n" for line in seed_lines),
        encoding="utf-8",
    )
    _git_rm(legacy)


def _append_ratings_record(ratings_path: Path, record: dict[str, Any]) -> None:
    """Append one JSON record as a single line, migrating a legacy ``.yml`` first.

    Wrapped in ``file_lock`` so the migration check + append is atomic against
    concurrent writers in the same worktree. The append itself is a lone
    ``O_APPEND`` line write — merging the log is pure concatenation, so no vote is
    ever lost regardless of merge order or parallelism.
    """
    with file_lock(ratings_path):
        if ratings_path.exists() and ratings_path.is_dir():
            raise ValueError(f"Ratings path {ratings_path!s} points to a directory, not a file")
        _materialize_migration_locked(ratings_path)
        line = json.dumps(record, sort_keys=True)
        with ratings_path.open("a", encoding="utf-8") as fd:
            fd.write(f"{line}\n")


def record_rating(
    ratings_path: Path,
    entry_id: str,
    direction: str,
) -> None:
    """Append an up/down/stale vote for an entry to the JSONL vote log.

    ``direction`` must be ``"up"``, ``"down"``, or ``"stale"``. A ``ts`` is stamped
    on the record — each vote is a distinct event (not a re-derivation), so votes
    are always distinct lines and both survive a union merge.
    """
    if direction not in ("up", "down", "stale"):
        raise ValueError(f"Invalid direction {direction!r}: must be 'up', 'down', or 'stale'")
    record: dict[str, Any] = {
        "dir": direction,
        "id": entry_id,
        "ts": datetime.now(tz=UTC).isoformat(),
    }
    _append_ratings_record(ratings_path, record)


def record_supersede(
    ratings_path: Path,
    old_id: str,
    new_id: str,
) -> None:
    """Append a supersede link (``old_id`` is superseded by ``new_id``) to the log."""
    _append_ratings_record(
        ratings_path,
        {"id": old_id, "superseded_by": new_id, "ts": datetime.now(tz=UTC).isoformat()},
    )


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

    # p10: 10th percentile.  math.ceil ensures we round up to the nearest
    # integer index, then -1 converts to 0-based.  For small samples (<=10
    # entries) this yields index 0 (the minimum) — intentionally conservative
    # so we don't over-filter when data is scarce.  max(0, ...) guards against
    # negative indices that could arise from floating-point edge cases.
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
) -> AnnotatedKnowledgeResult:
    """Read knowledge file, annotate headings with scores, and optionally filter.

    Filtering modes (mutually exclusive, checked in order):
    - ``no_filter=True``: no score filtering at all
    - ``min_score`` set: hard cutoff on net score
    - default: statistical auto-filter (prunes low-rated entries with sufficient votes)

    ``search_query`` and ``filter_tags`` combine with OR: an entry passes if it
    matches the search OR has any of the requested tags.

    Returns an AnnotatedKnowledgeResult with content=None if the knowledge file
    does not exist, and entries_count=0 if filters returned no results.
    """
    from wade.services.knowledge_search import evaluate_query, parse_query

    project_root = _resolve_knowledge_root(project_root)
    path = resolve_knowledge_path(project_root, config)
    if not path.exists():
        return AnnotatedKnowledgeResult(content=None, entries_count=0)
    if path.is_dir():
        raise ValueError(f"Knowledge path {config.path!r} points to a directory, not a file")

    text = path.read_text(encoding="utf-8")
    entries = parse_entries(text)

    if not entries and search_query is None and not filter_tags:
        return AnnotatedKnowledgeResult(content=text, entries_count=0)
    # Fall through to return header only (when filters are specified but no entries)

    if entries:
        ratings_path = resolve_ratings_path(path)
        ratings = read_ratings(ratings_path)
    else:
        ratings = {}

    # Pre-parse search query
    parsed_query = parse_query(search_query) if search_query else None

    # Compute auto-filter threshold if using default filtering
    auto_threshold: float | None = None
    if min_score is None and not no_filter and entries:
        auto_threshold = compute_auto_filter_threshold(entries, ratings)

    # Build the header (everything before the first entry)
    if entries:
        first_entry_pos = text.find(entries[0].raw)
        header = text[:first_entry_pos] if first_entry_pos > 0 else ""
    else:
        m = re.search(r"^##\s", text, re.MULTILINE)
        header = text[: m.start()] if m else text

    result_parts = [header]
    filtered_entry_count = 0
    for entry in entries:
        entry_rating = ratings.get(entry.entry_id) if entry.entry_id else None
        up = entry_rating.up if entry_rating else 0
        down = entry_rating.down if entry_rating else 0
        stale = entry_rating.stale if entry_rating else 0
        net_score = up - down
        total_votes = up + down
        should_annotate = entry.entry_id is not None

        # Score and stale filtering
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
            # Stale threshold filter (independent of score filter)
            if entry.entry_id is not None and stale >= 2:
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
            assert entry.date is not None  # entry_id is always accompanied by a date
            stale_part = f"/stale:{stale}" if stale > 0 else ""
            heading = f"## {id_part}{entry.date} | {entry.heading_rest} [+{up}/-{down}{stale_part}]"
            raw_lines = entry.raw.split("\n")
            raw_lines[0] = heading
            result_parts.append("\n".join(raw_lines))
        else:
            result_parts.append(entry.raw)
        filtered_entry_count += 1

    output = "".join(result_parts)
    if not output.endswith("\n"):
        output += "\n"
    return AnnotatedKnowledgeResult(content=output, entries_count=filtered_entry_count)


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
    project_root = _resolve_knowledge_root(project_root)
    if tags:
        for tag in tags:
            err = validate_tag(tag)
            if err:
                raise ValueError(err)

    path = ensure_knowledge_file(project_root, config)

    # Lock the uniqueness read + append as one unit. With worktree-local files the
    # lock only guards writers within this worktree; cross-worktree uniqueness
    # rests on the widened 48-bit id (see ``_generate_entry_id``). Without the lock,
    # two concurrent adds could each read the same existing-id set and generate
    # colliding ids before either append lands.
    with file_lock(path):
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

    with file_lock(knowledge_path):
        content = knowledge_path.read_text(encoding="utf-8")
        entries = parse_entries(content)
        target = next((e for e in entries if e.entry_id == entry_id), None)
        if target is None:
            raise ValueError(f"Entry ID '{entry_id}' not found")

        if tag in target.tags:
            return  # Already has the tag

        assert target.date is not None  # entries with entry_id always have a date
        session_type, tags, issue_part = _decompose_heading_rest(target.heading_rest)
        tags.append(tag)
        new_heading = _rebuild_heading_line(entry_id, target.date, session_type, tags, issue_part)

        old_heading_line = target.raw.split("\n")[0]
        entry_start = content.find(target.raw)
        if entry_start != -1:
            content = (
                content[:entry_start] + new_heading + content[entry_start + len(old_heading_line) :]
            )
        else:
            content = content.replace(old_heading_line, new_heading, 1)

        knowledge_path.write_text(content, encoding="utf-8")


def remove_tag_from_entry(
    knowledge_path: Path,
    entry_id: str,
    tag: str,
) -> None:
    """Remove a tag from an existing entry's heading (in-place file edit with locking)."""
    with file_lock(knowledge_path):
        content = knowledge_path.read_text(encoding="utf-8")
        entries = parse_entries(content)
        target = next((e for e in entries if e.entry_id == entry_id), None)
        if target is None:
            raise ValueError(f"Entry ID '{entry_id}' not found")

        if tag not in target.tags:
            raise ValueError(f"Tag '{tag}' not found on entry {entry_id}")

        assert target.date is not None  # entries with entry_id always have a date
        session_type, tags, issue_part = _decompose_heading_rest(target.heading_rest)
        tags.remove(tag)
        new_heading = _rebuild_heading_line(entry_id, target.date, session_type, tags, issue_part)

        old_heading_line = target.raw.split("\n")[0]
        entry_start = content.find(target.raw)
        if entry_start != -1:
            content = (
                content[:entry_start] + new_heading + content[entry_start + len(old_heading_line) :]
            )
        else:
            content = content.replace(old_heading_line, new_heading, 1)

        knowledge_path.write_text(content, encoding="utf-8")


def list_tags(
    knowledge_path: Path,
    entry_id: str | None = None,
) -> list[str]:
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
