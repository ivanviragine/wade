"""Abstract base class for task backend providers.

Each provider wraps a project management system (GitHub Issues, Linear, etc.)
and exposes a uniform interface for task CRUD, label management, and PR ops.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from wade.models.config import ProviderConfig
from wade.models.review import PRReviewStatus, ReviewThread
from wade.models.task import Label, Task, TaskState


class AbstractTaskProvider(ABC):
    """Base for all task backends.

    Each concrete provider wraps a project management system
    (GitHub Issues, ClickUp, Jira, Linear, etc.) and exposes
    a uniform interface for task CRUD, label management, and PR ops.
    """

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self._config = config or ProviderConfig()

    # --- Issue CRUD ---

    @abstractmethod
    def list_tasks(
        self,
        label: str | None = None,
        state: TaskState | None = TaskState.OPEN,
        limit: int = 50,
        exclude_labels: list[str] | None = None,
    ) -> list[Task]:
        """List tasks matching the given filters. Pass state=None to list all states."""

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

    def _is_not_found_error(self, error: Exception) -> bool:
        """Override in concrete providers to detect provider-specific not-found errors.

        Returns True if the error indicates the task was not found (e.g., deleted issue).
        Returns False for other errors (auth, network, etc.) which will be re-raised.
        """
        return False

    def read_task_or_none(self, task_id: str) -> Task | None:
        """Read a single task by its ID, returning None if not found.

        Unlike read_task(), this method returns None only for explicit "not found"
        conditions (e.g., deleted issue). Other exceptions (auth, network, server)
        are re-raised to avoid masking real failures.

        Subclasses should override _is_not_found_error() to detect provider-specific
        not-found conditions. Subclasses may also override this method entirely for
        more efficient error handling (e.g., using check=False in subprocess calls).
        """
        try:
            return self.read_task(task_id)
        except Exception as e:
            if self._is_not_found_error(e):
                return None
            raise

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

    # --- PR review operations (optional) ---

    def get_pr_review_threads(
        self,
        pr_number: int,
    ) -> list[ReviewThread]:
        """Fetch PR review threads. Returns empty list by default."""
        raise NotImplementedError(f"{type(self).__name__} does not support review threads")

    def resolve_review_thread(self, thread_id: str) -> bool:
        """Mark a PR review thread as resolved. Returns True on success."""
        raise NotImplementedError(f"{type(self).__name__} does not support resolving threads")

    def get_pr_issue_comments(
        self,
        pr_number: int,
    ) -> list[dict[str, str]]:
        """Fetch PR issue comments. Returns list of dicts with login/body keys."""
        return []

    def get_pr_review_status(
        self,
        pr_number: int,
    ) -> PRReviewStatus:
        """Fetch comprehensive PR review status in a single call.

        Returns a :class:`PRReviewStatus` combining inline threads, PR-level
        review submissions, pending reviewer assignments, and bot status.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support comprehensive review status"
        )

    # --- Repository info ---

    def get_repo_nwo(self) -> str:
        """Get the repo name-with-owner (e.g., 'owner/repo')."""
        raise NotImplementedError(f"{type(self).__name__} does not support repo info")

    # --- Parent issue detection ---

    def find_parent_issue(self, task_id: str, label: str | None = None) -> str | None:
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
