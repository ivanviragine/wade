"""Durable binding-aware review outcomes used by the completion gate."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from wade.models.session_manifest import (
    ResolvedBinding,
    ReviewBinding,
    ReviewOutcome,
    ReviewRecord,
)
from wade.models.workflow import DelegationKind
from wade.utils.filelock import file_lock
from wade.utils.safe_state import atomic_write_state_file, list_state_files, read_state_file

_DIRECTORIES = ("reviews",)
_PREFIX = "review@"


def review_record_filename(
    delegation: DelegationKind,
    commit: str,
    binding_digest: str,
) -> str:
    """Return the canonical filename for a review tuple."""

    return f"{_PREFIX}{delegation.value}@{commit}@{binding_digest.removeprefix('sha256:')}.json"


def _load_named(root: Path, filename: str) -> ReviewRecord | None:
    raw = read_state_file(root, _DIRECTORIES, filename)
    if raw is None:
        return None
    try:
        record = ReviewRecord.model_validate_json(raw)
    except (ValidationError, ValueError):
        return None
    expected = review_record_filename(record.delegation, record.commit, record.binding.digest)
    return record if expected == filename else None


def read_review_record(
    root: Path,
    *,
    delegation: DelegationKind,
    commit: str,
    binding: ResolvedBinding,
) -> ReviewRecord | None:
    """Read the exact validated record for one applicable binding."""

    filename = review_record_filename(delegation, commit, binding.digest)
    record = _load_named(root, filename)
    if record is None or record.binding != ReviewBinding.from_resolved(binding):
        return None
    return record


# Ranked lowest-first. ``UNATTEMPTED`` sits at the bottom on purpose: a reviewer
# that never started carries strictly less information than any outcome produced
# by one that did, so recording it can never downgrade or overwrite a real
# receipt for the same commit+binding (#480).
_OUTCOME_PRECEDENCE = {
    ReviewOutcome.UNATTEMPTED: 0,
    ReviewOutcome.NOTHING_STAGED: 1,
    ReviewOutcome.TIMED_OUT: 2,
    ReviewOutcome.NO_DIFF: 3,
    ReviewOutcome.REVIEWED: 4,
}


def write_review_record(
    root: Path,
    *,
    delegation: DelegationKind,
    commit: str,
    binding: ResolvedBinding,
    outcome: ReviewOutcome,
) -> ReviewRecord | None:
    """Atomically create or promote one idempotent review tuple.

    A satisfying outcome is never downgraded, and a timeout remains counted
    until a later satisfying result promotes it.
    """

    try:
        candidate = ReviewRecord(
            commit=commit,
            delegation=delegation,
            outcome=outcome,
            consumes_pass=outcome.consumes_pass,
            binding=ReviewBinding.from_resolved(binding),
        )
    except ValidationError:
        return None
    filename = review_record_filename(delegation, commit, binding.digest)
    data = (json.dumps(candidate.model_dump(mode="json"), indent=2, sort_keys=True) + "\n").encode()
    # Atomic replacement prevents torn records, but not a lost update: two
    # writers can each observe no record and the later UNATTEMPTED replacement
    # can otherwise erase a REVIEWED receipt. Lock the entire read/compare/write
    # transaction on this exact tuple so precedence is true across processes.
    record_path = root / ".wade" / _DIRECTORIES[0] / filename
    try:
        with file_lock(record_path, create_parent=False, resolve_path=False):
            existing = read_review_record(
                root,
                delegation=delegation,
                commit=commit,
                binding=binding,
            )
            if (
                existing is not None
                and _OUTCOME_PRECEDENCE[existing.outcome] >= _OUTCOME_PRECEDENCE[outcome]
            ):
                return existing
            return (
                candidate if atomic_write_state_file(root, _DIRECTORIES, filename, data) else None
            )
    except OSError:
        return None


def list_review_records(root: Path) -> tuple[ReviewRecord, ...]:
    """Return every safely readable, internally consistent review record."""

    entries = list_state_files(root, _DIRECTORIES)
    if entries is None:
        return ()
    records: list[ReviewRecord] = []
    for entry in entries:
        if not entry.startswith(_PREFIX) or not entry.endswith(".json"):
            continue
        record = _load_named(root, entry)
        if record is not None:
            records.append(record)
    return tuple(records)


def count_binding_passes(
    root: Path,
    *,
    delegation: DelegationKind,
    binding: ResolvedBinding,
) -> int:
    """Count pass-consuming tuples only for the active reviewer binding."""

    expected = ReviewBinding.from_resolved(binding)
    return sum(
        1
        for record in list_review_records(root)
        if record.delegation is delegation and record.binding == expected and record.consumes_pass
    )


def has_other_satisfying_binding(
    root: Path,
    *,
    delegation: DelegationKind,
    commit: str,
    binding: ResolvedBinding,
) -> bool:
    """Whether this commit was satisfied by a different reviewer identity."""

    expected = ReviewBinding.from_resolved(binding)
    return any(
        record.delegation is delegation
        and record.commit == commit
        and record.binding != expected
        and record.satisfies_review
        for record in list_review_records(root)
    )
