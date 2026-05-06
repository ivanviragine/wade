"""Markdown provider — read/write tasks from a single central markdown file.

The provider treats one markdown file as the source of truth for issues.
Each issue is a ``## #<id> <title>`` heading followed by an optional metadata
HTML comment and a body. PRs continue to be managed by the regular GitHub
flow (``git/pr.py``) — this provider only owns the issue lifecycle.

File format::

    # Wade Issues

    ## #1 Add login feature

    <!-- wade
    state: open
    labels: feature, complexity:medium
    -->

    Body goes here. Sub-headings (### ...) are part of the body.

    ## #2 Fix parser bug

    <!-- wade
    state: closed
    -->

    Another body.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

from wade.models.config import ProviderConfig
from wade.models.task import (
    Label,
    Task,
    TaskState,
    parse_complexity_from_body,
    parse_complexity_from_labels,
)
from wade.providers.base import AbstractTaskProvider

logger = structlog.get_logger()

DEFAULT_FILE_NAME = "ISSUES.md"
DEFAULT_FILE_HEADER = "# Wade Issues\n\n<!-- Managed by the Wade markdown issue provider. -->\n"

# Heading like "## #42 Title", "## #42: Title", or "## #42 - Title".
# Em (U+2014) and en (U+2013) dashes are accepted via explicit escapes.
_HEADING_RE = re.compile(
    "^##\\s+#(?P<id>[A-Za-z0-9_-]+)\\s*(?:[:\\-–—]\\s*)?(?P<title>.*?)\\s*$",  # noqa: RUF001
    re.MULTILINE,
)

# Metadata HTML comment block: <!-- wade ... -->
_META_RE = re.compile(r"<!--\s*wade\s*\n(?P<body>.*?)\n\s*-->", re.DOTALL)

_VALID_STATES = frozenset(s.value for s in TaskState)


class MarkdownProviderError(Exception):
    """Errors raised by MarkdownIssueProvider for non-recoverable conditions."""


class TaskNotFoundError(MarkdownProviderError):
    """Raised when a task ID does not exist in the markdown file."""


@dataclass
class _Section:
    """A parsed issue section from the markdown file.

    ``meta`` and ``body`` are kept separate so we can rewrite metadata
    without touching the user-authored body.
    """

    id: str
    title: str
    meta: dict[str, str]
    body: str
    # Raw spans (start, end) into the source text, for in-place rewrites.
    span: tuple[int, int]


def _parse_meta_block(text: str) -> dict[str, str]:
    """Parse ``key: value`` lines from a wade metadata block."""
    meta: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip().lower()] = value.strip()
    return meta


def _format_meta_block(meta: dict[str, str]) -> str:
    """Format a metadata dict as a ``<!-- wade ... -->`` HTML comment."""
    lines = ["<!-- wade"]
    for key in ("state", "labels"):
        value = meta.get(key, "")
        # Always emit ``state`` (default "open"); only emit ``labels`` if non-empty.
        if key == "state":
            lines.append(f"state: {value or TaskState.OPEN.value}")
        elif value:
            lines.append(f"labels: {value}")
    lines.append("-->")
    return "\n".join(lines)


def _split_labels(value: str) -> list[str]:
    """Split a comma-separated labels string into a clean list."""
    return [name.strip() for name in value.split(",") if name.strip()]


def _join_labels(names: list[str]) -> str:
    """Join label names into a comma-separated string."""
    return ", ".join(name for name in names if name)


def _parse_sections(text: str) -> list[_Section]:
    """Split a markdown file into issue sections.

    Returns sections in document order. Anything before the first ``## ``
    heading is preserved in the file as a prelude (handled by the writer).
    """
    headings = list(_HEADING_RE.finditer(text))
    if not headings:
        return []

    sections: list[_Section] = []
    for i, match in enumerate(headings):
        start = match.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        chunk = text[start:end]

        # Body is everything after the heading line.
        after_heading = chunk[match.end() - start :].lstrip("\n")

        meta_match = _META_RE.match(after_heading)
        if meta_match:
            meta = _parse_meta_block(meta_match.group("body"))
            body = after_heading[meta_match.end() :].lstrip("\n").rstrip()
        else:
            meta = {}
            body = after_heading.rstrip()

        sections.append(
            _Section(
                id=match.group("id"),
                title=match.group("title").strip(),
                meta=meta,
                body=body,
                span=(start, end),
            )
        )
    return sections


def _format_section(section: _Section) -> str:
    """Format a parsed section back to canonical markdown."""
    parts = [f"## #{section.id} {section.title}".rstrip(), ""]
    parts.append(_format_meta_block(section.meta))
    if section.body:
        parts.extend(["", section.body.rstrip()])
    parts.append("")
    return "\n".join(parts)


def _section_to_task(section: _Section) -> Task:
    """Convert a parsed _Section to a Task model."""
    state_str = (section.meta.get("state") or "open").lower()
    state = TaskState(state_str) if state_str in _VALID_STATES else TaskState.OPEN

    label_names = _split_labels(section.meta.get("labels", ""))
    labels = [Label(name=name) for name in label_names]

    return Task(
        id=section.id,
        title=section.title,
        body=section.body,
        state=state,
        complexity=parse_complexity_from_labels(labels) or parse_complexity_from_body(section.body),
        labels=labels,
        url=None,
    )


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically (write tmp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


class MarkdownIssueProvider(AbstractTaskProvider):
    """Task provider backed by a single central markdown file.

    Configuration (``provider.settings`` in ``.wade.yml``):
        path: Relative or absolute path to the markdown file.
              Relative paths resolve against ``project_root`` (the directory
              containing ``.wade.yml``) or CWD if no project root is set.
              Defaults to ``ISSUES.md``.
    """

    def __init__(
        self,
        config: ProviderConfig | None = None,
        project_root: Path | None = None,
    ) -> None:
        super().__init__(config)
        self._project_root = project_root or Path.cwd()
        self._path = self._resolve_path()

    # --- Path resolution ---

    def _resolve_path(self) -> Path:
        raw = self._config.settings.get("path", DEFAULT_FILE_NAME)
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate
        return (self._project_root / candidate).resolve()

    # --- File I/O ---

    def _read_text(self) -> str:
        if not self._path.exists():
            return ""
        return self._path.read_text(encoding="utf-8")

    def _write_text(self, content: str) -> None:
        if not content.endswith("\n"):
            content += "\n"
        _atomic_write(self._path, content)

    def _load_sections(self) -> tuple[str, list[_Section]]:
        """Return (prelude, sections) where prelude is everything before
        the first ``## `` heading.
        """
        text = self._read_text()
        sections = _parse_sections(text)
        prelude = text[: sections[0].span[0]] if sections else text
        return prelude, sections

    def _persist(self, prelude: str, sections: list[_Section]) -> None:
        """Reassemble the file from prelude + sections and write atomically."""
        if not prelude and not sections:
            self._write_text(DEFAULT_FILE_HEADER)
            return

        body_parts: list[str] = []
        if prelude.strip():
            body_parts.append(prelude.rstrip() + "\n")
        elif sections:
            # Ensure file always has at least the default header.
            body_parts.append(DEFAULT_FILE_HEADER.rstrip() + "\n")

        for section in sections:
            body_parts.append("\n" + _format_section(section))

        self._write_text("".join(body_parts))

    # --- Section helpers ---

    def _find_section(self, sections: list[_Section], task_id: str) -> _Section:
        for section in sections:
            if section.id == task_id:
                return section
        raise TaskNotFoundError(f"Task #{task_id} not found in {self._path}")

    def _next_id(self, sections: list[_Section]) -> str:
        existing = []
        for section in sections:
            try:
                existing.append(int(section.id))
            except ValueError:
                continue
        return str(max(existing) + 1) if existing else "1"

    # --- Issue CRUD ---

    def list_tasks(
        self,
        label: str | None = None,
        state: TaskState | None = TaskState.OPEN,
        limit: int = 50,
        exclude_labels: list[str] | None = None,
    ) -> list[Task]:
        """List tasks from the markdown file with optional filtering."""
        _, sections = self._load_sections()
        tasks: list[Task] = []
        exclude_set = set(exclude_labels or [])

        for section in sections:
            task = _section_to_task(section)

            if state is not None and task.state != state:
                continue

            label_names = {lbl.name for lbl in task.labels}
            if label and label not in label_names:
                continue
            if exclude_set and label_names & exclude_set:
                continue

            tasks.append(task)
            if len(tasks) >= limit:
                break

        return tasks

    def create_task(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> Task:
        """Append a new task as a ``## `` section to the file."""
        prelude, sections = self._load_sections()
        new_id = self._next_id(sections)
        meta: dict[str, str] = {"state": TaskState.OPEN.value}
        if labels:
            meta["labels"] = _join_labels(labels)

        section = _Section(
            id=new_id,
            title=title.strip(),
            meta=meta,
            body=body.rstrip(),
            span=(0, 0),
        )
        sections.append(section)
        self._persist(prelude, sections)

        logger.info("markdown.task_created", task_id=new_id, title=title, path=str(self._path))
        return _section_to_task(section)

    def read_task(self, task_id: str) -> Task:
        _, sections = self._load_sections()
        return _section_to_task(self._find_section(sections, task_id))

    def _is_not_found_error(self, error: Exception) -> bool:
        return isinstance(error, TaskNotFoundError)

    def update_task(
        self,
        task_id: str,
        body: str | None = None,
        title: str | None = None,
    ) -> Task:
        prelude, sections = self._load_sections()
        section = self._find_section(sections, task_id)
        if title is not None:
            section.title = title.strip()
        if body is not None:
            section.body = body.rstrip()
        self._persist(prelude, sections)
        return _section_to_task(section)

    def close_task(self, task_id: str) -> Task:
        prelude, sections = self._load_sections()
        section = self._find_section(sections, task_id)
        section.meta["state"] = TaskState.CLOSED.value
        # Closing implies leaving in-progress.
        labels = _split_labels(section.meta.get("labels", ""))
        labels = [name for name in labels if name != "in-progress"]
        if labels:
            section.meta["labels"] = _join_labels(labels)
        else:
            section.meta.pop("labels", None)
        self._persist(prelude, sections)
        logger.info("markdown.task_closed", task_id=task_id, path=str(self._path))
        return _section_to_task(section)

    def comment_on_task(self, task_id: str, body: str) -> None:
        prelude, sections = self._load_sections()
        section = self._find_section(sections, task_id)
        timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        comment_block = f"\n\n### Comment — {timestamp}\n\n{body.rstrip()}"
        section.body = (section.body.rstrip() + comment_block).strip()
        self._persist(prelude, sections)

    # --- Label management ---

    def ensure_label(self, label: Label) -> None:
        """Markdown labels are inline strings — nothing to pre-create."""
        return None

    def add_label(self, task_id: str, label_name: str) -> None:
        try:
            prelude, sections = self._load_sections()
            section = self._find_section(sections, task_id)
            labels = _split_labels(section.meta.get("labels", ""))
            if label_name not in labels:
                labels.append(label_name)
                section.meta["labels"] = _join_labels(labels)
                self._persist(prelude, sections)
        except TaskNotFoundError:
            logger.warning("markdown.label_add_failed", task_id=task_id, label=label_name)

    def remove_label(self, task_id: str, label_name: str) -> None:
        try:
            prelude, sections = self._load_sections()
            section = self._find_section(sections, task_id)
            labels = _split_labels(section.meta.get("labels", ""))
            if label_name in labels:
                labels = [name for name in labels if name != label_name]
                if labels:
                    section.meta["labels"] = _join_labels(labels)
                else:
                    section.meta.pop("labels", None)
                self._persist(prelude, sections)
        except TaskNotFoundError:
            logger.warning("markdown.label_remove_failed", task_id=task_id, label=label_name)

    # --- Project board operations ---

    def move_to_in_progress(self, task_id: str) -> bool:
        try:
            prelude, sections = self._load_sections()
            section = self._find_section(sections, task_id)
            section.meta["state"] = TaskState.IN_PROGRESS.value
            self._persist(prelude, sections)
            logger.info("markdown.moved_to_in_progress", task_id=task_id)
            return True
        except TaskNotFoundError:
            return False
