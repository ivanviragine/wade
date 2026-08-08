"""Markdown provider — read/write tasks from a single central markdown file.

The provider treats one markdown file as the source of truth for issues.
Each issue is a ``## #<id> <title>`` heading followed by an optional metadata
HTML comment and a body. PRs continue to be managed by the regular GitHub
flow (``git/pr.py``); PR-review thread / comment APIs are delegated to an
internal :class:`GitHubProvider` so review automation works the same as it
does for the GitHub Issues provider.

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

import contextlib
import functools
import os
import re
import secrets
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, ConfigDict

from wade.models.config import ProviderConfig
from wade.models.task import (
    CloseReason,
    Label,
    Task,
    TaskState,
    parse_complexity_from_body,
    parse_complexity_from_labels,
    parse_tracking_child_ids,
)
from wade.providers._pr_delegate import GitHubPRDelegateMixin
from wade.providers.base import AbstractTaskProvider
from wade.utils.filelock import file_lock
from wade.utils.process import run

if TYPE_CHECKING:
    from wade.providers.github import GitHubProvider

logger = structlog.get_logger()

DEFAULT_FILE_NAME = "ISSUES.md"
DEFAULT_FILE_HEADER = "# Wade Issues\n\n<!-- Managed by the Wade markdown issue provider. -->\n"

# Heading like "## #42 Title", "## #42: Title", or "## #42 - Title".
# The ID is restricted to digits so the regex doesn't accidentally match
# regular markdown anchor headings like ``## #disclaimer Some text``, and
# so IDs round-trip through ``int()`` / wade's ``#\d+`` parsing.
# Em (U+2014) and en (U+2013) dashes are accepted as title separators.
_HEADING_RE = re.compile(
    "^##\\s+#(?P<id>\\d+)\\s*(?:[:\\-–—]\\s*)?(?P<title>.*?)\\s*$",  # noqa: RUF001
    re.MULTILINE,
)

# Metadata HTML comment block: <!-- wade ... -->
_META_RE = re.compile(r"<!--\s*wade\s*\n(?P<body>.*?)\n\s*-->", re.DOTALL)

_VALID_STATES = frozenset(s.value for s in TaskState)


class MarkdownProviderError(Exception):
    """Errors raised by MarkdownIssueProvider for non-recoverable conditions."""


class TaskNotFoundError(MarkdownProviderError):
    """Raised when a task ID does not exist in the markdown file."""


class _Section(BaseModel):
    """A parsed issue section from the markdown file.

    ``meta`` and ``body`` are kept separate so we can rewrite metadata
    without touching the user-authored body.
    """

    # Section is mutated in place during read-modify-write (title, body,
    # meta), and the schema is internal — keep validation light.
    model_config = ConfigDict(validate_assignment=False, frozen=False)

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
    """Format a metadata dict as a ``<!-- wade ... -->`` HTML comment.

    Wade-managed keys (``state``, ``labels``) are emitted first in canonical
    order. Any additional keys present in ``meta`` are passed through after
    them, preserving user-added metadata like ``priority`` or ``owner`` that
    wade doesn't interpret but shouldn't silently drop on write.
    """
    lines = ["<!-- wade"]
    # Always emit state with a sensible default.
    lines.append(f"state: {meta.get('state') or TaskState.OPEN.value}")
    labels = meta.get("labels", "")
    if labels:
        lines.append(f"labels: {labels}")
    # Pass through anything else the user added.
    for key, value in meta.items():
        if key in ("state", "labels"):
            continue
        if value:
            lines.append(f"{key}: {value}")
    lines.append("-->")
    return "\n".join(lines)


def _coerce_bool(value: object) -> bool:
    """Coerce a settings value (string or bool) to a Python bool.

    ``ProviderConfig.settings`` is typed ``dict[str, str]``, so YAML
    booleans usually arrive as strings like ``"true"`` after Pydantic
    coercion. But upstream callers (tests, programmatic config) may pass
    real ``bool``s — accept either form to avoid an ``AttributeError`` on
    ``.lower()``.
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


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
    # Accept ``in-progress`` (dash) as a synonym for ``in_progress`` so a
    # hand-edited file doesn't silently downgrade to OPEN.
    state_str = (section.meta.get("state") or "open").lower().replace("-", "_")
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


@functools.cache
def _resolve_main_worktree(start: Path) -> Path | None:
    """Return the main worktree path for the git repo containing ``start``.

    From a linked worktree, returns the primary checkout (so all worktrees
    point to the same ``ISSUES.md``). From the main checkout, returns its
    own root. Returns ``None`` if ``start`` isn't in a working-tree git repo
    (not in a repo, in a bare repo, or in a submodule's .git/modules tree)
    — callers fall back to ``project_root``.

    Cached because the result is stable per-path within a process and
    ``get_provider(config)`` is hot. The cache is keyed by the absolute,
    resolved ``start`` path; callers should pass already-resolved paths.
    """
    common = run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=start,
        check=False,
    )
    if common.returncode != 0:
        return None
    common_dir = Path(common.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = (start / common_dir).resolve()
    candidate = common_dir.parent
    # Submodules report `.git/modules/<sub>` as the common dir, whose parent
    # is `.git/modules/` — not a working tree. Heuristic: only accept the
    # candidate if it looks like a checkout (has a .git entry of its own).
    if not (candidate / ".git").exists():
        return None
    return candidate


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically (write tmp + rename).

    Preserves the destination's existing permission bits across writes —
    ``tempfile.mkstemp`` defaults to ``0600``, which would otherwise leak
    through ``os.replace`` and silently strip group/other read on every
    ``ISSUES.md`` mutation (showing up as spurious permission churn in
    ``git status``). For brand-new files we use ``0644`` to match the
    convention for tracked text files.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    target_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(tmp_name, target_mode)
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


class MarkdownIssueProvider(GitHubPRDelegateMixin, AbstractTaskProvider):
    """Task provider backed by a single central markdown file.

    Configuration (``provider.settings`` in ``.wade.yml``):
        path: Relative or absolute path to the markdown file.
              Relative paths resolve against the **main worktree** (the
              repo's primary checkout) — not whatever worktree wade was
              invoked from. This gives every worktree a single source of
              truth for issues, so parallel ``create_task`` calls from
              different branches can't produce ID collisions or merge
              conflicts on ``ISSUES.md``. Defaults to ``ISSUES.md``.

    Task IDs are random 8-digit decimal strings (e.g. ``47239185``)
    rather than sequential integers, to avoid collisions when multiple
    worktrees create issues independently. They stay numeric (not hex)
    so wade's existing ``#\\d+`` parsing — checklist refs, dependency
    sections, branch names — keeps working unchanged.
    """

    def __init__(
        self,
        config: ProviderConfig | None = None,
        project_root: Path | None = None,
        github_provider: GitHubProvider | None = None,
    ) -> None:
        """Build a provider rooted at ``project_root`` with PR delegation wired."""
        super().__init__(config)
        self._project_root = project_root or Path.cwd()
        self._path = self._resolve_path()
        self._auto_commit = _coerce_bool(self._config.settings.get("auto_commit", False))
        self._init_pr_delegate(github_provider)

    # --- Path resolution ---

    def _resolve_path(self) -> Path:
        """Resolve the configured ``path`` setting to an absolute file path.

        Relative paths anchor at the main worktree (so every linked
        worktree converges on the same physical ``ISSUES.md``); absolute
        paths are honored verbatim.
        """
        raw = self._config.settings.get("path", DEFAULT_FILE_NAME)
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate
        anchor = _resolve_main_worktree(self._project_root) or self._project_root
        return (anchor / candidate).resolve()

    # --- File I/O ---

    def _read_text(self) -> str:
        """Read the markdown file, returning ``""`` if it doesn't exist yet."""
        if not self._path.exists():
            return ""
        return self._path.read_text(encoding="utf-8")

    def _write_text(self, content: str) -> None:
        """Atomically write ``content`` to the markdown file with a trailing newline."""
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

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        """Acquire an exclusive cross-process lock for the duration of a
        read-modify-write cycle.

        Two worktrees writing concurrently to the same ``ISSUES.md`` could
        each load a snapshot, mutate in memory, and have the second writer
        clobber the first (atomic write protects torn writes, not lost
        updates). :func:`wade.utils.filelock.file_lock` blocks on a sibling
        ``.lock`` file so the load and persist are observed as one atomic unit.
        """
        with file_lock(self._path):
            yield

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
        """Return the section matching ``task_id`` or raise ``TaskNotFoundError``."""
        for section in sections:
            if section.id == task_id:
                return section
        raise TaskNotFoundError(f"Task #{task_id} not found in {self._path}")

    def _generate_id(self, sections: list[_Section]) -> str:
        """Generate a fresh random 8-digit decimal ID, avoiding existing ones.

        Random IDs (vs. sequential integers) prevent collisions when
        multiple worktrees create issues in parallel. We use decimal
        rather than hex so wade's ``#\\d+`` parsing — checklist child
        refs, dependency sections, ``int(issue_number)`` casts in the
        merge flow — keeps working unchanged. The ID space is ~90M
        (10^7 .. 10^8 - 1, always exactly 8 digits); collisions inside
        a single project are astronomically unlikely, but we retry on
        the off chance.
        """
        existing = {section.id for section in sections}
        for _ in range(50):
            candidate = str(secrets.randbelow(9 * 10**7) + 10**7)
            if candidate not in existing:
                return candidate
        raise RuntimeError("Could not generate a unique markdown task ID")

    # --- Issue CRUD ---

    def list_tasks(
        self,
        label: str | None = None,
        state: TaskState | None = TaskState.OPEN,
        limit: int = 50,
        exclude_labels: list[str] | None = None,
    ) -> list[Task]:
        """List tasks from the markdown file with optional filtering."""
        if limit <= 0:
            return []
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
        with self._lock():
            prelude, sections = self._load_sections()
            new_id = self._generate_id(sections)
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
        """Return the task with the given ID. Raises ``TaskNotFoundError`` if absent."""
        _, sections = self._load_sections()
        return _section_to_task(self._find_section(sections, task_id))

    def _is_not_found_error(self, error: Exception) -> bool:
        """Tell ``read_task_or_none`` that ``TaskNotFoundError`` means "not found"."""
        return isinstance(error, TaskNotFoundError)

    def update_task(
        self,
        task_id: str,
        body: str | None = None,
        title: str | None = None,
    ) -> Task:
        """Rewrite the section's title and/or body, preserving everything else."""
        with self._lock():
            prelude, sections = self._load_sections()
            section = self._find_section(sections, task_id)
            if title is not None:
                section.title = title.strip()
            if body is not None:
                section.body = body.rstrip()
            self._persist(prelude, sections)
        return _section_to_task(section)

    def close_task(self, task_id: str, reason: CloseReason | None = None) -> Task:
        """Mark a task ``closed`` and strip the in-progress label.

        If ``auto_commit`` is set in provider settings, commits the change
        to git so the merge flow doesn't leave the working tree dirty.

        The markdown provider has no close-reason concept — ``reason`` is
        accepted for interface parity with other providers and ignored.
        """
        with self._lock():
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
        if self._auto_commit:
            self._git_commit_close(task_id)
        return _section_to_task(section)

    def _git_commit_close(self, task_id: str) -> None:
        """Stage and commit the ISSUES.md change announcing a task close.

        Non-fatal: any failure (not a git repo, file untracked, hook
        rejection, signing failure) is logged at WARNING and swallowed —
        the close itself already succeeded on disk.
        """
        cwd = self._path.parent
        try:
            add = run(
                ["git", "add", str(self._path)],
                cwd=cwd,
                check=False,
            )
            if add.returncode != 0:
                logger.warning(
                    "markdown.auto_commit_add_failed",
                    task_id=task_id,
                    stderr=add.stderr.strip() if add.stderr else "",
                )
                return
            commit = run(
                ["git", "commit", "-m", f"chore: close #{task_id}"],
                cwd=cwd,
                check=False,
            )
            if commit.returncode != 0:
                logger.warning(
                    "markdown.auto_commit_failed",
                    task_id=task_id,
                    stderr=commit.stderr.strip() if commit.stderr else "",
                )
                return
            logger.info("markdown.auto_committed", task_id=task_id)
        except Exception as exc:  # log + continue; close already succeeded on disk
            logger.warning(
                "markdown.auto_commit_exception",
                task_id=task_id,
                error=str(exc),
            )

    def comment_on_task(self, task_id: str, body: str) -> None:
        """Append a timestamped ``### Comment`` block to the task body."""
        with self._lock():
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
        """Add ``label_name`` to the task's labels list. Idempotent; non-fatal on missing task."""
        try:
            with self._lock():
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
        """Remove ``label_name`` from the task's labels list. Non-fatal on missing task."""
        try:
            with self._lock():
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

    # --- Parent / tracking-issue detection ---

    def find_parent_issue(self, task_id: str, label: str | None = None) -> str | None:
        """Locate the tracking issue that lists ``task_id`` as a child.

        Scans every section's body for checklist refs of the form
        ``- [ ] #<id>`` (checked or unchecked) and returns the first
        matching section's id. Optionally filter to sections carrying
        ``label`` (e.g., ``feature-plan``) to avoid spurious matches
        from unrelated issues that happen to reference ``task_id``.
        """
        _, sections = self._load_sections()
        for section in sections:
            if label:
                labels = _split_labels(section.meta.get("labels", ""))
                if label not in labels:
                    continue
            if section.id == task_id:
                continue
            children = parse_tracking_child_ids(section.body, include_checked=True)
            if task_id in children:
                return section.id
        return None

    # --- Project board operations ---

    def move_to_in_progress(self, task_id: str) -> bool:
        """Flip the task's state to ``in_progress``. Returns ``False`` if missing."""
        try:
            with self._lock():
                prelude, sections = self._load_sections()
                section = self._find_section(sections, task_id)
                section.meta["state"] = TaskState.IN_PROGRESS.value
                self._persist(prelude, sections)
            logger.info("markdown.moved_to_in_progress", task_id=task_id)
            return True
        except TaskNotFoundError:
            return False

    # PR-review operations are inherited from GitHubPRDelegateMixin.
