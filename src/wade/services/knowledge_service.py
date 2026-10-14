"""Knowledge service — append and manage project knowledge entries."""

from __future__ import annotations

import json
import math
import os
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

# Pure knowledge-file helpers live in a leaf module (#358 review) so lower-level
# callers (the ``done`` gate, worktree bootstrap) don't import this service. Re-exported
# here (explicit ``as`` aliases) so ``knowledge_service``'s public surface is unchanged;
# ``validate_knowledge_file`` is imported from the leaf module directly by its callers.
from wade.utils.knowledge_file import ParsedEntry as ParsedEntry
from wade.utils.knowledge_file import parse_entries as parse_entries
from wade.utils.knowledge_file import resolve_knowledge_path as resolve_knowledge_path
from wade.utils.knowledge_file import resolve_ratings_path as resolve_ratings_path

logger = structlog.get_logger()

KNOWLEDGE_TEMPLATE = """\
# Project Knowledge

Shared learnings from AI planning and implementation sessions.
Search relevant entries during the task; verify committed proof.
Add new entries via `wade knowledge add`.

---
"""

# Tag validation: lowercase kebab-case, max 30 chars
_TAG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_TAG_MAX_LEN = 30

# Legacy transport path remains recognizable for explicit owner recovery. New
# observations persist directly in the ignored Git-common-dir proposal queue.
STAGED_RATINGS_RELATIVE_PATH = ".wade/knowledge-ratings-staged.jsonl"

# Compatibility identity for retained parent-created plan/deps worktrees. It
# never grants tracked-write authority or permission to deliver legacy records;
# new observations use the ignored common-directory owner-bound proposal queue.
THROWAWAY_SESSION_MARKER_RELATIVE_PATH = ".wade/throwaway-session"


class KnowledgeEntry(BaseModel, frozen=True):
    """Result of appending a knowledge entry."""

    path: Path
    entry_id: str


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

    ``staging_error`` carries the reason a detached session's staged-vote log
    could not be read. It is a distinct state from "no staged votes": the log
    exists but the handoff will fail, which is precisely the case a session must
    never be reported clean for (#462 review).
    """

    root: Path
    dirty_paths: list[str] = []
    legacy_migration_pending: bool = False
    pending_proposal_count: int = 0
    proposal_error: str | None = None
    staged_vote_count: int = 0
    staging_path: Path | None = None
    staging_error: str | None = None


class RatingEvent(BaseModel, frozen=True):
    """One durable append-only knowledge-rating event.

    The JSON record deliberately preserves the pre-#462 ``id`` / ``dir`` keys
    so older readers keep working. ``event_id`` is new and permits a staged
    event to be delivered more than once without changing the folded score.
    """

    event_id: str
    entry_id: str
    direction: str
    timestamp: str

    def to_record(self) -> dict[str, str]:
        return {
            "dir": self.direction,
            "event_id": self.event_id,
            "id": self.entry_id,
            "ts": self.timestamp,
        }


class StagedRatingsFlushResult(BaseModel, frozen=True):
    """Outcome of preserving/reconciling a legacy detached transport artifact.

    ``worktree`` names the session the votes came from, so a sweep over
    *retained* worktrees (:func:`flush_retained_staged_ratings`) can report
    which one each outcome belongs to.
    """

    success: bool
    staged_count: int = 0
    appended_count: int = 0
    message: str | None = None
    worktree: Path | None = None


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
    """Knowledge snapshots always belong to the checkout being observed."""
    return project_root


def throwaway_session_marker_path(worktree_path: Path) -> Path:
    """Return the marker path that authorizes vote staging in *worktree_path*."""
    return worktree_path / THROWAWAY_SESSION_MARKER_RELATIVE_PATH


def mark_throwaway_knowledge_session(worktree_path: Path) -> Path:
    """Identify a parent-created throwaway session for compatibility/recovery.

    Called by planning/dependency parents before launch. New observations persist
    directly in the common-directory queue; legacy logs require explicit verified
    owner recovery and are never delivered by a generic parent flush.

    Raises ``ValueError`` when the marker would land outside *worktree_path*: it
    lives under the same repo-controlled ``.wade/`` as the staging log, so a
    symlink there would make this ``mkdir`` + ``write_text`` an arbitrary write
    outside the throwaway worktree — and would leave the marker behind in
    whatever directory it pointed at. Both callers treat the raise as "no
    throwaway session", which is the safe outcome.
    """
    marker = throwaway_session_marker_path(worktree_path)
    if path_escapes_session(worktree_path, marker):
        raise ValueError(f"Refusing to mark a throwaway session outside the worktree: {marker!s}")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "wade throwaway session — observations use the owner-bound proposal queue; "
        "legacy feedback requires explicit original-owner recovery.\n",
        encoding="utf-8",
    )
    return marker


def is_throwaway_knowledge_session(project_root: Path) -> bool:
    """Recognize a marked parent-created detached plan/deps worktree.

    A detached primary or hand-made worktree is not a managed parent session.
    This legacy identity check does not grant author authority; every tracked
    write independently verifies the frozen task/session and Git ownership.
    """
    from wade.git.repo import is_head_attached, is_worktree

    try:
        if not throwaway_session_marker_path(project_root).is_file():
            return False
        return is_worktree(project_root) and not is_head_attached(project_root)
    except OSError:
        return False


def staged_ratings_path(project_root: Path) -> Path:
    """Return the recognizable legacy transport path for explicit recovery."""
    return project_root / STAGED_RATINGS_RELATIVE_PATH


def path_escapes_session(project_root: Path, path: Path) -> bool:
    """Whether *path* resolves outside the detached session root.

    ``.wade/`` is an ordinary checked-out path, so a repository (or a later
    local edit) can replace it with a symlink pointing at the main checkout or
    any other writable location. Following it would silently defeat the whole
    point of staging: a detached session must never read, write, or delete
    outside its own throwaway worktree merely to move a vote. Every filesystem
    operation on a session-relative ``.wade/`` path goes through this first —
    the readiness probe, the marker writer, the staging writer, and the flush's
    read + unlink — so a symlink planted *after* a passing preflight is still
    refused at the moment of the operation.

    An unresolvable path (``OSError`` from ``resolve``) counts as an escape —
    fail closed rather than touch an unknown location.
    """
    parent = path.parent
    try:
        if parent.is_symlink() or path.is_symlink():
            return True
        root = project_root.resolve()
        # ``strict=False``: the ``.wade`` dir may not exist yet on the probe path.
        return not parent.resolve().is_relative_to(root)
    except OSError:
        return True


def resolve_canonical_knowledge_path(project_root: Path, config: KnowledgeConfig) -> Path:
    """Resolve the knowledge path for the current session's resolved root.

    Reads and author writes always use the observed checkout's local snapshot.
    Detached/main/bounded observers stage proposals rather than redirect tracked
    writes into main or another author's branch.
    """
    return resolve_knowledge_path(_resolve_knowledge_root(project_root), config)


class ThrowawayWriteRefusal(BaseModel, frozen=True):
    """Domain result: a knowledge *write* is refused in a throwaway detached-HEAD session.

    A ``wade plan`` / ``wade task deps`` worktree is created with a detached HEAD and
    discarded at session end — it has no branch/PR to carry an ``add`` / ``tag`` edit,
    which would otherwise be an unreviewed write to main. ``plan_hint`` is True when a
    plan dir (``<root>/.wade/plans``, which ``plan_service`` creates and ``deps`` does
    not) is present, so the CLI can add the "record it in the plan file" hint only for a
    plan session — never for a ``task deps`` session.
    """

    command: str
    plan_hint: bool


def refusal_for_throwaway_write(project_root: Path, command: str) -> ThrowawayWriteRefusal | None:
    """Return a refusal when ``command`` is a knowledge write in a throwaway session, else None.

    A knowledge *write* (``add`` / ``tag add`` / ``tag remove``) must ride to origin in a
    PR; a throwaway detached-HEAD (plan / task deps) worktree has none, so the write is
    refused. ``rate``, ``get``, ``tag list``, and ``status`` stay allowed (a vote is
    bounded, append-only, and carried forward).

    Only gates inside a real git repo with a detached HEAD: a non-repo / git-unavailable
    path (tests, odd setups) has ``is_head_attached`` False too but must **not** be
    treated as a throwaway session, so an unresolvable git state returns None (allow).
    """
    from wade.git.repo import get_git_dir, is_head_attached

    try:
        if get_git_dir(project_root) is None:
            return None
        if is_head_attached(project_root):
            return None
    except OSError:
        return None  # can't determine git state — don't block
    return ThrowawayWriteRefusal(
        command=command,
        plan_hint=(project_root / ".wade" / "plans").is_dir(),
    )


def _legacy_ratings_path(ratings_path: Path) -> Path:
    """Derive the legacy counter-YAML path from the JSONL vote-log path.

    ``KNOWLEDGE.ratings.jsonl`` → ``KNOWLEDGE.ratings.yml``. Used only to fold a
    pre-#358 sidecar into the same scores on read (in memory) and to materialize it
    to JSONL on the first ratings write.
    """
    return ratings_path.with_suffix(".yml")


def knowledge_status(project_root: Path, config: KnowledgeConfig) -> KnowledgeStatus:
    """Report local knowledge/ratings state without escaping a session worktree.

    Read-only commands in a detached plan/deps worktree deliberately use the
    checked-out knowledge snapshot, not the main checkout.  That snapshot is
    enough to search, validate an entry ID, and display staged votes; reaching
    into main would make those harmless operations fail in a correctly
    constrained sandbox.  Attached worktrees keep their existing local status
    behaviour.
    """
    from wade.git import repo as git_repo

    root = project_root
    knowledge_path = resolve_knowledge_path(root, config)
    ratings_path = resolve_ratings_path(knowledge_path)
    legacy_path = _legacy_ratings_path(ratings_path)
    legacy_pending = legacy_path.is_file() and not ratings_path.exists()

    dirty = git_repo.status_porcelain_paths(
        root, str(knowledge_path), str(ratings_path), str(legacy_path)
    )

    staging_path: Path | None = None
    staged_vote_count = 0
    staging_error: str | None = None
    if is_throwaway_knowledge_session(project_root):
        staging_path = staged_ratings_path(project_root)
        try:
            staged_vote_count = len(_load_staged_rating_records(staging_path))
        except (OSError, ValueError) as exc:
            # A corrupt or unreadable transport log is exactly when someone runs
            # `wade knowledge status` to diagnose a failed handoff. Keep it out
            # of the exit code — dirty_paths and the pending legacy migration
            # must still be reportable — but carry the failure explicitly so the
            # caller cannot mistake it for "no staged votes" and call the
            # session clean when the handoff is about to fail.
            logger.warning("knowledge.staged_ratings_unreadable", error=str(exc))
            staged_vote_count = 0
            staging_error = str(exc)

    pending_proposal_count = 0
    proposal_error = None
    from wade.git import repo as git_repo
    from wade.services.knowledge_proposals import dispositions, proposals

    if git_repo.get_git_common_dir(project_root) is not None:
        try:
            finished = {
                d.proposal_id
                for d in dispositions(project_root)
                if d.action in {"discarded", "assessed"}
            }
            pending_proposal_count = sum(
                p.proposal_id not in finished for p in proposals(project_root)
            )
        except (ValueError, OSError) as exc:
            proposal_error = str(exc)

    return KnowledgeStatus(
        root=root,
        dirty_paths=dirty,
        legacy_migration_pending=legacy_pending,
        pending_proposal_count=pending_proposal_count,
        proposal_error=proposal_error,
        staged_vote_count=staged_vote_count,
        staging_path=staging_path,
        staging_error=staging_error,
    )


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


def find_entry_id(knowledge_path: Path, entry_id: str) -> bool:
    """Check whether an entry ID exists in the knowledge file."""
    if not knowledge_path.is_file():
        return False
    text = knowledge_path.read_text(encoding="utf-8")
    entries = parse_entries(text)
    return any(e.entry_id == entry_id for e in entries)


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
    """Historical usefulness only; authoritative corruption belongs to reconcile."""
    from wade.models.knowledge import RelationshipEvent
    from wade.utils.knowledge_events import decode_history, utility_counts

    history = decode_history(ratings_path.read_text(encoding="utf-8"))
    ids = {str(r["id"]) for r in history.legacy_records} | {e.entry_id for e in history.events}
    data = {}
    for entry_id in sorted(ids):
        up, down, stale = utility_counts(history, entry_id)
        links = {
            str(r["superseded_by"])
            for r in history.legacy_records
            if r.get("id") == entry_id and r.get("superseded_by")
        }
        links.update(
            e.related_id
            for e in history.events
            if isinstance(e, RelationshipEvent)
            and e.entry_id == entry_id
            and e.related_id is not None
        )
        data[entry_id] = EntryRating(
            up=up,
            down=down,
            stale=stale,
            superseded_by=next(iter(links)) if len(links) == 1 else None,
        )
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

    Return type (``dict[str, EntryRating]``) is unchanged. These totals describe
    historical usefulness only; they do not assess correctness or hide entries
    by default.
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
    from wade.utils.knowledge_events import legacy_yaml_history

    seed_lines = [
        json.dumps(json.loads(line), sort_keys=True)
        for line in legacy_yaml_history(legacy.read_text()).splitlines()
    ]
    ratings_path.write_text(
        "".join(f"{line}\n" for line in seed_lines),
        encoding="utf-8",
    )
    _git_rm(legacy)


def _append_jsonl_record(
    path: Path,
    record: dict[str, Any],
    *,
    materialize_legacy: bool,
) -> None:
    """Append one JSONL record under the canonical lock for *path*.

    ``materialize_legacy`` is true only for the tracked ratings sidecar.  A
    detached-session staging file is intentionally an isolated transport log;
    it must never inspect, migrate, or copy a canonical knowledge file.
    """
    if materialize_legacy:
        from wade.services.knowledge_ownership import assert_author_path

        assert_author_path(path)
    with file_lock(path):
        _append_jsonl_record_locked(path, record, materialize_legacy=materialize_legacy)


def _append_jsonl_record_locked(
    path: Path,
    record: dict[str, Any],
    *,
    materialize_legacy: bool,
) -> None:
    """Append one JSONL record while the caller holds *path*'s lock."""
    if path.exists() and path.is_dir():
        raise ValueError(f"Ratings path {path!s} points to a directory, not a file")
    if materialize_legacy:
        _materialize_migration_locked(path)
    line = json.dumps(record, sort_keys=True)
    with path.open("a", encoding="utf-8") as fd:
        fd.write(f"{line}\n")
        fd.flush()
        os.fsync(fd.fileno())


def _append_ratings_record(ratings_path: Path, record: dict[str, Any]) -> None:
    """Append one canonical ratings record, migrating legacy YAML if needed."""
    from wade.services.knowledge_store import append_event

    append_event(ratings_path, record)


def create_rating_event(entry_id: str, direction: str) -> RatingEvent:
    """Validate and serialize-independently create one durable rating event."""
    if direction not in ("up", "down", "stale"):
        raise ValueError(f"Invalid direction {direction!r}: must be 'up', 'down', or 'stale'")
    return RatingEvent(
        event_id=uuid.uuid4().hex,
        entry_id=entry_id,
        direction=direction,
        timestamp=datetime.now(tz=UTC).isoformat(),
    )


def record_rating(
    ratings_path: Path,
    entry_id: str,
    direction: str,
) -> RatingEvent:
    """Append an up/down/stale vote for an entry to the JSONL vote log.

    ``direction`` must be ``"up"``, ``"down"``, or ``"stale"``. A ``ts`` is stamped
    on the record — each vote is a distinct event (not a re-derivation), so votes
    are always distinct lines and both survive a union merge.
    """
    event = create_rating_event(entry_id, direction)
    _append_ratings_record(ratings_path, event.to_record())
    return event


def stage_rating_event(project_root: Path, event: RatingEvent) -> Path:
    """Persist *event* only in a detached session's ignored transport log.

    Re-validates containment at write time: a readiness preflight cannot stop a
    symlink planted at ``.wade/`` afterwards from redirecting the append out of
    the throwaway worktree.
    """
    path = staged_ratings_path(project_root)
    if path_escapes_session(project_root, path):
        raise ValueError(
            f"Refusing to stage a knowledge vote outside the session worktree: {path!s}"
        )
    _append_jsonl_record(path, event.to_record(), materialize_legacy=False)
    return path


def record_rating_for_session(
    project_root: Path,
    config: KnowledgeConfig,
    entry_id: str,
    direction: str,
) -> RatingEvent:
    """Legacy feedback is utility history, never a correctness assessment."""
    from wade.services.knowledge_ownership import actor
    from wade.services.knowledge_proposals import stage_proposal

    event = create_rating_event(entry_id, direction)
    if actor(project_root).author:
        _append_ratings_record(
            resolve_ratings_path(resolve_knowledge_path(project_root, config)), event.to_record()
        )
    else:
        stage_proposal(project_root, config, entry_id, event.to_record())
    return event


def record_handoff_rating_for_session(
    project_root: Path,
    config: KnowledgeConfig,
    entry_id: str,
    direction: str,
    handoff_id: str,
) -> RatingEvent:
    """Persist completed-plan feedback as a historical owner-bound proposal."""
    from wade.services.knowledge_proposals import stage_proposal

    if not handoff_id:
        raise ValueError("Plan handoff ratings require a stable handoff identity")
    event = create_rating_event(entry_id, direction).model_copy(
        update={
            "event_id": str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"wade:plan-handoff-rating:{handoff_id}:{entry_id}")
            ),
        }
    )
    proposal = stage_proposal(
        project_root,
        config,
        entry_id,
        event.to_record() | {"handoff_id": handoff_id},
        operation_id=handoff_id,
    )
    return RatingEvent(
        event_id=event.event_id,
        entry_id=entry_id,
        direction=direction,
        timestamp=str(proposal.original_payload["ts"]),
    )


def _load_staged_rating_records(staging_path: Path) -> list[dict[str, Any]]:
    """Read valid staged vote records, rejecting a malformed transport log.

    Staging is written only by :func:`stage_rating_event`, so treating a bad
    line as a handoff failure is safer than silently deleting a recoverable
    artefact.  Canonical ratings reads remain deliberately forgiving of merge
    damage; this stricter transport parser is only for the retryable handoff.
    """
    if not staging_path.exists():
        return []
    if staging_path.is_dir():
        raise ValueError(f"Staged ratings path {staging_path!s} points to a directory")

    records: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for line_number, raw_line in enumerate(
        staging_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            record: Any = json.loads(raw_line)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Invalid staged rating record on line {line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"Invalid staged rating record on line {line_number}")
        event_id = record.get("event_id")
        entry_id = record.get("id")
        direction = record.get("dir")
        timestamp = record.get("ts")
        if not (
            isinstance(event_id, str)
            and event_id
            and isinstance(entry_id, str)
            and isinstance(direction, str)
            and direction in ("up", "down", "stale")
            and isinstance(timestamp, str)
        ):
            raise ValueError(f"Invalid staged rating record on line {line_number}")
        if event_id in seen_event_ids:
            continue
        seen_event_ids.add(event_id)
        records.append(record)
    return records


def _event_ids_in_jsonl(path: Path) -> set[str]:
    """Return durable event IDs already present in a canonical ratings log."""
    if not path.is_file():
        return set()
    event_ids: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(raw_line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("event_id"), str):
            event_ids.add(record["event_id"])
    return event_ids


def flush_staged_ratings(
    worktree_path: Path,
    repo_root: Path,
    config: KnowledgeConfig,
) -> StagedRatingsFlushResult:
    """Preserve legacy transport logs; no generic delivery or main mutation.

    New sessions durably stage directly in the ignored common-directory queue.
    Legacy logs require explicit operator recovery with a verified owner and
    remain intact until that recovery succeeds.
    """
    path = staged_ratings_path(worktree_path)
    if path_escapes_session(worktree_path, path):
        return StagedRatingsFlushResult(
            success=False,
            worktree=worktree_path,
            message="Unsafe legacy proposal path; original retained",
        )
    if path.exists() and path.read_text().strip():
        from wade.services.knowledge_proposals import legacy_transport_archived

        try:
            if legacy_transport_archived(repo_root, worktree_path):
                return StagedRatingsFlushResult(
                    success=True,
                    worktree=worktree_path,
                    message="Original legacy transport durably archived by explicit owner recovery",
                )
        except (ValueError, OSError):
            pass
        return StagedRatingsFlushResult(
            success=False,
            worktree=worktree_path,
            message="Legacy pending feedback retained; explicitly recover with an operation owner",
        )
    return StagedRatingsFlushResult(success=True, worktree=worktree_path)


def flush_retained_staged_ratings(
    repo_root: Path,
    config: KnowledgeConfig,
) -> list[StagedRatingsFlushResult]:
    """Report retained legacy transport without accepting or delivering it.

    New proposals already persist outside disposable worktrees. Legacy logs
    require explicit original-owner recovery; a verified exact durable archive
    permits the owning parent's eventual cleanup. The sweep never removes any
    worktree or changes tracked main/branch files.
    """
    from wade.git import worktree as git_worktree

    try:
        worktrees = git_worktree.list_worktrees(repo_root)
    except Exception as exc:
        logger.warning("knowledge.retained_sweep_list_failed", error=str(exc))
        return []

    root = repo_root.resolve()
    results: list[StagedRatingsFlushResult] = []
    for entry in worktrees:
        path = Path(entry.path)
        if path.resolve() == root or entry.branch != "(detached)":
            continue
        if not is_throwaway_knowledge_session(path):
            continue
        if path_escapes_session(path, staged_ratings_path(path)):
            continue
        if not staged_ratings_path(path).exists():
            continue
        results.append(flush_staged_ratings(path, repo_root, config))
    return results


def record_supersede(ratings_path: Path, old_id: str, new_id: str) -> None:
    """Record a historical relationship, never replace correctness by utility."""
    from wade.models.knowledge import RelationshipEvent
    from wade.services.knowledge_store import append_event

    event = RelationshipEvent(
        event_id=uuid.uuid4().hex, entry_id=old_id, relation="superseded_by", related_id=new_id
    )
    append_event(ratings_path, event.model_dump(mode="json"))


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
    raw: bool = False,
    all_history: bool = False,
) -> AnnotatedKnowledgeResult:
    """Read knowledge file, annotate headings with scores, and optionally filter.

    Filtering modes (mutually exclusive, checked in order):
    - ``no_filter=True``: no score filtering at all
    - ``min_score`` set: hard cutoff on net score
    - default: no usefulness or stale filtering; exact duplicates share display only

    ``search_query`` and ``filter_tags`` combine with OR: an entry passes if it
    matches the search OR has any of the requested tags.

    Returns an AnnotatedKnowledgeResult with content=None if the knowledge file
    does not exist, and entries_count=0 if filters returned no results.
    """
    from wade.services.knowledge_search import evaluate_query, parse_query

    path = resolve_knowledge_path(project_root, config)
    if not path.exists():
        return AnnotatedKnowledgeResult(content=None, entries_count=0)
    if path.is_dir():
        raise ValueError(f"Knowledge path {config.path!r} points to a directory, not a file")

    text = path.read_text(encoding="utf-8")
    entries = parse_entries(text)
    if raw:
        return AnnotatedKnowledgeResult(content=text, entries_count=len(entries))

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

    from wade.config.loader import load_config
    from wade.git.repo import GitError
    from wade.services.knowledge_reconcile import reconcile_knowledge
    from wade.utils.knowledge_events import body_digest, exact_duplicate_groups

    representatives = {
        member: group.representative_id
        for group in exact_duplicate_groups(entries)
        for member in group.member_ids
    }
    try:
        evidence_report = reconcile_knowledge(
            project_root, config, main_ref=load_config(project_root).project.main_branch
        )
        # A tolerant read must never promote the arbitrary first payload of a
        # corrupt durable identity into a correctness assertion.
        states = {e.entry_id: e for e in evidence_report.entries} if evidence_report.valid else {}
        evidence_problems = evidence_report.problems
    except (GitError, ValueError, OSError) as exc:
        states = {}
        evidence_problems = (str(exc),)

    # Build the header (everything before the first entry)
    if entries:
        first_entry_pos = text.find(entries[0].raw)
        header = text[:first_entry_pos] if first_entry_pos > 0 else ""
    else:
        m = re.search(r"^##\s", text, re.MULTILINE)
        header = text[: m.start()] if m else text

    result_parts = [header]
    if evidence_problems:
        result_parts.append("\n> Knowledge evidence is invalid; correctness is unverified.\n")
        result_parts.extend("> " + problem + "\n" for problem in evidence_problems)
    filtered_entry_count = 0
    for entry in entries:
        if (
            not all_history
            and entry.entry_id is not None
            and representatives.get(entry.entry_id, entry.entry_id) != entry.entry_id
        ):
            continue
        entry_rating = ratings.get(entry.entry_id) if entry.entry_id else None
        up = entry_rating.up if entry_rating else 0
        down = entry_rating.down if entry_rating else 0
        stale = entry_rating.stale if entry_rating else 0
        should_annotate = entry.entry_id is not None
        candidates = [entry]
        if not all_history and entry.entry_id is not None:
            candidates += [
                e
                for e in entries
                if e.entry_id != entry.entry_id
                and representatives.get(e.entry_id or "") == entry.entry_id
            ]

        def matches(candidate: ParsedEntry) -> bool:
            rating = ratings.get(candidate.entry_id) if candidate.entry_id else None
            score = rating.up - rating.down if rating else 0
            if not no_filter and min_score is not None and score < min_score:
                return False
            if parsed_query is None and not filter_tags:
                return True
            text_match = parsed_query is not None and evaluate_query(
                parsed_query, candidate.raw.split("\n")[0] + "\n" + candidate.content
            )
            tag_match = bool(filter_tags and set(candidate.tags) & set(filter_tags))
            return text_match or tag_match

        if not any(matches(candidate) for candidate in candidates):
            continue

        # Re-build the heading with score annotation. ``should_annotate`` already
        # implies the entry was parsed from a structured ``## <id> | <date> | …``
        # heading (only that form yields an entry_id), so re-matching the raw line is
        # redundant.
        if should_annotate:
            id_part = f"{entry.entry_id} | " if entry.entry_id else ""
            assert entry.date is not None  # entry_id is always accompanied by a date
            stale_part = f"/stale:{stale}" if stale > 0 else ""
            state = states.get(entry.entry_id)
            display_matches = state is not None and state.body_digest == body_digest(entry)
            assessment = (
                state.assessment
                if state is not None and display_matches
                else "needs-recheck"
                if state is not None and state.assessments
                else "unverified"
            )
            publication = (
                state.publication if state is not None and display_matches else "unavailable"
            )
            heading = (
                f"## {id_part}{entry.date} | {entry.heading_rest} "
                f"[utility:+{up}/-{down}{stale_part}; {assessment}; {publication}]"
            )
            raw_lines = entry.raw.split("\n")
            raw_lines[0] = heading
            result_parts.append("\n".join(raw_lines))
            if state:
                if not display_matches:
                    result_parts.append(
                        "\n> Displayed claim differs from committed content; prior receipts "
                        "are historical and do not establish correctness/publication "
                        "for this text.\n"
                    )
                else:
                    result_parts.extend("\n> " + reason + "\n" for reason in state.reasons)
                for assessment_report in state.assessments:
                    if not all_history and not assessment_report.active:
                        continue
                    receipt_state = (
                        "historical for different claim content"
                        if not display_matches
                        else "active"
                        if assessment_report.active
                        else "replaced historical proof"
                    )
                    result_parts.append(
                        f"\n> Receipt {assessment_report.event.event_id}: "
                        f"{assessment_report.publication}; {receipt_state}\n"
                    )
                    result_parts.extend(
                        "\n> "
                        + proof.reason
                        + f" expected={proof.expected}; actual={proof.actual}\n"
                        for proof in assessment_report.checks
                    )
            members = [
                member
                for member, representative in representatives.items()
                if representative == entry.entry_id and member != entry.entry_id
            ]
            for member in sorted(members):
                member_state = states.get(member)
                displayed_member = next(e for e in entries if e.entry_id == member)
                member_matches = (
                    member_state is not None
                    and member_state.body_digest == body_digest(displayed_member)
                )
                member_assessment = (
                    member_state.assessment
                    if member_state is not None and member_matches
                    else "needs-recheck"
                    if member_state is not None and member_state.assessments
                    else "unverified"
                )
                result_parts.append(
                    f"\n> Exact duplicate member {member}: "
                    f"{member_assessment}; "
                    "own receipts retained (use --all-history or reconcile --json).\n"
                )
        else:
            result_parts.append(entry.raw)
        filtered_entry_count += 1

    if all_history:
        ratings_path = resolve_ratings_path(path)
        if ratings_path.exists():
            result_parts.append(
                "\n## Raw append-only history (no authority from append order)\n\n```jsonl\n"
                + ratings_path.read_text()
                + "\n```\n"
            )
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
    kind: str = "note",
    claim_key: str | None = None,
) -> KnowledgeEntry:
    """Format and append a knowledge entry to the knowledge file.

    Returns a KnowledgeEntry with the path and generated entry ID.
    """
    from wade.services.knowledge_ownership import assert_author

    assert_author(project_root)
    project_root = _resolve_knowledge_root(project_root)
    if tags:
        for tag in tags:
            err = validate_tag(tag)
            if err:
                raise ValueError(err)

    if not re.fullmatch(r"[a-z][a-z0-9_-]*", kind):
        raise ValueError("Knowledge kind must be a lowercase semantic identifier")
    if claim_key is not None and (not claim_key.strip() or any(c in claim_key for c in "|\r\n")):
        raise ValueError("Claim key must be a nonempty single heading field")
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
        key_part = f" | key: {claim_key}" if claim_key else ""
        header = (
            f"## {entry_id} | {timestamp} | {session_type} | kind: {kind}"
            f"{key_part}{tags_part}{issue_part}"
        )

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
        else:
            issue_part = " | ".join(filter(None, (issue_part, part)))
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

    from wade.services.knowledge_ownership import assert_author_path

    assert_author_path(knowledge_path)

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
    from wade.services.knowledge_ownership import assert_author_path

    assert_author_path(knowledge_path)

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
