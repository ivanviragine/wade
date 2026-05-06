"""ClickUp provider — wraps the ClickUp REST API v2 for task and tag operations.

All ClickUp interactions go through the HTTP API using an API token
for authentication. Tags are used as the label equivalent.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import structlog

from wade.models.config import ProviderConfig
from wade.models.task import (
    Label,
    Task,
    TaskState,
    parse_complexity_from_body,
    parse_complexity_from_labels,
)
from wade.providers._pr_delegate import GitHubPRDelegateMixin
from wade.providers.base import AbstractTaskProvider
from wade.utils.http import APIError, HTTPClient

logger = structlog.get_logger()

# ClickUp statuses that map to "closed"
_CLOSED_STATUSES = frozenset({"closed", "complete", "done"})


def _epoch_ms_to_datetime(ms: str | None) -> datetime | None:
    """Convert a ClickUp epoch-millisecond string to a datetime."""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)
    except (ValueError, TypeError, OverflowError):
        return None


def _parse_clickup_task(raw: dict[str, Any]) -> Task:
    """Convert a ClickUp task JSON object to a WADE Task model."""
    status_obj = raw.get("status") or {}
    status_str = (status_obj.get("status") or "").lower()
    state = TaskState.CLOSED if status_str in _CLOSED_STATUSES else TaskState.OPEN

    tags: list[Label] = []
    for tag in raw.get("tags") or []:
        color = (tag.get("tag_bg") or "ededed").lstrip("#")
        tags.append(Label(name=tag.get("name", ""), color=color))

    body = raw.get("markdown_description") or raw.get("description") or ""

    return Task(
        id=str(raw.get("id", "")),
        title=raw.get("name", ""),
        body=body,
        state=state,
        complexity=parse_complexity_from_labels(tags) or parse_complexity_from_body(body),
        labels=tags,
        url=raw.get("url", ""),
        created_at=_epoch_ms_to_datetime(raw.get("date_created")),
        updated_at=_epoch_ms_to_datetime(raw.get("date_updated")),
    )


class ClickUpProvider(GitHubPRDelegateMixin, AbstractTaskProvider):
    """ClickUp tasks + tags via REST API v2.

    PRs continue to flow through GitHub, so PR-review thread / comment APIs
    are delegated to an inner GitHubProvider via :class:`GitHubPRDelegateMixin`.
    """

    def __init__(
        self,
        config: ProviderConfig | None = None,
        github_provider: AbstractTaskProvider | None = None,
    ) -> None:
        super().__init__(config)

        token_env = self._config.api_token_env or "CLICKUP_API_TOKEN"
        api_key = os.environ.get(token_env, "")
        if not api_key:
            raise ValueError(
                f"ClickUp API token not found. Set the {token_env} environment variable."
            )

        self._list_id = self._config.settings.get("list_id", "")
        self._team_id = self._config.settings.get("team_id", "")
        if not self._list_id or not self._team_id:
            raise ValueError(
                "ClickUp provider requires 'list_id' and 'team_id' in provider.settings"
            )

        self._client = HTTPClient(
            base_url="https://api.clickup.com",
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
        )
        self._space_id: str | None = None
        self._init_pr_delegate(github_provider)

    # --- Issue CRUD ---

    # ClickUp returns at most 100 tasks per page
    _PAGE_SIZE = 100

    def list_tasks(
        self,
        label: str | None = None,
        state: TaskState | None = TaskState.OPEN,
        limit: int = 50,
        exclude_labels: list[str] | None = None,
    ) -> list[Task]:
        """List tasks using the filtered team tasks endpoint."""
        tasks: list[Task] = []
        page = 0

        while len(tasks) < limit:
            params: dict[str, Any] = {
                "page": str(page),
                "subtasks": "false",
                "include_closed": "true" if state is None or state == TaskState.CLOSED else "false",
            }

            if label:
                params["tags[]"] = label

            # Filter to the configured list
            params["list_ids[]"] = self._list_id

            data = self._client.get(
                f"/api/v2/team/{self._team_id}/task",
                params=params,
            )

            raw_tasks: list[dict[str, Any]] = data.get("tasks", [])
            if not raw_tasks:
                break

            for raw in raw_tasks:
                task = _parse_clickup_task(raw)

                # Apply state filter
                if state is not None and task.state != state:
                    continue

                # Apply exclude_labels filter
                if exclude_labels:
                    task_label_names = {lbl.name for lbl in task.labels}
                    if task_label_names & set(exclude_labels):
                        continue

                tasks.append(task)
                if len(tasks) >= limit:
                    break

            # Stop if this was the last page (fewer results than page size)
            if len(raw_tasks) < self._PAGE_SIZE:
                break

            page += 1

        return tasks

    def create_task(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> Task:
        """Create a task in the configured ClickUp list."""
        payload: dict[str, Any] = {
            "name": title,
            "markdown_description": body,
        }
        if labels:
            payload["tags"] = labels

        data = self._client.post(
            f"/api/v2/list/{self._list_id}/task",
            json=payload,
        )

        task = _parse_clickup_task(data)
        logger.info("clickup.task_created", task_id=task.id, title=title)
        return task

    def read_task(self, task_id: str) -> Task:
        """Read a single task by its ID."""
        data = self._client.get(
            f"/api/v2/task/{task_id}",
            params={"include_markdown_description": "true"},
        )
        return _parse_clickup_task(data)

    def update_task(
        self,
        task_id: str,
        body: str | None = None,
        title: str | None = None,
    ) -> Task:
        """Update a task's title and/or body."""
        payload: dict[str, Any] = {}
        if body is not None:
            payload["markdown_description"] = body
        if title is not None:
            payload["name"] = title

        if payload:
            self._client.put(f"/api/v2/task/{task_id}", json=payload)

        return self.read_task(task_id)

    def close_task(self, task_id: str) -> Task:
        """Close a task by setting its status to 'closed'."""
        self._client.put(
            f"/api/v2/task/{task_id}",
            json={"status": "closed"},
        )
        logger.info("clickup.task_closed", task_id=task_id)
        return self.read_task(task_id)

    def comment_on_task(self, task_id: str, body: str) -> None:
        """Add a comment to a task."""
        self._client.post(
            f"/api/v2/task/{task_id}/comment",
            json={"comment_text": body},
        )

    # --- Label management (via ClickUp tags) ---

    def ensure_label(self, label: Label) -> None:
        """Ensure a tag exists in the space, creating it if needed."""
        space_id = self._resolve_space_id()

        # Check if tag already exists
        try:
            data = self._client.get(f"/api/v2/space/{space_id}/tag")
            existing_tags: list[dict[str, Any]] = data.get("tags", [])
            for tag in existing_tags:
                if tag.get("name") == label.name:
                    return
        except APIError:
            pass  # Search failed — try creating anyway

        # Create the tag
        try:
            self._client.post(
                f"/api/v2/space/{space_id}/tag",
                json={"tag": {"name": label.name, "tag_bg": f"#{label.color}"}},
            )
            logger.info("clickup.tag_created", name=label.name)
        except APIError as e:
            # Duplicate tag is fine — any other 400 should surface
            if e.status_code == 400 and "already exists" in str(e).lower():
                return
            raise

    def add_label(self, task_id: str, label_name: str) -> None:
        """Add a tag to a task (non-fatal on failure)."""
        try:
            self._client.post(f"/api/v2/task/{task_id}/tag/{label_name}", json={})
        except APIError:
            logger.warning(
                "clickup.tag_add_failed",
                task_id=task_id,
                tag=label_name,
            )

    def remove_label(self, task_id: str, label_name: str) -> None:
        """Remove a tag from a task (non-fatal on failure)."""
        try:
            self._client.delete(f"/api/v2/task/{task_id}/tag/{label_name}")
        except APIError:
            logger.warning(
                "clickup.tag_remove_failed",
                task_id=task_id,
                tag=label_name,
            )

    # --- Project board operations ---

    def move_to_in_progress(self, task_id: str) -> bool:
        """Move a task to 'in progress' status."""
        try:
            self._client.put(
                f"/api/v2/task/{task_id}",
                json={"status": "in progress"},
            )
            logger.info("clickup.moved_to_in_progress", task_id=task_id)
            return True
        except APIError:
            return False

    # --- Internal helpers ---

    def _resolve_space_id(self) -> str:
        """Lazily resolve the space ID from the configured list."""
        if self._space_id:
            return self._space_id

        # Check settings first
        space_id = self._config.settings.get("space_id")
        if space_id:
            self._space_id = space_id
            return space_id

        # Derive from list metadata
        data = self._client.get(f"/api/v2/list/{self._list_id}")
        space = data.get("space", {})
        resolved_id = space.get("id", "")
        if not resolved_id:
            raise ValueError(
                f"Could not resolve space_id from list {self._list_id}. "
                "Set 'space_id' in provider.settings."
            )
        self._space_id = str(resolved_id)
        return self._space_id
