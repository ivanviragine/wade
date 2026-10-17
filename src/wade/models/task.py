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


# ---------------------------------------------------------------------------
# Task reference grammar
# ---------------------------------------------------------------------------
#
# Task bodies (dependency sections, tracking checklists) reference other tasks
# of the *same* provider. GitHub and Markdown keep their established
# ``#<digits>`` form; every other provider uses a namespaced token such as
# ``clickup:abc1234``. Characters outside ``[A-Za-z0-9_-]`` are percent-encoded
# (UTF-8, uppercase hex) so a reference is always one complete token, never a
# prefix of a longer ID: ``#123abc`` is not task ``123``. Readers decode only
# complete tokens of their own namespace, and only canonical encodings.

HASH_REFERENCE_PROVIDERS = frozenset({"github", "markdown"})
"""Providers whose task bodies reference tasks as ``#<digits>``."""

_REF_SAFE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_QUALIFIED_ID_PATTERN = r"(?:[A-Za-z0-9_-]|%[0-9A-F]{2})+"
_CANONICAL_DECIMAL_RE = re.compile(r"[1-9][0-9]*")
# ``(?<![\w&/#])`` rejects ``repo#42``, ``&#42;`` and URL fragments; ``(?!\w)``
# rejects ``#123abc`` instead of truncating it to ``123``.
_HASH_REF_PATTERN = r"(?<![\w&/#])#(?P<hash>[0-9]+)(?!\w)"
# Legacy WADE content wrote ``#<id>`` for every provider. Opaque IDs are read in
# that form only inside structured positions (checklist items, dependency lines).
_LEGACY_HASH_PATTERN = r"(?<![\w&/#])#(?P<legacy>[A-Za-z0-9_-]+)(?![\w-])"


def escape_reference_id(task_id: str) -> str:
    """Percent-encode every character outside ``[A-Za-z0-9_-]`` (UTF-8, uppercase)."""
    return "".join(
        char if char in _REF_SAFE_CHARS else "".join(f"%{b:02X}" for b in char.encode("utf-8"))
        for char in task_id
    )


def unescape_reference_id(token: str) -> str | None:
    """Decode a canonical escaped ID, or ``None`` for malformed/non-canonical input."""
    if not token or re.fullmatch(_QUALIFIED_ID_PATTERN, token) is None:
        return None
    raw = bytearray()
    index = 0
    while index < len(token):
        if token[index] == "%":
            raw.append(int(token[index + 1 : index + 3], 16))
            index += 3
        else:
            raw.extend(token[index].encode("ascii"))
            index += 1
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return decoded if escape_reference_id(decoded) == token else None


def _uses_hash_refs(provider: str) -> bool:
    return str(provider) in HASH_REFERENCE_PROVIDERS


def format_task_ref(task_id: str, provider: str = "github") -> str:
    """Render a task-body reference: ``#42`` (GitHub/Markdown) or ``clickup:abc1234``."""
    namespace = str(provider)
    if _uses_hash_refs(namespace) and _CANONICAL_DECIMAL_RE.fullmatch(task_id):
        return f"#{task_id}"
    return f"{namespace}:{escape_reference_id(task_id)}"


def _task_ref_regex(provider: str, *, legacy_hash: bool) -> re.Pattern[str]:
    namespace = str(provider)
    alternatives = [
        rf"(?<![\w:/.%-]){re.escape(namespace)}:(?P<qualified>{_QUALIFIED_ID_PATTERN})"
        r"(?![A-Za-z0-9_%-])"
    ]
    if _uses_hash_refs(namespace):
        alternatives.append(_HASH_REF_PATTERN)
    elif legacy_hash:
        alternatives.append(_LEGACY_HASH_PATTERN)
    return re.compile("|".join(alternatives))


def _decode_ref_match(match: re.Match[str]) -> str | None:
    groups = match.groupdict()
    if groups.get("qualified") is not None:
        return unescape_reference_id(groups["qualified"])
    if groups.get("hash") is not None:
        value = groups["hash"]
        return value if _CANONICAL_DECIMAL_RE.fullmatch(value) else None
    return groups.get("legacy")


def parse_task_refs(text: str, provider: str = "github", *, legacy_hash: bool = False) -> list[str]:
    """Extract exact task IDs referenced in *text*, in order.

    Only complete tokens of *provider*'s namespace are decoded. ``legacy_hash``
    additionally accepts ``#<opaque-id>`` for non-hash providers — use it only
    for structured positions that WADE itself generated.
    """
    refs: list[str] = []
    for match in _task_ref_regex(provider, legacy_hash=legacy_hash).finditer(text):
        decoded = _decode_ref_match(match)
        if decoded is not None:
            refs.append(decoded)
    return refs


def _leading_task_ref(text: str, provider: str) -> str | None:
    """Decode the reference that starts *text* (after an optional `` ` `` or ``[``)."""
    candidate = text[1:] if text[:1] in ("`", "[") else text
    match = _task_ref_regex(provider, legacy_hash=True).match(candidate)
    return _decode_ref_match(match) if match is not None else None


_CHECKLIST_ITEM_RE = re.compile(r"^[ \t]*-\s*\[(?P<mark>[ xX])\]\s+(?P<rest>.*)$", re.MULTILINE)
"""Matches checklist items like ``- [ ] #42`` or ``- [X] `clickup:abc1234```."""

_ANY_CHECKLIST_ITEM_RE = re.compile(r"^[ \t]*-\s*\[[xX ]\]\s+", re.MULTILINE)
"""Matches any checklist item (checked or unchecked), including indented lines."""


def is_tracking_issue(title: str) -> bool:
    """Return True if the title indicates a tracking (epic) issue."""
    return title.startswith(_TRACKING_PREFIX)


def parse_tracking_child_ids(
    body: str, *, include_checked: bool = False, provider: str = "github"
) -> list[str]:
    """Extract exact child task IDs from checklist items that start with a reference.

    By default, only unchecked items are returned; ``include_checked=True``
    includes checked items too. Supports indented items and optional backticks
    or link brackets around the reference. Other references elsewhere in the
    body are ignored.
    """
    child_ids: list[str] = []
    for match in _CHECKLIST_ITEM_RE.finditer(body):
        if not include_checked and match.group("mark") != " ":
            continue
        child = _leading_task_ref(match.group("rest"), provider)
        if child is not None:
            child_ids.append(child)
    return child_ids


def has_checklist_items(body: str) -> bool:
    """Return True if the body contains any checklist items (checked or unchecked).

    Use this to distinguish bodies using checklist format from those using
    plain task references.
    """
    return bool(_ANY_CHECKLIST_ITEM_RE.search(body))


def parse_all_issue_refs(body: str, provider: str = "github") -> list[str]:
    """Extract all complete task references of *provider* from a body, in order."""
    return parse_task_refs(body, provider)


_DEP_LINE_RE = re.compile(r"\*\*Depends on:\*\*\s*(.*?)$", re.MULTILINE)
_BLOCK_LINE_RE = re.compile(r"\*\*Blocks:\*\*\s*(.*?)$", re.MULTILINE)


def parse_dependency_refs(body: str, provider: str = "github") -> dict[str, list[str]]:
    """Parse ``**Depends on:**`` and ``**Blocks:**`` task refs from markdown body.

    Returns a dict with ``depends_on`` and ``blocks`` keys, each a list of exact
    task IDs.
    """
    result: dict[str, list[str]] = {"depends_on": [], "blocks": []}
    dep_match = _DEP_LINE_RE.search(body)
    if dep_match:
        result["depends_on"] = parse_task_refs(dep_match.group(1), provider, legacy_hash=True)
    block_match = _BLOCK_LINE_RE.search(body)
    if block_match:
        result["blocks"] = parse_task_refs(block_match.group(1), provider, legacy_hash=True)
    return result


_ISSUE_HEADING_RE = re.compile(rf"^#\s*Issue\s+#(?P<id>{_QUALIFIED_ID_PATTERN}):\s*(?P<title>.+)$")


def format_issue_heading(task_id: str, title: str) -> str:
    """Render the runtime ``# Issue #<id>: <title>`` heading with the exact ID.

    Numeric IDs keep the legacy ``# Issue #42: ...`` shape; other IDs are
    escaped as one complete token (``# Issue #abc1234: ...``,
    ``# Issue #a%3Ab: ...``) so :func:`parse_issue_heading` recovers them exactly.
    """
    return f"# Issue #{escape_reference_id(task_id)}: {title}"


def parse_issue_heading(line: str) -> tuple[str, str] | None:
    """Return ``(task_id, title)`` from a :func:`format_issue_heading` line, or ``None``."""
    match = _ISSUE_HEADING_RE.match(line)
    if match is None:
        return None
    task_id = unescape_reference_id(match.group("id"))
    if task_id is None:
        return None
    return task_id, match.group("title").strip()


class TaskReference(BaseModel):
    """Provider-aware reference to one task in GitHub PR content.

    Only GitHub-backed tasks are GitHub issues: they render ``#42`` and may use
    closing keywords. Every other provider — including numeric ClickUp or
    Markdown IDs — renders a qualified ``<provider>:<id>`` token (linked to the
    provider URL when known) and never a GitHub closing keyword, so merging the
    PR cannot close an unrelated GitHub issue that shares the number.
    """

    model_config = {"frozen": True}

    provider: str = "github"
    task_id: str
    url: str | None = None

    @property
    def is_github_issue(self) -> bool:
        return self.provider == "github" and bool(_CANONICAL_DECIMAL_RE.fullmatch(self.task_id))

    @property
    def label(self) -> str:
        """Plain text form: ``#42`` or ``clickup:abc1234``."""
        if self.is_github_issue:
            return f"#{self.task_id}"
        return f"{self.provider}:{escape_reference_id(self.task_id)}"

    @property
    def link(self) -> str:
        """Markdown form: ``#42``, ``[clickup:abc1234](<url>)`` or the plain label."""
        if self.is_github_issue or not self.url:
            return self.label
        return f"[{self.label}]({self.url})"

    def reference_line(self, close: bool) -> str:
        """The WADE-owned PR line declaring this task and its closure intent.

        GitHub: ``Closes #42`` / ``Implements #42``. External providers:
        ``Completes <link>`` (WADE closes it through the provider after merge)
        / ``Implements <link>``.
        """
        if self.is_github_issue:
            return f"{'Closes' if close else 'Implements'} {self.label}"
        return f"{'Completes' if close else 'Implements'} {self.link}"

    def part_of_line(self) -> str:
        return f"Part of {self.link}"

    def token_pattern(self) -> str:
        """Regex matching exactly this reference's label or link (complete token)."""
        if self.is_github_issue:
            return rf"#{re.escape(self.task_id)}(?!\w)"
        label = re.escape(self.label)
        return rf"(?:\[{label}\]\([^)\s]*\)|{label})(?![A-Za-z0-9_%-])"


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
        return cls.from_text(path, content)

    @classmethod
    def from_text(cls, path: Path, content: str) -> PlanFile:
        """Parse already-read Markdown, including descriptor-safe native imports."""
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
