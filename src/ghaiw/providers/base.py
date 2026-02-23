"""Abstract base class for task backend providers.

Each provider wraps a project management system (GitHub Issues, Linear, etc.)
and exposes a uniform interface for task CRUD, label management, and PR ops.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ghaiw.models.task import Label, Task, TaskState


class AbstractTaskProvider(ABC):
    """Base for all task backends.

    Only GitHub is implemented initially.
    Future: Linear, Asana, Trello, ClickUp, Jira.
    """

    # --- Issue CRUD ---

    @abstractmethod
    def list_tasks(
        self,
        label: str | None = None,
        state: TaskState = TaskState.OPEN,
        limit: int = 50,
        exclude_labels: list[str] | None = None,
    ) -> list[Task]:
        """List tasks matching the given filters."""

    @abstractmethod
    def create_task(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> Task:
        """Create a new task. Returns the created task with its ID and URL."""

    @abstractmethod
    def read_task(self, task_id: str) -> Task:
        """Read a single task by its ID (e.g., issue number)."""

    @abstractmethod
    def update_task(
        self,
        task_id: str,
        body: str | None = None,
        title: str | None = None,
    ) -> Task:
        """Update task title and/or body."""

    @abstractmethod
    def close_task(self, task_id: str) -> Task:
        """Close a task."""

    @abstractmethod
    def comment_on_task(self, task_id: str, body: str) -> None:
        """Add a comment to a task."""

    # --- Label management ---

    @abstractmethod
    def ensure_label(self, label: Label) -> None:
        """Ensure a label exists, creating it if needed."""

    @abstractmethod
    def add_label(self, task_id: str, label_name: str) -> None:
        """Add a label to a task (non-fatal on failure)."""

    @abstractmethod
    def remove_label(self, task_id: str, label_name: str) -> None:
        """Remove a label from a task (non-fatal on failure)."""

    # --- Snapshot/diff for AI session detection ---

    @abstractmethod
    def snapshot_task_numbers(
        self,
        label: str | None = None,
        state: TaskState = TaskState.OPEN,
    ) -> set[str]:
        """Get current task IDs (for pre/post AI session diff)."""

    # --- PR operations (optional — not all providers have PRs) ---

    def create_pr(
        self,
        title: str,
        body: str,
        base_branch: str,
        draft: bool = False,
    ) -> str:
        """Create a pull request. Returns the PR URL."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support pull requests"
        )

    def merge_pr(
        self,
        pr_number: str,
        strategy: str = "squash",
        delete_branch: bool = True,
    ) -> None:
        """Merge a pull request."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support pull requests"
        )

    def get_pr_for_branch(self, branch: str) -> dict[str, Any] | None:
        """Get PR info for a branch. Returns dict with number/body or None."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support pull requests"
        )

    def update_pr_body(self, pr_number: str, body: str) -> None:
        """Update a PR's body text."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support pull requests"
        )

    # --- Repository info ---

    def get_repo_nwo(self) -> str:
        """Get the repo name-with-owner (e.g., 'owner/repo')."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support repo info"
        )

    # --- Parent issue detection ---

    def find_parent_issue(
        self, task_id: str, label: str | None = None
    ) -> str | None:
        """Find parent/tracking issue containing task_id in a checklist.

        Returns the parent issue number or None.
        """
        return None

    # --- Project board operations ---

    def move_to_in_progress(self, task_id: str) -> bool:
        """Move a task to 'In Progress' on a project board.

        Returns True if successful, False otherwise.
        Not all providers support project boards.
        """
        return False
