"""Task domain models — Task, PlanFile, Complexity, Label, TaskState."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class Complexity(StrEnum):
    """Task complexity level — maps to AI model tiers."""

    EASY = "easy"
    MEDIUM = "medium"
    COMPLEX = "complex"
    VERY_COMPLEX = "very_complex"


class TaskState(StrEnum):
    """Task lifecycle state."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


class CloseReason(StrEnum):
    """Typed reason for closing a task — maps to provider-specific close semantics.

    GitHub renders ``NOT_PLANNED`` as a grey "closed as not planned" state,
    distinct from the default "closed as completed". Providers without a
    close-reason concept accept and ignore it.
    """

    COMPLETED = "completed"
    NOT_PLANNED = "not planned"


class LabelType(StrEnum):
    """Categories of labels managed by WADE."""

    ISSUE_LABEL = "issue_label"
    IN_PROGRESS = "in_progress"
    COMPLEXITY = "complexity"
    PLANNED_BY = "planned_by"
    PLANNED_MODEL = "planned_model"
    IMPLEMENTED_BY = "implemented_by"
    IMPLEMENTED_MODEL = "implemented_model"
    REVIEW_ADDRESSED_BY = "review_addressed_by"
    REVIEW_ADDRESSED_MODEL = "review_addressed_model"
    AI_LABEL = "ai_label"


_LABEL_PREFIX_MAP: dict[str, LabelType] = {
    "planned-by:": LabelType.PLANNED_BY,
    "planned-model:": LabelType.PLANNED_MODEL,
    "implemented-by:": LabelType.IMPLEMENTED_BY,
    "implemented-model:": LabelType.IMPLEMENTED_MODEL,
    "review-addressed-by:": LabelType.REVIEW_ADDRESSED_BY,
    "review-addressed-model:": LabelType.REVIEW_ADDRESSED_MODEL,
    "complexity:": LabelType.COMPLEXITY,
}


def infer_label_type(name: str) -> LabelType:
    """Infer LabelType from a label name prefix.

    Returns the matching LabelType if the name starts with a known prefix,
    otherwise returns LabelType.ISSUE_LABEL.
    """
    normalized = name.strip().lower()
    for prefix, label_type in _LABEL_PREFIX_MAP.items():
        if normalized.startswith(prefix):
            return label_type
    return LabelType.ISSUE_LABEL


class Label(BaseModel):
    """A GitHub label with metadata."""

    name: str
    color: str = "ededed"
    description: str = ""
    label_type: LabelType = LabelType.ISSUE_LABEL

    @model_validator(mode="after")
    def _infer_label_type(self) -> Label:
        if self.label_type == LabelType.ISSUE_LABEL:
            self.label_type = infer_label_type(self.name)
        return self


class Task(BaseModel):
    """A task from the external provider (GitHub Issue, etc.)."""

    id: str
    title: str = Field(max_length=256)
    body: str = ""
    state: TaskState = TaskState.OPEN
    complexity: Complexity | None = None
    labels: list[Label] = []
    url: str | None = None
    parent_id: str | None = None
    subtask_ids: list[str] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None


def parse_complexity_from_body(body: str) -> Complexity | None:
    """Parse a ``## Complexity`` section from markdown body text.

    Scans the body for a ``## Complexity`` heading and matches the first word
    against known complexity levels (easy, medium, complex, very_complex).
    """
    in_section = False
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("## complexity"):
            in_section = True
            continue
        if in_section:
            if stripped.startswith("## "):
                break  # Next section
            text = stripped.lower()
            if text:
                match = re.match(r"(easy|medium|complex|very_complex)", text)
                if match:
                    return Complexity(match.group(1))
    return None


# Characters stripped from the ends of a raw "Base Branch" value: surrounding
# markdown backticks and quotes plus whitespace. Internal whitespace is left
# intact on purpose so a malformed value (e.g. ``feature x``) survives to the
# plan-done validator instead of being silently truncated to a valid-looking ref.
_BASE_BRANCH_EDGE_CHARS = "`\"' "


def parse_base_branch_from_body(body: str) -> str | None:
    """Parse a ``## Base Branch`` section from markdown body text.

    Scans for a ``## Base Branch`` heading (case-insensitive) and returns the
    first non-empty line beneath it, with surrounding backticks/quotes/whitespace
    stripped. Returns ``None`` when the section is absent or holds no non-empty
    value — both mean "use the configured main branch".

    The value is intentionally **not** split on internal whitespace: a malformed
    entry like ``feature x`` is returned verbatim so
    :func:`wade.utils.plan_validation.validate_plan_dir` can flag it as an error,
    rather than being silently truncated to a valid-looking ``feature``.
    """
    in_section = False
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            # Match the heading EXACTLY, mirroring PlanFile.sections' parser. A
            # prefix match ("## base branch"...) would misread an unrelated
            # "## Base Branch Notes" heading as a declaration — and since
            # validate_plan_dir keys its empty-section check on the exact
            # "base branch" section name, that misparse would slip through (#376).
            if stripped[3:].strip().lower() == "base branch":
                in_section = True
                continue
            if in_section:
                break  # Next section — no value found
        elif in_section and stripped:
            return stripped.strip(_BASE_BRANCH_EDGE_CHARS) or None
    return None


def parse_complexity_from_labels(labels: list[Label]) -> Complexity | None:
    """Extract complexity from a ``complexity:X`` label.

    Scans labels for one matching ``complexity:<value>`` where value is a
    known :class:`Complexity` member.  Returns the first match or ``None``.
    """
    for label in labels:
        normalized = label.name.strip().lower()
        if normalized.startswith("complexity:"):
            value = normalized.removeprefix("complexity:")
            try:
                return Complexity(value)
            except ValueError:
                continue
    return None


_TRACKING_PREFIX = "Tracking:"
"""Title prefix that identifies a tracking (epic) issue."""

_CHECKLIST_ISSUE_RE = re.compile(
    r"^[ \t]*-\s*\[(?P<mark>[ xX])\]\s+`?#(?P<id>\d+)`?",
    re.MULTILINE,
)
"""Matches checklist issue refs like ``- [ ] #42`` or ``- [X] `#42```."""

_ANY_CHECKLIST_ITEM_RE = re.compile(r"^[ \t]*-\s*\[[xX ]\]\s+", re.MULTILINE)
"""Matches any checklist item (checked or unchecked), including indented lines."""


def is_tracking_issue(title: str) -> bool:
    """Return True if the title indicates a tracking (epic) issue."""
    return title.startswith(_TRACKING_PREFIX)


def parse_tracking_child_ids(body: str, *, include_checked: bool = False) -> list[str]:
    """Extract child issue numbers from unchecked checklist items.

    By default, only matches unchecked ``- [ ] #N`` lines and skips checked
    items. Set ``include_checked=True`` to include checked checklist refs too.

    Supports indented checklist items and optional backticks around ``#N``.
    Other ``#N`` references elsewhere in the body are ignored.

    Returns issue numbers as strings without ``#`` prefix.
    """
    child_ids: list[str] = []
    for match in _CHECKLIST_ISSUE_RE.finditer(body):
        if include_checked or match.group("mark") == " ":
            child_ids.append(match.group("id"))
    return child_ids


def has_checklist_items(body: str) -> bool:
    """Return True if the body contains any checklist items (checked or unchecked).

    Use this to distinguish bodies using checklist format from those using
    plain ``#N`` issue references.
    """
    return bool(_ANY_CHECKLIST_ITEM_RE.search(body))


def parse_all_issue_refs(body: str) -> list[str]:
    """Extract all ``#N`` issue references from a body, in order.

    Returns issue numbers as strings without ``#`` prefix.
    Use this to catch plain references that are not in checklist format.
    """
    return re.findall(r"#(\d+)", body)


_DEP_LINE_RE = re.compile(r"\*\*Depends on:\*\*\s*(.*?)$", re.MULTILINE)
_BLOCK_LINE_RE = re.compile(r"\*\*Blocks:\*\*\s*(.*?)$", re.MULTILINE)
_ISSUE_REF_RE = re.compile(r"#(\d+)")


def parse_dependency_refs(body: str) -> dict[str, list[str]]:
    """Parse ``**Depends on:**`` and ``**Blocks:**`` issue refs from markdown body.

    Returns a dict with ``depends_on`` and ``blocks`` keys, each a list of
    issue number strings (without ``#`` prefix).
    """
    result: dict[str, list[str]] = {"depends_on": [], "blocks": []}
    dep_match = _DEP_LINE_RE.search(body)
    if dep_match:
        result["depends_on"] = _ISSUE_REF_RE.findall(dep_match.group(1))
    block_match = _BLOCK_LINE_RE.search(body)
    if block_match:
        result["blocks"] = _ISSUE_REF_RE.findall(block_match.group(1))
    return result


class PlanFile(BaseModel):
    """A parsed plan markdown file."""

    path: Path
    title: str = Field(max_length=256)
    complexity: Complexity | None = None
    base_branch: str | None = None
    body: str
    sections: dict[str, str] = {}

    @classmethod
    def from_markdown(cls, path: Path) -> PlanFile:
        """Parse a plan markdown file.

        The first line must be a `# Title` heading.
        Sections are extracted from `## Heading` markers.
        Complexity is parsed from a `## Complexity` section if present.
        A base branch is parsed from an optional `## Base Branch` section.
        """
        content = path.read_text(encoding="utf-8")
        lines = content.split("\n")

        # Extract title from first heading
        title = ""
        body_start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                title = stripped[2:].strip()
                body_start = i + 1
                break

        if not title:
            raise ValueError(f"Plan file {path} must have a '# Title' as the first heading")

        body = "\n".join(lines[body_start:]).strip()

        # Parse sections (## headings)
        sections: dict[str, str] = {}
        current_section = ""
        current_lines: list[str] = []

        for line in lines[body_start:]:
            if line.strip().startswith("## "):
                if current_section:
                    sections[current_section] = "\n".join(current_lines).strip()
                current_section = line.strip()[3:].strip().lower()
                current_lines = []
            elif current_section:
                current_lines.append(line)

        if current_section:
            sections[current_section] = "\n".join(current_lines).strip()

        complexity = parse_complexity_from_body(body)
        base_branch = parse_base_branch_from_body(body)

        return cls(
            path=path,
            title=title,
            complexity=complexity,
            base_branch=base_branch,
            body=body,
            sections=sections,
        )
