"""Batch review domain models — context for post-batch coherence review."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BatchIssueContext(BaseModel):
    """Context for a single issue within a batch review."""

    issue_number: str
    issue_title: str
    branch_name: str | None = None
    local_ref_exists: bool = True
    pr_number: int | None = None
    pr_url: str | None = None
    diff_stat: str = ""
    merged: bool = False
    conflict: bool = False
    status: str = ""
    base_branch: str | None = None


class BatchReviewContext(BaseModel):
    """Aggregated context for a batch coherence review."""

    issues: list[BatchIssueContext] = Field(default_factory=list)
    main_branch: str = "main"
    tracking_issue: str | None = None
    integration_branch: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    chains: list[list[str]] = Field(default_factory=list)
