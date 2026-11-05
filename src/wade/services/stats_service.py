"""Local usage statistics — recording facade, GitHub backfill, reports, export.

Recording is **best-effort by contract**: no function here may raise into, or
noticeably slow, the workflow that calls it. Every failure is logged at debug
level and swallowed. Recording is skipped entirely when ``WADE_STATS_DISABLE``
is truthy or the project's ``.wade.yml`` sets ``stats.enabled: false``.

Data never leaves the machine: the store is a per-user SQLite file
(``wade.stats.store.default_db_path``).
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel

import wade
from wade.models.stats import (
    AISessionRecord,
    ModelUsageRow,
    ProjectRef,
    PullRequestRecord,
    PullRequestSnapshot,
    StatsEvent,
    StatsEventKind,
    StatsFilter,
    StatsOutcome,
    StatsReport,
    StatsSessionKind,
    StatsSource,
    TaskRecord,
    utc_now,
)
from wade.stats.insights import build_insights
from wade.stats.pricing import PriceBook
from wade.stats.report import build_report, session_cost, session_tokens
from wade.stats.store import StatsStore, default_db_path
from wade.stats.usage_parser import (
    IMPL_USAGE_MARKERS,
    PLAN_USAGE_MARKERS,
    REVIEW_USAGE_MARKERS,
    ParsedUsageSession,
    has_wade_markers,
    parse_usage_block,
    usage_fingerprint,
)

if TYPE_CHECKING:
    from crossby.models.ai import TokenUsage

    from wade.models.config import ProjectConfig
    from wade.models.delegation import DelegationRequest, DelegationResult
    from wade.models.task import Task
    from wade.providers.base import AbstractTaskProvider

logger = structlog.get_logger()

_TRUTHY = {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Enablement + project identity
# ---------------------------------------------------------------------------


def stats_enabled(config: ProjectConfig | None = None) -> bool:
    """Whether *local* recording is allowed (env kill-switch, then ``stats.storage``)."""
    if os.environ.get("WADE_STATS_DISABLE", "").strip().lower() in _TRUTHY:
        return False
    return config is None or config.stats.records_local


def github_usage_enabled(config: ProjectConfig | None) -> bool:
    """Whether usage tables go into PR/issue bodies (``stats.storage``)."""
    return config is None or config.stats.writes_github


_SLUG_RE = re.compile(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$")


def _slug_from_remote(url: str | None) -> str | None:
    if not url:
        return None
    m = _SLUG_RE.search(url.strip())
    return m.group(1) if m else None


@lru_cache(maxsize=64)
def _resolve_project_cached(path: str) -> ProjectRef:
    from wade.git import repo as git_repo

    p = Path(path)
    try:
        root = git_repo.main_checkout_root(git_repo.get_repo_root(p))
    except Exception:
        root = p
    slug = None
    try:
        slug = _slug_from_remote(git_repo.get_remote_url(root))
    except Exception:
        slug = None
    return ProjectRef(root=str(Path(os.path.abspath(root))), slug=slug)


def resolve_project(path: Path) -> ProjectRef:
    """Main-checkout root + ``owner/repo`` slug, so worktrees roll up together."""
    return _resolve_project_cached(str(Path(os.path.abspath(path))))


@contextmanager
def _store(path: Path | None = None) -> Iterator[StatsStore]:
    store = StatsStore(path)
    try:
        yield store
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Session recording
# ---------------------------------------------------------------------------


def usage_fields(usage: TokenUsage | None) -> dict[str, Any]:
    """Map a crossby ``TokenUsage`` onto :class:`AISessionRecord` fields."""
    if usage is None:
        return {}
    from wade.utils.token_usage_markdown import resolve_token_usage_totals

    total, inp, out, cac = resolve_token_usage_totals(
        total_tokens=usage.total_tokens,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens,
        model_breakdown=usage.model_breakdown,
    )
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cached_tokens": cac,
        "total_tokens": total,
        "premium_requests": usage.premium_requests,
        "tool_session_id": usage.session_id,
        "models": [
            ModelUsageRow(
                model=row.model,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cached_tokens=row.cached_tokens,
                premium_requests=row.premium_requests,
            )
            for row in usage.model_breakdown
        ],
    }


class SessionTracker:
    """Handle for one in-flight AI session; every method is a safe no-op on failure.

    The row is inserted at :func:`begin_session` time (so crashed and detached
    launches are still visible, as ``running``/``detached``) and completed by
    :meth:`finish`.
    """

    def __init__(self, record: AISessionRecord | None, db_path: Path | None = None) -> None:
        self._record = record
        self._db_path = db_path
        self._finished = False

    @property
    def active(self) -> bool:
        return self._record is not None and not self._finished

    @property
    def record(self) -> AISessionRecord | None:
        return self._record

    @property
    def db_path(self) -> Path | None:
        return self._db_path

    def set_usage(self, usage: TokenUsage | None) -> None:
        if self._record is None or usage is None:
            return
        try:
            self._record = self._record.model_copy(update=usage_fields(usage))
            if not self._record.model and self._record.models:
                self._record.model = self._record.effective_model
        except Exception:
            logger.debug("stats.set_usage_failed", exc_info=True)

    def set_pr(self, pr_number: int | None) -> None:
        if self._record is not None and pr_number is not None:
            self._record.pr_number = pr_number

    def set_model(self, model: str | None) -> None:
        if self._record is not None and model and not self._record.model:
            self._record.model = model

    def finish(
        self,
        *,
        exit_code: int | None = None,
        outcome: StatsOutcome | None = None,
    ) -> None:
        """Persist the end of the session (idempotent)."""
        if self._record is None or self._finished:
            return
        self._finished = True
        rec = self._record
        try:
            rec.ended_at = utc_now()
            rec.exit_code = exit_code
            if outcome is None:
                if exit_code is None:
                    outcome = StatsOutcome.UNKNOWN
                else:
                    outcome = StatsOutcome.SUCCESS if exit_code == 0 else StatsOutcome.FAILED
            rec.outcome = outcome
            with _store(self._db_path) as store:
                store.finish_session(rec)
        except Exception:
            logger.debug("stats.finish_failed", exc_info=True)

    def detached(self) -> None:
        """Mark a launch handed to another terminal (no observable end)."""
        if self._record is None or self._finished:
            return
        self._finished = True
        try:
            self._record.outcome = StatsOutcome.DETACHED
            with _store(self._db_path) as store:
                store.finish_session(self._record)
        except Exception:
            logger.debug("stats.detach_failed", exc_info=True)


def launch_outcome(launch_completed: bool, exit_code: int | None) -> StatsOutcome | None:
    """Outcome for a launch site's ``finally`` block.

    ``None`` lets :meth:`SessionTracker.finish` derive it from the exit code.
    Call it from inside ``finally``: a propagating ``KeyboardInterrupt`` is
    recorded as a cancellation rather than a failure.
    """
    import sys

    if isinstance(sys.exc_info()[1], KeyboardInterrupt):
        return StatsOutcome.CANCELLED
    if launch_completed:
        return None
    # Launched but the user said the (non-blocking) session isn't finished yet.
    if exit_code is not None:
        return StatsOutcome.UNKNOWN
    return StatsOutcome.FAILED


def begin_session(
    *,
    kind: StatsSessionKind,
    cwd: Path,
    ai_tool: str | None,
    model: str | None = None,
    effort: str | None = None,
    permission_mode: str | None = None,
    task_id: str | None = None,
    branch: str | None = None,
    worktree: Path | None = None,
    launch_mode: str = "inline",
    resumed: bool = False,
    operation: str | None = None,
    config: ProjectConfig | None = None,
    db_path: Path | None = None,
) -> SessionTracker:
    """Record that an AI session is starting; never raises."""
    if not stats_enabled(config):
        return SessionTracker(None)
    try:
        rec = AISessionRecord(
            id=uuid.uuid4().hex,
            project=resolve_project(cwd),
            kind=kind,
            operation=operation,
            source=StatsSource.LOCAL,
            task_id=str(task_id) if task_id is not None else None,
            branch=branch,
            worktree=str(worktree) if worktree is not None else None,
            ai_tool=str(ai_tool) if ai_tool else None,
            model=model,
            effort=str(effort.value if hasattr(effort, "value") else effort) if effort else None,
            permission_mode=str(permission_mode) if permission_mode else None,
            launch_mode=launch_mode,
            resumed=resumed,
            wade_version=wade.__version__,
        )
        with _store(db_path) as store:
            store.insert_session(rec)
        tracker = SessionTracker(rec, db_path)
        if kind is StatsSessionKind.PLAN:
            global _current_plan
            _current_plan = tracker
        return tracker
    except Exception:
        logger.debug("stats.begin_failed", exc_info=True)
        return SessionTracker(None)


def _operation_slug(label: str | None) -> str | None:
    """``"the plan review"`` → ``"plan_review"`` (delegation operation labels)."""
    if not label:
        return None
    text = label.strip().lower().removeprefix("the ").removeprefix("this ")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or None


def record_delegation(
    request: DelegationRequest,
    result: DelegationResult,
    started_at: datetime,
    *,
    db_path: Path | None = None,
) -> None:
    """Record one finished delegated run (headless/interactive review, deps …).

    Delegated runs expose no transcript, so these rows carry duration and
    outcome but no tokens. Never raises.
    """
    if result.never_launched or not stats_enabled():
        return
    try:
        from wade.config.loader import load_config

        cwd = request.cwd or Path.cwd()
        try:
            config: ProjectConfig | None = load_config(cwd)
        except Exception:
            config = None
        if not stats_enabled(config):
            return
        if result.timed_out:
            outcome = StatsOutcome.TIMEOUT
        elif result.cancelled:
            outcome = StatsOutcome.CANCELLED
        elif result.success:
            outcome = StatsOutcome.SUCCESS
        else:
            outcome = StatsOutcome.FAILED
        task_ids = request.knowledge_task_ids
        rec = AISessionRecord(
            id=uuid.uuid4().hex,
            project=resolve_project(cwd),
            kind=StatsSessionKind.DELEGATION,
            operation=_operation_slug(request.operation),
            task_id=task_ids[0] if len(task_ids) == 1 else None,
            ai_tool=request.ai_tool,
            model=request.model,
            effort=request.effort,
            permission_mode=str(request.permission_mode.value),
            launch_mode=str(request.mode.value),
            started_at=started_at,
            ended_at=utc_now(),
            exit_code=result.exit_code,
            outcome=outcome,
            wade_version=wade.__version__,
        )
        with _store(db_path) as store:
            store.insert_session(rec)
    except Exception:
        logger.debug("stats.delegation_failed", exc_info=True)


def record_event(
    kind: StatsEventKind,
    *,
    cwd: Path,
    task_id: str | None = None,
    pr_number: int | None = None,
    branch: str | None = None,
    data: dict[str, Any] | None = None,
    config: ProjectConfig | None = None,
    db_path: Path | None = None,
) -> None:
    """Record a lifecycle event; never raises."""
    if not stats_enabled(config):
        return
    try:
        event = StatsEvent(
            kind=kind,
            project=resolve_project(cwd),
            task_id=str(task_id) if task_id is not None else None,
            pr_number=pr_number,
            branch=branch,
            data=data or {},
        )
        with _store(db_path) as store:
            store.insert_event(event)
    except Exception:
        logger.debug("stats.event_failed", kind=str(kind), exc_info=True)


# ---------------------------------------------------------------------------
# Migration: import everything WADE has written to GitHub PRs and tasks
# ---------------------------------------------------------------------------


class MigrationResult(BaseModel):
    """Counts reported by :func:`migrate_from_github`."""

    project: str
    dry_run: bool = False
    prs_seen: int = 0
    wade_prs: int = 0
    tasks_seen: int = 0
    wade_tasks: int = 0
    sessions_imported: int = 0
    sessions_skipped: int = 0  # already in the database (recorded locally or imported)
    errors: list[str] = []


_LABEL_ATTRS = {
    "planned-by:": "planned_by",
    "planned-model:": "planned_model",
    "implemented-by:": "implemented_by",
    "implemented-model:": "implemented_model",
    "review-addressed-by:": "review_addressed_by",
    "review-addressed-model:": "review_addressed_model",
}


def _label_attribution(labels: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in labels:
        for prefix, attr in _LABEL_ATTRS.items():
            if name.startswith(prefix) and name[len(prefix) :]:
                out[attr] = name[len(prefix) :]
    return out


def _dedupe_scope(kind: StatsSessionKind, task_id: str | None, pr_number: int | None) -> str:
    # One planning session is copied into every issue it created, so planning
    # usage is deduped project-wide; everything else per task (or per PR).
    if kind is StatsSessionKind.PLAN:
        return "plan"
    if task_id:
        return f"task:{task_id}"
    return f"pr:{pr_number}" if pr_number is not None else "-"


def _session_fingerprint(s: AISessionRecord) -> str | None:
    if not any(
        v is not None for v in (s.total_tokens, s.input_tokens, s.output_tokens, s.cached_tokens)
    ):
        return None
    return usage_fingerprint(
        s.kind.value, s.ai_tool, s.input_tokens, s.output_tokens, s.cached_tokens, s.total_tokens
    )


class _Candidate(BaseModel):
    source: StatsSource
    number: str
    kind: StatsSessionKind
    parsed: ParsedUsageSession
    task_id: str | None
    pr_number: int | None
    branch: str | None
    started_at: datetime


def migrate_from_github(
    repo_root: Path,
    *,
    provider: AbstractTaskProvider | None = None,
    branch_prefix: str | None = None,
    issue_label: str | None = None,
    limit: int = 1000,
    include_prs: bool = True,
    include_tasks: bool = True,
    dry_run: bool = False,
    db_path: Path | None = None,
) -> MigrationResult:
    """Import WADE's GitHub-resident history into the local database.

    Reads every PR (via ``gh``) and every task (via the configured provider, so
    ClickUp/Markdown tasks work too) and stores:

    - PR metadata (state, timestamps, size, branch, linked task);
    - task metadata (state, timestamps, labels, planned/implemented/review-by
      tool + model attribution from WADE's labels);
    - one AI session per usage table: implementation and review tables from PR
      bodies, and planning/implementation/review tables from task bodies.

    Idempotent and safe to re-run. A session that appears in several places
    (PR body, task body, recorded locally) is stored once, matched by its usage
    fingerprint; planning usage, copied into every issue of a multi-issue plan,
    is matched project-wide and attributed to the earliest issue. Usage tables
    carry no timestamps, so imported sessions are dated from their PR/task.
    """
    from wade.git import pr as git_pr
    from wade.models.task import TaskState
    from wade.utils.task_identity import task_id_from_branch

    project = resolve_project(repo_root)
    result = MigrationResult(project=project.label, dry_run=dry_run)
    now = utc_now()
    prefix = (branch_prefix or "").strip("/")

    snapshots: list[PullRequestSnapshot] = []
    if include_prs:
        try:
            snapshots = git_pr.list_prs_for_stats(repo_root, limit=limit)
        except Exception as exc:
            result.errors.append(f"pull requests: {exc}")
    tasks: list[Task] = []
    if include_tasks and provider is not None:
        try:
            tasks = provider.list_tasks(state=None, limit=limit)
        except Exception as exc:
            result.errors.append(f"tasks: {exc}")
    result.prs_seen, result.tasks_seen = len(snapshots), len(tasks)
    tasks.sort(key=lambda t: (t.created_at or now, t.id))

    with _store(db_path) as store:
        seen: set[tuple[str, str]] = set()
        for s in store.fetch_sessions(StatsFilter(projects=[project.root])):
            if (fp := _session_fingerprint(s)) is not None:
                seen.add((_dedupe_scope(s.kind, s.task_id, s.pr_number), fp))

        candidates: list[_Candidate] = []
        pr_tasks: set[str] = set()
        for snap in snapshots:
            wade_managed = has_wade_markers(snap.body) or bool(
                prefix and snap.head_ref.startswith(prefix + "/")
            )
            task_id = task_id_from_branch(snap.head_ref) if wade_managed else None
            if wade_managed:
                result.wade_prs += 1
                if task_id:
                    pr_tasks.add(task_id)
            if not dry_run:
                store.upsert_pull_request(
                    PullRequestRecord(
                        project=project,
                        number=snap.number,
                        title=snap.title,
                        url=snap.url,
                        branch=snap.head_ref,
                        state=snap.state,
                        is_draft=snap.is_draft,
                        task_id=task_id,
                        wade_managed=wade_managed,
                        created_at=snap.created_at,
                        merged_at=snap.merged_at,
                        closed_at=snap.closed_at,
                        additions=snap.additions,
                        deletions=snap.deletions,
                        changed_files=snap.changed_files,
                    ),
                    synced_at=now,
                )
            if not wade_managed:
                continue
            for kind, markers in (
                (StatsSessionKind.IMPLEMENT, IMPL_USAGE_MARKERS),
                (StatsSessionKind.REVIEW_PR_COMMENTS, REVIEW_USAGE_MARKERS),
            ):
                ts = snap.created_at or now
                if kind is StatsSessionKind.REVIEW_PR_COMMENTS:
                    ts = snap.merged_at or snap.closed_at or ts
                candidates.extend(
                    _Candidate(
                        source=StatsSource.PR_BODY,
                        number=str(snap.number),
                        kind=kind,
                        parsed=parsed,
                        task_id=task_id,
                        pr_number=snap.number,
                        branch=snap.head_ref,
                        started_at=ts,
                    )
                    for parsed in parse_usage_block(snap.body, markers)
                )

        for task in tasks:
            labels = [lbl.name for lbl in task.labels]
            attribution = _label_attribution(labels)
            wade_managed = (
                has_wade_markers(task.body)
                or bool(attribution)
                or bool(issue_label and issue_label in labels)
            )
            if wade_managed:
                result.wade_tasks += 1
            if not dry_run:
                store.upsert_task(
                    TaskRecord(
                        project=project,
                        task_id=task.id,
                        title=task.title,
                        url=task.url,
                        state=task.state.value
                        if isinstance(task.state, TaskState)
                        else str(task.state),
                        created_at=task.created_at,
                        updated_at=task.updated_at,
                        labels=labels,
                        wade_managed=wade_managed,
                        **attribution,
                    ),
                    synced_at=now,
                )
            created = task.created_at or now
            for kind, markers, ts in (
                (StatsSessionKind.PLAN, PLAN_USAGE_MARKERS, created),
                (StatsSessionKind.IMPLEMENT, IMPL_USAGE_MARKERS, created),
                (
                    StatsSessionKind.REVIEW_PR_COMMENTS,
                    REVIEW_USAGE_MARKERS,
                    task.updated_at or created,
                ),
            ):
                candidates.extend(
                    _Candidate(
                        source=StatsSource.ISSUE_BODY,
                        number=task.id,
                        kind=kind,
                        parsed=parsed,
                        task_id=task.id,
                        pr_number=None,
                        branch=None,
                        started_at=ts,
                    )
                    for parsed in parse_usage_block(task.body, markers)
                )

        for c in candidates:
            p = c.parsed
            if p.has_tokens:
                fp = usage_fingerprint(
                    c.kind.value,
                    p.ai_tool,
                    p.input_tokens,
                    p.output_tokens,
                    p.cached_tokens,
                    p.total_tokens,
                )
                key = (_dedupe_scope(c.kind, c.task_id, c.pr_number), fp)
                if key in seen:
                    result.sessions_skipped += 1
                    continue
                dedupe_key = f"gh:{project.root}:{key[0]}:{fp}"
            else:
                # A token-less row ("unavailable") has no fingerprint; the PR
                # copy is authoritative when the task has a PR.
                if c.source is StatsSource.ISSUE_BODY and c.task_id in pr_tasks:
                    result.sessions_skipped += 1
                    continue
                key = None
                dedupe_key = (
                    f"gh:{project.root}:{c.source.value}:{c.number}:{c.kind.value}:{p.index}"
                )
            rec = AISessionRecord(
                id=uuid.uuid4().hex,
                project=project,
                kind=c.kind,
                source=c.source,
                task_id=c.task_id,
                branch=c.branch,
                pr_number=c.pr_number,
                ai_tool=p.ai_tool,
                model=p.model,
                started_at=c.started_at,
                outcome=StatsOutcome.UNKNOWN,
                input_tokens=p.input_tokens,
                output_tokens=p.output_tokens,
                cached_tokens=p.cached_tokens,
                total_tokens=p.total_tokens,
                premium_requests=p.premium_requests,
                models=p.models,
                dedupe_key=dedupe_key,
            )
            inserted = True if dry_run else store.insert_session(rec)
            if key is not None:
                seen.add(key)
            if inserted:
                result.sessions_imported += 1
            else:
                result.sessions_skipped += 1
    return result


_current_plan: SessionTracker | None = None


def link_plan_tasks(
    task_ids: list[str],
    *,
    cwd: Path,
    config: ProjectConfig | None = None,
    db_path: Path | None = None,
) -> None:
    """Attribute this process's planning session to the tasks it created.

    The session is linked to the first task (a session has one task); every
    created task gets a ``task_created`` event naming the planning session.
    """
    if not task_ids or not stats_enabled(config):
        return
    tracker = _current_plan
    rec = tracker.record if tracker is not None else None
    try:
        if rec is not None:
            with _store(db_path or (tracker.db_path if tracker else None)) as store:
                store.set_session_task(rec.id, str(task_ids[0]))
        for tid in task_ids:
            record_event(
                StatsEventKind.TASK_CREATED,
                cwd=cwd,
                task_id=str(tid),
                data={
                    "plan_session": rec.id if rec else None,
                    "plan_siblings": len(task_ids),
                    "ai_tool": rec.ai_tool if rec else None,
                },
                config=config,
                db_path=db_path,
            )
    except Exception:
        logger.debug("stats.link_plan_tasks_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_REL_RE = re.compile(r"^(\d+)\s*([hdwmy])$")


def parse_time_bound(value: str | None, *, now: datetime | None = None) -> datetime | None:
    """Parse ``7d`` / ``12h`` / ``4w`` / ``3m`` / ``1y`` / ``YYYY-MM-DD`` / ``all``."""
    if value is None:
        return None
    text = value.strip().lower()
    if text in {"", "all", "ever"}:
        return None
    now = now or utc_now()
    if m := _REL_RE.match(text):
        n, unit = int(m.group(1)), m.group(2)
        days = {"h": n / 24, "d": n, "w": n * 7, "m": n * 30, "y": n * 365}[unit]
        return now - timedelta(days=days)
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(
            f"Unrecognized time '{value}' — use e.g. 7d, 12h, 4w, 3m, 2026-01-31 or all"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def build_stats_report(flt: StatsFilter, *, db_path: Path | None = None) -> StatsReport:
    """Aggregate the store for *flt*, including period-over-period and insights."""
    prices = PriceBook.load()
    with _store(db_path) as store:
        sessions = store.fetch_sessions(flt)
        prs = store.fetch_pull_requests(flt)
        tasks = store.fetch_tasks(flt)
        all_tasks = store.fetch_tasks(flt.model_copy(update={"since": None, "until": None}))
        previous = None
        if flt.since is not None:
            end = flt.until or utc_now()
            span = end - flt.since
            prev_flt = flt.model_copy(update={"since": flt.since - span, "until": flt.since})
            previous = store.fetch_sessions(prev_flt)
    report = build_report(sessions, prs, prices, flt, previous, tasks, all_tasks)
    report.insights = build_insights(report)
    return report


def list_sessions(flt: StatsFilter, *, db_path: Path | None = None) -> list[AISessionRecord]:
    with _store(db_path) as store:
        return store.fetch_sessions(flt)


def list_pull_requests(flt: StatsFilter, *, db_path: Path | None = None) -> list[PullRequestRecord]:
    with _store(db_path) as store:
        return store.fetch_pull_requests(flt)


def filter_options(*, db_path: Path | None = None) -> dict[str, list[str]]:
    with _store(db_path) as store:
        return store.distinct_values()


def store_info(*, db_path: Path | None = None) -> dict[str, Any]:
    path = db_path or default_db_path()
    info: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if path.is_file():
        info["size_bytes"] = path.stat().st_size
        with _store(db_path) as store:
            info["counts"] = store.counts()
    return info


def prune(older_than: datetime, *, db_path: Path | None = None) -> int:
    with _store(db_path) as store:
        return store.prune_before(older_than)


EXPORT_COLUMNS = [
    "id",
    "project",
    "kind",
    "operation",
    "source",
    "task_id",
    "pr_number",
    "branch",
    "ai_tool",
    "model",
    "effort",
    "permission_mode",
    "launch_mode",
    "resumed",
    "started_at",
    "ended_at",
    "duration_s",
    "exit_code",
    "outcome",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "total_tokens",
    "premium_requests",
    "cost_usd",
    "wade_version",
]


def session_rows(
    sessions: list[AISessionRecord], prices: PriceBook | None = None
) -> list[dict[str, Any]]:
    """Flatten sessions for tables/CSV/JSON (with resolved tokens and cost)."""
    prices = prices or PriceBook.load()
    rows: list[dict[str, Any]] = []
    for s in sessions:
        inp, out, cac, total = session_tokens(s)
        usd, priced, _ = session_cost(s, prices)
        rows.append(
            {
                "id": s.id,
                "project": s.project.label,
                "kind": s.kind.value,
                "operation": s.operation,
                "source": s.source.value,
                "task_id": s.task_id,
                "pr_number": s.pr_number,
                "branch": s.branch,
                "ai_tool": s.ai_tool,
                "model": s.effective_model,
                "effort": s.effort,
                "permission_mode": s.permission_mode,
                "launch_mode": s.launch_mode,
                "resumed": s.resumed,
                "started_at": s.started_at.isoformat(),
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "duration_s": round(s.duration_s, 1) if s.duration_s is not None else None,
                "exit_code": s.exit_code,
                "outcome": s.outcome.value,
                "input_tokens": inp,
                "output_tokens": out,
                "cached_tokens": cac,
                "total_tokens": total,
                "premium_requests": s.premium_requests,
                "cost_usd": round(usd, 4) if priced else None,
                "wade_version": s.wade_version,
                "models": [m.model_dump() for m in s.models],
            }
        )
    return rows


def export_sessions(flt: StatsFilter, fmt: str = "csv", *, db_path: Path | None = None) -> str:
    """Serialize filtered sessions as ``csv``, ``json`` or ``jsonl``."""
    rows = session_rows(list_sessions(flt, db_path=db_path))
    if fmt == "json":
        return json.dumps(rows, indent=2, default=str)
    if fmt == "jsonl":
        return "".join(json.dumps(r, default=str) + "\n" for r in rows)
    if fmt != "csv":
        raise ValueError(f"Unknown export format '{fmt}' (csv, json, jsonl)")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()
