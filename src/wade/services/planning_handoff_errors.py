"""Safe diagnostics for persisted planning metadata that cannot be collected."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from wade import __version__
from wade.utils.safe_state import read_state_file

READER_VERSION = __version__

HandoffCategory = Literal[
    "interactive-state",
    "review-metadata",
    "frozen-bindings",
    "launch-binding",
    "progress",
    "native-state",
]

# Public protocol field names only. Unknown extra/map keys can themselves be
# secrets, even when they look like ordinary identifiers.
_SAFE_FIELDS = frozenset(
    {
        "version",
        "schema_version",
        "session_id",
        "tool",
        "model",
        "effort",
        "sandbox",
        "review_required",
        "knowledge_required",
        "bundle_digest",
        "bundle",
        "completed",
        "refresh_pending",
        "plans",
        "filename",
        "markdown",
        "depends_on",
        "knowledge_votes",
        "knowledge_proposals",
        "entry_id",
        "body_digest",
        "body_digest_version",
        "feedback",
        "note",
        "plan_filename",
        "bindings",
        "work",
        "review",
        "skills",
        "canonical_ref",
        "content_digest",
        "files",
        "materialized_path",
        "source_path",
        "session",
        "task_id",
        "workflow_revision",
        "provider",
        "project",
        "knowledge",
        "persisted_issues",
        "persisted_plan_digests",
        "pending_issue_markers",
        "pending_issue_branch_titles",
        "superseded_issue_ids",
        "self_review_pending",
        "ai_command",
        "type",
    }
)
_SAFE_CODES = frozenset(
    {
        "extra_forbidden",
        "json_invalid",
        "missing",
        "literal_error",
        "model_type",
        "model_attributes_type",
        "dict_type",
        "list_type",
        "tuple_type",
        "string_type",
        "string_too_short",
        "string_too_long",
        "int_type",
        "int_parsing",
        "bool_type",
        "bool_parsing",
        "value_error",
        "greater_than",
        "less_than",
        "union_tag_invalid",
        "union_tag_not_found",
        "enum",
        "frozen_instance",
    }
)
_LIST_FIELDS = frozenset(
    {"plans", "knowledge_votes", "knowledge_proposals", "skills", "files", "depends_on"}
)


def _declared_version(raw: bytes | None, field: str) -> str:
    try:
        data = json.loads(raw) if raw is not None else None
    except (ValueError, UnicodeError, RecursionError):
        return "unavailable"
    value = data.get(field) if isinstance(data, dict) else None
    return str(value) if type(value) is int and 0 <= value <= 9999 else "unavailable"


def _validation_cause(exc: BaseException) -> ValidationError | None:
    seen: set[int] = set()
    for _ in range(16):
        if id(exc) in seen:
            break
        seen.add(id(exc))
        if isinstance(exc, ValidationError):
            return exc
        if exc.__cause__ is None:
            break
        exc = exc.__cause__
    return None


def _safe_location(location: tuple[object, ...]) -> str:
    parts: list[str] = []
    for index, part in enumerate(location[:6]):
        if isinstance(part, str) and part in _SAFE_FIELDS:
            parts.append(part)
        elif (
            type(part) is int
            and 0 <= part <= 9999
            and index
            and location[index - 1] in _LIST_FIELDS
        ):
            parts.append(str(part))
        else:
            parts.append("[redacted]")
    if len(location) > 6:
        parts.append("...")
    return ".".join(parts) or "[root]"


class UncollectableHandoffError(ValueError):
    """Persisted metadata is not editable Markdown; retain its original owner.

    The exception cause remains available internally. Its string never includes
    validator messages, input values, unknown field names or raw JSON.
    """

    def __init__(
        self,
        root: Path,
        category: HandoffCategory,
        cause: BaseException,
        *,
        raw: bytes | None = None,
    ) -> None:
        self.root = root
        self.category = category
        validation = _validation_cause(cause)
        details: list[str] = []
        reason = "invalid persisted metadata or frozen binding integrity"
        if validation is not None:
            errors = validation.errors(
                include_input=False, include_context=False, include_url=False
            )
            codes = {error["type"] for error in errors}
            reason = (
                "unsupported fields"
                if "extra_forbidden" in codes
                else "malformed JSON"
                if "json_invalid" in codes
                else "schema validation failed"
            )
            for error in errors[:8]:
                code = error["type"] if error["type"] in _SAFE_CODES else "validation_error"
                details.append(f"{_safe_location(error['loc'])}: {code}")
            if len(errors) > 8:
                details.append(f"{len(errors) - 8} additional errors omitted")
        manifest = read_state_file(root, ("session",), "manifest.json")
        schema = _declared_version(
            manifest if category == "frozen-bindings" else raw,
            "schema_version" if category == "frozen-bindings" else "version",
        )
        workflow = _declared_version(manifest, "workflow_revision")
        detail = "; ".join(details)
        message = (
            f"Uncollectable {category}: {reason}. "
            f"Reader WADE {READER_VERSION[:48]}; declared schema {schema}; "
            f"declared workflow {workflow}; writer version unavailable. "
            "State may be malformed or incompatible; schema/runtime skew is possible."
        )
        if detail:
            message += f" Fields: {detail}."
        super().__init__(message)
