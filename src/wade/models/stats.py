"""Local usage-statistics models — AI sessions, lifecycle events, PRs, reports.

Pure data (leaf layer). The SQLite persistence lives in ``wade.stats.store``;
aggregation and insights in ``wade.stats.report`` / ``wade.stats.insights``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return an aware UTC timestamp truncated to whole seconds."""
    return datetime.now(UTC).replace(microsecond=0)


class StatsSessionKind(StrEnum):
    """Which WADE workflow launched an AI tool."""

    PLAN = "plan"
    IMPLEMENT = "implement"
    REVIEW_PR_COMMENTS = "review_pr_comments"
    DELEGATION = "delegation"


class StatsOutcome(StrEnum):
    """How a recorded AI session ended."""

    RUNNING = "running"  # started, never finished (crash, kill, or still running)
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    DETACHED = "detached"  # launched in another terminal; end is not observable
    UNKNOWN = "unknown"  # imported history without an exit status


class StatsSource(StrEnum):
    """Where a session row came from."""

    LOCAL = "local"  # observed by this machine at launch time
    PR_BODY = "pr_body"  # migrated from a PR body's wade usage block
    ISSUE_BODY = "issue_body"  # migrated from a task/issue body's wade usage block


class StatsEventKind(StrEnum):
    """Lifecycle events recorded alongside AI sessions."""

    WORKTREE_CREATED = "worktree_created"
    WORKTREE_REMOVED = "worktree_removed"
    PR_CREATED = "pr_created"
    PR_READY = "pr_ready"
    PR_MERGED = "pr_merged"
    TASK_CREATED = "task_created"


class ModelUsageRow(BaseModel):
    """Per-model token counts inside one AI session."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    premium_requests: int = 0


class ProjectRef(BaseModel):
    """A project as the stats store identifies it."""

    root: str
    slug: str | None = None

    @property
    def label(self) -> str:
        return self.slug or self.root


class AISessionRecord(BaseModel):
    """One AI-tool launch observed (or imported) by WADE."""

    id: str
    project: ProjectRef
    kind: StatsSessionKind
    operation: str | None = None  # delegation operation, e.g. "code_review"
    source: StatsSource = StatsSource.LOCAL
    task_id: str | None = None
    branch: str | None = None
    worktree: str | None = None
    pr_number: int | None = None
    ai_tool: str | None = None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    launch_mode: str | None = None  # inline | detached | headless | interactive | resume
    resumed: bool = False
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    exit_code: int | None = None
    outcome: StatsOutcome = StatsOutcome.RUNNING
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    total_tokens: int | None = None
    premium_requests: int | None = None
    tool_session_id: str | None = None
    wade_version: str | None = None
    dedupe_key: str | None = None
    models: list[ModelUsageRow] = Field(default_factory=list)

    @property
    def duration_s(self) -> float | None:
        if self.ended_at is None:
            return None
        return max(0.0, (self.ended_at - self.started_at).total_seconds())

    @property
    def effective_model(self) -> str | None:
        """Requested model, else the dominant model in the breakdown."""
        if self.model:
            return self.model
        if self.models:
            return max(self.models, key=lambda m: m.input_tokens + m.output_tokens).model
        return None


class StatsEvent(BaseModel):
    """A workflow lifecycle event."""

    kind: StatsEventKind
    project: ProjectRef
    ts: datetime = Field(default_factory=utc_now)
    task_id: str | None = None
    pr_number: int | None = None
    branch: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class PullRequestSnapshot(BaseModel):
    """A PR as fetched from GitHub for statistics (``gh pr list --json``)."""

    number: int
    title: str = ""
    url: str = ""
    head_ref: str = ""
    base_ref: str = ""
    state: str = ""
    is_draft: bool = False
    created_at: datetime | None = None
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = None
    body: str = ""


class PullRequestRecord(BaseModel):
    """A PR as stored in the stats database."""

    project: ProjectRef
    number: int
    title: str = ""
    url: str = ""
    branch: str = ""
    state: str = ""
    is_draft: bool = False
    task_id: str | None = None
    wade_managed: bool = False
    created_at: datetime | None = None
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = None

    @property
    def lead_time_h(self) -> float | None:
        if self.created_at is None or self.merged_at is None:
            return None
        return max(0.0, (self.merged_at - self.created_at).total_seconds() / 3600)


class TaskRecord(BaseModel):
    """A task (GitHub issue / ClickUp task / Markdown entry) as stored for stats.

    ``planned_by`` & co. come from WADE's ``planned-by:<tool>`` /
    ``implemented-model:<model>`` labels, so tool attribution survives even for
    tasks whose bodies never carried usage tables.
    """

    project: ProjectRef
    task_id: str
    title: str = ""
    url: str | None = None
    state: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    labels: list[str] = Field(default_factory=list)
    planned_by: str | None = None
    planned_model: str | None = None
    implemented_by: str | None = None
    implemented_model: str | None = None
    review_addressed_by: str | None = None
    review_addressed_model: str | None = None
    wade_managed: bool = False


# ---------------------------------------------------------------------------
# Query + report models
# ---------------------------------------------------------------------------


class StatsFilter(BaseModel):
    """Slice applied to every query. Empty lists mean "no restriction"."""

    since: datetime | None = None
    until: datetime | None = None
    projects: list[str] = Field(default_factory=list)  # slug or root
    tools: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)
    task_id: str | None = None


class UsageTotals(BaseModel):
    """Aggregate metrics over a set of sessions.

    ``total_tokens`` is always ``input + output + cached`` (tools disagree on
    what their own "total" means). ``fresh_tokens`` is ``input + output`` —
    the comparable measure across tools, because not every transcript reports
    cache reads and cache reads are billed at a fraction of fresh input.
    """

    sessions: int = 0
    finished: int = 0
    failures: int = 0
    local_sessions: int = 0  # recorded at launch (vs migrated from GitHub)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    fresh_tokens: int = 0
    premium_requests: int = 0
    cost_usd: float = 0.0
    priced_tokens: int = 0  # tokens covered by a known price
    priced_fresh_tokens: int = 0
    cache_prompt_tokens: int = 0  # input + cached of sessions that reported cache reads
    duration_s: float = 0.0
    timed_sessions: int = 0  # sessions contributing to duration_s

    @property
    def cache_ratio(self) -> float | None:
        """Share of prompt-side tokens served from cache.

        ``None`` when no cache reads were reported at all: agentic CLIs always
        hit the prompt cache, so zero means the transcript didn't report it,
        not that the cache went unused.
        """
        prompt = self.input_tokens + self.cached_tokens
        # Only trust the ratio when sessions that reported cache reads carry at
        # least half of the prompt volume (a handful of stray rows don't count).
        if not self.cached_tokens or self.cache_prompt_tokens * 2 < prompt:
            return None
        return self.cached_tokens / self.cache_prompt_tokens

    @property
    def failure_rate(self) -> float | None:
        return self.failures / self.finished if self.finished else None

    @property
    def avg_duration_s(self) -> float | None:
        return self.duration_s / self.timed_sessions if self.timed_sessions else None

    @property
    def price_coverage(self) -> float | None:
        """Share of fresh tokens (the cost drivers) covered by a known price."""
        return self.priced_fresh_tokens / self.fresh_tokens if self.fresh_tokens else None

    def as_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        data.update(
            cache_ratio=self.cache_ratio,
            failure_rate=self.failure_rate,
            avg_duration_s=self.avg_duration_s,
            price_coverage=self.price_coverage,
        )
        return data


class BreakdownRow(BaseModel):
    """Totals for one value of a dimension (tool, model, kind, project, task)."""

    key: str
    totals: UsageTotals = Field(default_factory=UsageTotals)


class DailyPoint(BaseModel):
    """Per-day totals split by a series dimension (AI tool)."""

    day: str  # YYYY-MM-DD (UTC)
    tokens: dict[str, int] = Field(default_factory=dict)  # fresh tokens per tool
    sessions: int = 0
    cost_usd: float = 0.0


class PRFlowSummary(BaseModel):
    """Pull-request flow metrics over the filtered window."""

    opened: int = 0
    merged: int = 0
    closed_unmerged: int = 0
    open: int = 0
    wade_opened: int = 0
    wade_merged: int = 0
    median_lead_time_h: float | None = None
    median_cycle_time_h: float | None = None  # task created → PR merged
    wade_median_lead_time_h: float | None = None
    other_median_lead_time_h: float | None = None
    median_changed_lines: float | None = None
    tokens_per_merged_pr: float | None = None
    cost_per_merged_pr: float | None = None
    avg_review_sessions_per_pr: float | None = None


class TaskRow(BaseModel):
    """Per-task rollup: what one task cost across plan/implement/review."""

    task_id: str
    project: str
    totals: UsageTotals = Field(default_factory=UsageTotals)
    by_kind_tokens: dict[str, int] = Field(default_factory=dict)  # fresh tokens per kind
    review_sessions: int = 0
    pr_numbers: list[int] = Field(default_factory=list)
    merged: bool = False


class InsightLevel(StrEnum):
    INFO = "info"
    GOOD = "good"
    WARNING = "warning"


class Insight(BaseModel):
    """A short, data-backed observation surfaced in the CLI and dashboard."""

    level: InsightLevel = InsightLevel.INFO
    title: str
    detail: str


class StatsReport(BaseModel):
    """Everything the CLI summary and the dashboard render for one filter."""

    generated_at: datetime = Field(default_factory=utc_now)
    filter: StatsFilter = Field(default_factory=StatsFilter)
    totals: UsageTotals = Field(default_factory=UsageTotals)
    previous_totals: UsageTotals | None = None  # same-length window before `since`
    by_tool: list[BreakdownRow] = Field(default_factory=list)
    by_model: list[BreakdownRow] = Field(default_factory=list)
    by_kind: list[BreakdownRow] = Field(default_factory=list)
    by_project: list[BreakdownRow] = Field(default_factory=list)
    daily: list[DailyPoint] = Field(default_factory=list)
    top_tasks: list[TaskRow] = Field(default_factory=list)
    tasks_created: int = 0
    tasks_closed: int = 0
    prs: PRFlowSummary = Field(default_factory=PRFlowSummary)
    unpriced_models: list[str] = Field(default_factory=list)
    insights: list[Insight] = Field(default_factory=list)
