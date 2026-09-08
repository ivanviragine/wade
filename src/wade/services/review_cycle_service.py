"""Safe PR-comment review-cycle state outside immutable session bundles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from wade.models.review_cycle import ReviewCycleContext
from wade.utils.safe_state import (
    atomic_write_state_file,
    delete_state_file,
    read_state_file,
    state_file_present,
)

_DIRECTORIES = ("review-cycles",)
_PREFIX = "review-cycle@"


@dataclass(frozen=True)
class ReviewCycleLookup:
    """A context lookup that preserves an unsafe-state diagnostic."""

    context: ReviewCycleContext | None
    diagnostic: str | None = None


def review_cycle_filename(issue_number: str) -> str:
    """Return the one canonical context filename for an issue's active cycle."""

    normalized = issue_number.strip().lstrip("#")
    if not normalized.isdigit() or int(normalized) <= 0:
        raise ValueError("Review-cycle issue number must be positive")
    return f"{_PREFIX}{int(normalized)}.json"


def read_review_cycle(
    root: Path,
    *,
    issue_number: str,
    pr_number: int,
) -> ReviewCycleLookup:
    """Read a current identity-matched cycle, never trusting malformed state."""

    try:
        filename = review_cycle_filename(issue_number)
    except ValueError as exc:
        return ReviewCycleLookup(None, str(exc))
    raw = read_state_file(root, _DIRECTORIES, filename)
    if raw is None:
        if state_file_present(root, _DIRECTORIES, filename):
            return ReviewCycleLookup(
                None,
                "PR-comment review-cycle state is unreadable or unsafe",
            )
        return ReviewCycleLookup(None)
    try:
        context = ReviewCycleContext.model_validate_json(raw)
    except (ValidationError, ValueError):
        return ReviewCycleLookup(None, "PR-comment review-cycle state is malformed")
    if context.issue_number != str(int(issue_number.lstrip("#"))):
        return ReviewCycleLookup(None, "PR-comment review-cycle issue identity does not match")
    if context.pr_number != pr_number:
        return ReviewCycleLookup(None, "PR-comment review-cycle PR identity does not match")
    return ReviewCycleLookup(context)


def initialize_or_refresh_review_cycle(
    root: Path,
    *,
    issue_number: str,
    pr_number: int,
    baseline_commit: str,
    feedback: str,
) -> ReviewCycleLookup:
    """Create a cycle or refresh its feedback while preserving its first baseline."""

    existing = read_review_cycle(root, issue_number=issue_number, pr_number=pr_number)
    if existing.diagnostic is not None:
        return existing
    try:
        context = (
            ReviewCycleContext(**(existing.context.model_dump() | {"feedback": feedback}))
            if existing.context is not None
            else ReviewCycleContext(
                issue_number=issue_number,
                pr_number=pr_number,
                baseline_commit=baseline_commit,
                feedback=feedback,
            )
        )
        filename = review_cycle_filename(issue_number)
    except (ValidationError, ValueError) as exc:
        return ReviewCycleLookup(None, f"Could not validate PR-comment review-cycle state: {exc}")
    payload = (
        json.dumps(context.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    ).encode()
    if not atomic_write_state_file(root, _DIRECTORIES, filename, payload):
        return ReviewCycleLookup(None, "Could not persist PR-comment review-cycle state safely")
    return ReviewCycleLookup(context)


def clear_review_cycle(root: Path, *, issue_number: str) -> bool:
    """Remove an active cycle only after its PR-comment session has succeeded."""

    try:
        filename = review_cycle_filename(issue_number)
    except ValueError:
        return False
    if not state_file_present(root, _DIRECTORIES, filename):
        return True
    return delete_state_file(root, _DIRECTORIES, filename)
