"""Explicit plan handoff from native terminals, without parsing tool storage."""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from pathlib import Path

from wade.models.interactive_plan import (
    InteractivePlanReview,
    InteractivePlanReviews,
    InteractivePlanState,
)
from wade.models.plan_bundle import PlanBundle, PlanMember
from wade.models.workflow import SessionKind
from wade.services import native_plan_service as native
from wade.services.session_composition_service import (
    SessionCompositionError,
    load_session_manifest,
    validate_frozen_session_bundle,
)
from wade.utils.plan_validation import load_plan_file, plan_done
from wade.utils.safe_state import (
    atomic_write_state_file,
    delete_state_file,
    exclusive_write_state_file,
    list_state_files,
    read_state_file,
    state_file_present,
)

STATE = "interactive-session.json"
REVIEWS = "interactive-reviews.json"
MAX_HANDOFF_BYTES = 4_000_000


def read_artifact(path: Path) -> str:
    """Read one explicitly named native artifact, bounded and without following a link."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Plan source must be a regular file")
        data = stream.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("Plan source exceeds the 2 MB import limit")
    return data.decode("utf-8")


def active(root: Path) -> bool:
    return state_file_present(root, ("plans",), STATE)


def _binding_digest(root: Path) -> str:
    manifest = load_session_manifest(root)
    if manifest is None:
        raise ValueError("Interactive planning requires its frozen session bundle")
    try:
        validate_frozen_session_bundle(root, manifest, expected_kind=SessionKind.PLAN)
    except SessionCompositionError as exc:
        raise ValueError(str(exc)) from exc
    return manifest.bundle_digest


def begin(
    root: Path, tool: str, *, review_required: bool, knowledge_required: bool = False
) -> None:
    state = InteractivePlanState(
        session_id=str(uuid.uuid4()),
        tool=tool,
        review_required=review_required,
        knowledge_required=knowledge_required,
        bundle_digest=_binding_digest(root),
    )
    if not exclusive_write_state_file(root, ("plans",), STATE, state.model_dump_json()):
        raise ValueError("Cannot safely create the interactive planning handoff")


def _load(root: Path) -> InteractivePlanState:
    raw = read_state_file(root, ("plans",), STATE, max_bytes=MAX_HANDOFF_BYTES)
    if raw is None:
        raise ValueError("Interactive planning handoff is absent or unsafe")
    state = InteractivePlanState.model_validate_json(raw)
    if state.bundle_digest != _binding_digest(root):
        raise ValueError("Interactive planning bindings changed; restart the planning session")
    return state


def _save(root: Path, state: InteractivePlanState) -> None:
    if not atomic_write_state_file(
        root, ("plans",), STATE, state.model_dump_json().encode(), max_bytes=MAX_HANDOFF_BYTES
    ):
        raise ValueError("Cannot safely save the interactive planning handoff")


def import_artifact(root: Path, markdown: str) -> None:
    """Materialize explicitly supplied content; replace only this run's own members."""
    state = _load(root)
    bundle = native.parse_artifact(markdown)
    previous = {member.filename for member in state.bundle.plans} if state.bundle else set()
    files = list_state_files(root, ("plans",))
    if files is None:
        raise ValueError("Unsafe planning directory")
    existing = {name for name in files if name.startswith("PLAN") and name.endswith(".md")}
    if existing - previous:
        raise ValueError("Plan import conflicts with existing files; nothing was overwritten")
    for name in existing:
        if read_state_file(root, ("plans",), name) is None:
            raise ValueError("Cannot replace an unsafe planning artifact")
    # A failed or partial revision must never leave an earlier completion valid.
    state.completed = False
    _save(root, state)
    for member in bundle.plans:
        if member.filename in existing:
            written = atomic_write_state_file(
                root, ("plans",), member.filename, member.markdown.encode()
            )
        else:
            written = exclusive_write_state_file(root, ("plans",), member.filename, member.markdown)
        if not written:
            raise ValueError("Plan import failed; the session output was retained")
    for name in previous - {member.filename for member in bundle.plans}:
        if name in existing and not delete_state_file(root, ("plans",), name):
            raise ValueError("Cannot safely remove a superseded plan member")
    state.bundle = bundle
    _save(root, state)


def _current_bundle(root: Path, state: InteractivePlanState) -> PlanBundle:
    if state.bundle is None:
        files = list_state_files(root, ("plans",))
        if files is None:
            raise ValueError("Unsafe planning directory")
        names = [name for name in files if name.startswith("PLAN") and name.endswith(".md")]
        members = tuple(PlanMember(filename=name, markdown=_content(root, name)) for name in names)
        return PlanBundle(plans=members)
    native.validate_imported_set(root, state.bundle)
    return PlanBundle(
        plans=tuple(
            PlanMember(
                filename=member.filename,
                markdown=_content(root, member.filename),
                depends_on=member.depends_on,
            )
            for member in state.bundle.plans
        ),
        knowledge_votes=state.bundle.knowledge_votes,
    )


def _content(root: Path, name: str) -> str:
    # Also require a valid task boundary before handing content to a reviewer.
    load_plan_file(root / ".wade/plans" / name)
    raw = read_state_file(root, ("plans",), name)
    if raw is None:
        raise ValueError("Plan artifact is unsafe or unreadable")
    return raw.decode("utf-8")


def _reviews(root: Path) -> InteractivePlanReviews:
    raw = read_state_file(root, ("plans",), REVIEWS)
    if raw is None:
        if state_file_present(root, ("plans",), REVIEWS):
            raise ValueError("Plan review state is unsafe or unreadable")
        return InteractivePlanReviews()
    return InteractivePlanReviews.model_validate_json(raw)


def _save_reviews(root: Path, reviews: InteractivePlanReviews) -> None:
    if not atomic_write_state_file(root, ("plans",), REVIEWS, reviews.model_dump_json().encode()):
        raise ValueError("Cannot safely record the plan review")


def review_root(path: Path) -> Path | None:
    path = path.absolute()
    if path.parent.name != "plans" or path.parent.parent.name != ".wade":
        return None
    root = path.parent.parent.parent
    return root if active(root) else None


def record_review(path: Path, content: str, *, self_review: bool) -> None:
    root = review_root(path)
    if root is None:
        return
    state = _load(root)
    reviews = _reviews(root)
    reviews.plans[path.name] = InteractivePlanReview(
        session_id=state.session_id,
        bundle_digest=state.bundle_digest,
        content_digest=hashlib.sha256(content.encode()).hexdigest(),
        self_review_pending=self_review,
    )
    _save_reviews(root, reviews)


def invalidate_review(path: Path) -> None:
    """A new review attempt cannot reuse an older success if it fails."""
    root = review_root(path)
    if root is None:
        return
    state = _load(root)
    state.completed = False
    _save(root, state)
    reviews = _reviews(root)
    reviews.plans.pop(path.name, None)
    _save_reviews(root, reviews)


def acknowledge_self_review(path: Path) -> None:
    root = review_root(path)
    if root is None:
        raise ValueError("Self-review acknowledgement requires an interactive planning session")
    state = _load(root)
    reviews = _reviews(root)
    review = reviews.plans.get(path.name)
    if review is None or not review.self_review_pending:
        raise ValueError("Run wade review plan first, then perform its emitted self-review")
    if not _matches(state, review, _content(root, path.name)):
        raise ValueError("The plan or review binding changed; run wade review plan again")
    review.self_review_pending = False
    _save_reviews(root, reviews)


def _matches(state: InteractivePlanState, review: InteractivePlanReview, markdown: str) -> bool:
    return (
        review.session_id == state.session_id
        and review.bundle_digest == state.bundle_digest
        and review.content_digest == hashlib.sha256(markdown.encode()).hexdigest()
    )


def _validate_reviews(root: Path, state: InteractivePlanState, bundle: PlanBundle) -> None:
    if not state.review_required:
        return
    reviews = _reviews(root)
    for member in bundle.plans:
        review = reviews.plans.get(member.filename)
        command = f"wade review plan .wade/plans/{member.filename}"
        if review is None or not _matches(state, review, member.markdown):
            raise ValueError(f"Review is required for the current plan: {command}")
        if review.self_review_pending:
            raise ValueError(
                f"Perform the emitted self-review, then run {command} --ack-self-review"
            )


def complete(root: Path) -> None:
    state = _load(root)
    state.completed = False
    _save(root, state)
    bundle = _current_bundle(root, state)
    if plan_done(root / ".wade/plans").has_errors:
        raise ValueError("Fix plan validation errors before completing the session")
    if state.knowledge_required and bundle.knowledge_votes is None:
        raise ValueError(
            "Knowledge-enabled planning requires a bundle with knowledge_votes; "
            "use an explicit empty list when no entries were evaluated"
        )
    _validate_reviews(root, state, bundle)
    state.bundle = bundle
    state.completed = True
    _save(root, state)


def collect(root: Path) -> PlanBundle:
    """Accept only a completed submission whose files and reviews are still current."""
    state = _load(root)
    if not state.completed or state.bundle is None:
        raise ValueError("No completed plan handoff; run wade plan-session done before exiting")
    bundle = _current_bundle(root, state)
    if bundle != state.bundle:
        raise ValueError("Plans changed after completion; run wade plan-session done again")
    _validate_reviews(root, state, bundle)
    return bundle
