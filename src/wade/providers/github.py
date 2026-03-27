"""GitHub provider — wraps the gh CLI for issue, label, and PR operations.

All GitHub interactions go through the gh CLI binary, which handles
authentication, pagination, rate limits, and token refresh transparently.
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from wade.models.config import ProviderConfig
from wade.models.review import (
    PendingReviewer,
    PRReview,
    PRReviewStatus,
    ReviewBotStatus,
    ReviewComment,
    ReviewState,
    ReviewThread,
    detect_coderabbit_review_status,
    filter_actionable_threads,
)
from wade.models.task import (
    Label,
    Task,
    TaskState,
    parse_complexity_from_body,
    parse_complexity_from_labels,
    parse_tracking_child_ids,
)
from wade.providers.base import AbstractTaskProvider
from wade.utils.process import CommandError, run

logger = structlog.get_logger()


def _extract_number_from_url(url: str) -> str:
    """Extract the issue/PR number from a GitHub URL.

    GitHub URLs end with the number: https://github.com/owner/repo/issues/42
    """
    match = re.search(r"(\d+)\s*$", url)
    if match:
        return match.group(1)
    raise ValueError(f"Could not extract number from URL: {url}")


def _parse_gh_task(raw: dict[str, Any]) -> Task:
    """Convert a gh JSON object to a Task model."""
    labels = []
    for lbl in raw.get("labels", []):
        if isinstance(lbl, dict):
            labels.append(
                Label(
                    name=lbl.get("name", ""),
                    color=lbl.get("color", "ededed"),
                    description=lbl.get("description", ""),
                )
            )

    state_str = raw.get("state", "OPEN").lower()
    state = TaskState.CLOSED if state_str == "closed" else TaskState.OPEN

    body = raw.get("body", "") or ""

    return Task(
        id=str(raw.get("number", "")),
        title=raw.get("title", ""),
        body=body,
        state=state,
        complexity=parse_complexity_from_labels(labels) or parse_complexity_from_body(body),
        labels=labels,
        url=raw.get("url", ""),
        created_at=raw.get("createdAt"),
        updated_at=raw.get("updatedAt"),
    )


class GitHubProvider(AbstractTaskProvider):
    """GitHub Issues + PRs via the gh CLI."""

    def __init__(self, config: ProviderConfig | None = None) -> None:
        super().__init__(config)

    def list_tasks(
        self,
        label: str | None = None,
        state: TaskState | None = TaskState.OPEN,
        limit: int = 50,
        exclude_labels: list[str] | None = None,
    ) -> list[Task]:
        """List issues with optional label and state filters. Pass state=None to list all states."""
        cmd = ["gh", "issue", "list", "--limit", str(limit)]

        # Map state: None means "all"
        gh_state = "all" if state is None else state.value
        cmd.extend(["--state", gh_state])

        if label:
            cmd.extend(["--label", label])

        # Build search exclusions
        if exclude_labels:
            search_parts = [f"-label:{lbl}" for lbl in exclude_labels]
            cmd.extend(["--search", " ".join(search_parts)])

        cmd.extend(
            [
                "--json",
                "number,title,state,labels,body,url,createdAt,updatedAt",
            ]
        )

        result = run(cmd, check=True, retries=3)
        raw_list = json.loads(result.stdout)
        return [_parse_gh_task(item) for item in raw_list]

    def create_task(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> Task:
        """Create a GitHub Issue. Returns the created task with ID and URL."""
        cmd = ["gh", "issue", "create", "--title", title]

        # Write body to temp file to handle multiline content safely
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(body)
            body_file = f.name

        try:
            cmd.extend(["--body-file", body_file])

            if labels:
                for label in labels:
                    cmd.extend(["--label", label])

            result = run(cmd, check=True)
            url = result.stdout.strip()
            number = _extract_number_from_url(url)

            logger.info("github.issue_created", number=number, title=title)

            return Task(
                id=number,
                title=title,
                body=body,
                state=TaskState.OPEN,
                url=url,
                labels=[Label(name=lbl) for lbl in (labels or [])],
            )
        finally:
            Path(body_file).unlink(missing_ok=True)

    def read_task(self, task_id: str) -> Task:
        """Read a single issue by number."""
        result = run(
            [
                "gh",
                "issue",
                "view",
                task_id,
                "--json",
                "number,title,body,state,labels,url,createdAt,updatedAt",
            ],
            check=True,
            retries=3,
        )

        raw = json.loads(result.stdout)
        return _parse_gh_task(raw)

    def read_task_or_none(self, task_id: str) -> Task | None:
        """Read a single issue by number, returning None if not found.

        Returns None only for explicit "not found" conditions (deleted issue).
        Other failures (auth, network, rate-limit) are logged at WARNING and re-raised
        to avoid masking real backend failures.

        Uses check=False to avoid ERROR-level logs for subprocess failures.
        """
        result = run(
            [
                "gh",
                "issue",
                "view",
                task_id,
                "--json",
                "number,title,body,state,labels,url,createdAt,updatedAt",
            ],
            check=False,
        )

        if result.returncode != 0:
            # Check stderr to differentiate "not found" from other failures
            stderr = (result.stderr or "").lower()
            is_not_found = "not found" in stderr or "could not resolve to an issue" in stderr

            if is_not_found:
                logger.debug(
                    "github.read_task_or_none_not_found",
                    task_id=task_id,
                    returncode=result.returncode,
                )
                return None

            # Other failures (auth, network, rate-limit) should not be silent
            logger.warning(
                "github.read_task_or_none_failed",
                task_id=task_id,
                returncode=result.returncode,
                stderr=result.stderr.strip() if result.stderr else "",
            )
            # Re-raise to preserve the error context
            raise RuntimeError(
                f"Failed to read issue {task_id}: {result.stderr or 'unknown error'}"
            )

        try:
            raw = json.loads(result.stdout)
            return _parse_gh_task(raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "github.read_task_or_none_parse_failed",
                task_id=task_id,
                error=str(e),
            )
            # Successful exit but unparseable output is unexpected — don't mask it
            raise RuntimeError(f"Failed to parse issue {task_id} response: {e}") from e

    def update_task(
        self,
        task_id: str,
        body: str | None = None,
        title: str | None = None,
    ) -> Task:
        """Update an issue's title and/or body."""
        if body is not None:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
                f.write(body)
                body_file = f.name
            try:
                run(
                    ["gh", "issue", "edit", task_id, "--body-file", body_file],
                    check=True,
                    retries=3,
                )
            finally:
                Path(body_file).unlink(missing_ok=True)

        if title is not None:
            run(
                ["gh", "issue", "edit", task_id, "--title", title],
                check=True,
                retries=3,
            )

        return self.read_task(task_id)

    def close_task(self, task_id: str) -> Task:
        """Close an issue."""
        run(["gh", "issue", "close", task_id], check=True, retries=3)
        logger.info("github.issue_closed", number=task_id)
        return self.read_task(task_id)

    def comment_on_task(self, task_id: str, body: str) -> None:
        """Add a comment to an issue."""
        run(
            ["gh", "issue", "comment", task_id, "--body", body],
            check=True,
        )

    # --- Label management ---

    def ensure_label(self, label: Label) -> None:
        """Ensure a label exists, creating it if needed.

        1. Search for the label name
        2. If not found, create it (handling "already exists" race condition)
        """
        # Check if label already exists
        try:
            result = run(
                ["gh", "label", "list", "--search", label.name, "--json", "name", "-q", ".[].name"],
                check=True,
                retries=3,
            )
            existing = result.stdout.strip().splitlines()
            if label.name in existing:
                return
        except CommandError:
            pass  # Search failed — try creating anyway

        # Create the label
        cmd = [
            "gh",
            "label",
            "create",
            label.name,
            "--color",
            label.color,
        ]
        if label.description:
            cmd.extend(["--description", label.description])

        try:
            run(cmd, check=True, retries=3)
            logger.info("github.label_created", name=label.name)
        except CommandError as e:
            # "already exists" is fine — race condition with concurrent creation
            if "already exists" in e.stderr.lower():
                return
            raise

    def add_label(self, task_id: str, label_name: str) -> None:
        """Add a label to an issue (non-fatal on failure)."""
        try:
            run(
                ["gh", "issue", "edit", task_id, "--add-label", label_name],
                check=True,
                retries=3,
            )
        except CommandError:
            logger.warning(
                "github.label_add_failed",
                task_id=task_id,
                label=label_name,
            )

    def remove_label(self, task_id: str, label_name: str) -> None:
        """Remove a label from an issue (non-fatal on failure)."""
        try:
            run(
                ["gh", "issue", "edit", task_id, "--remove-label", label_name],
                check=True,
                retries=3,
            )
        except CommandError:
            logger.warning(
                "github.label_remove_failed",
                task_id=task_id,
                label=label_name,
            )

    # --- PR review operations ---

    def get_pr_review_threads(
        self,
        pr_number: int,
    ) -> list[ReviewThread]:
        """Fetch PR review threads via GitHub GraphQL API with pagination."""
        try:
            nwo = self.get_repo_nwo()
        except CommandError:
            logger.warning("github.get_review_threads_nwo_failed")
            return []

        owner, repo = nwo.split("/", 1)
        threads: list[ReviewThread] = []
        cursor: str | None = None

        while True:
            try:
                page_threads, has_next, cursor = self._fetch_review_threads_page(
                    owner, repo, pr_number, cursor
                )
            except (CommandError, json.JSONDecodeError) as e:
                logger.warning("github.review_threads_fetch_failed", error=str(e))
                break
            threads.extend(page_threads)
            if not has_next or not cursor:
                break

        return threads

    def _parse_thread_nodes(self, nodes: list[dict[str, Any]]) -> list[ReviewThread]:
        """Parse GraphQL reviewThread nodes into ReviewThread models."""
        threads: list[ReviewThread] = []
        for node in nodes:
            comments: list[ReviewComment] = []
            for cnode in node.get("comments", {}).get("nodes", []):
                comments.append(
                    ReviewComment(
                        author=cnode.get("author", {}).get("login", "")
                        if cnode.get("author")
                        else "",
                        body=cnode.get("body", ""),
                        path=cnode.get("path"),
                        line=cnode.get("line"),
                        created_at=cnode.get("createdAt"),
                        url=cnode.get("url"),
                    )
                )
            threads.append(
                ReviewThread(
                    id=node.get("id", ""),
                    is_resolved=node.get("isResolved", False),
                    is_outdated=node.get("isOutdated", False),
                    comments=comments,
                )
            )
        return threads

    def _fetch_review_threads_page(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        cursor: str | None = None,
    ) -> tuple[list[ReviewThread], bool, str | None]:
        """Fetch one page of review threads. Returns (threads, has_next, end_cursor)."""
        query = """
query($owner: String!, $repo: String!, $pr: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $after) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          isResolved
          isOutdated
          comments(first: 50) {
            nodes {
              body
              path
              line
              author { login }
              createdAt
              url
            }
          }
        }
      }
    }
  }
}"""

        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={repo}",
            "-F",
            f"pr={pr_number}",
            "-f",
            f"query={query}",
        ]
        if cursor:
            cmd.extend(["-f", f"after={cursor}"])

        result = run(cmd, check=True, retries=3)
        data = json.loads(result.stdout)

        pr_data = data.get("data", {}).get("repository", {}).get("pullRequest") or {}
        threads_data = pr_data.get("reviewThreads", {})
        page_info = threads_data.get("pageInfo", {})
        nodes = threads_data.get("nodes", [])

        threads = self._parse_thread_nodes(nodes)

        return (
            threads,
            page_info.get("hasNextPage", False),
            page_info.get("endCursor"),
        )

    def resolve_review_thread(self, thread_id: str) -> bool:
        """Mark a PR review thread as resolved via GitHub GraphQL mutation."""
        query = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread {
      isResolved
    }
  }
}"""
        try:
            result = run(
                [
                    "gh",
                    "api",
                    "graphql",
                    "-f",
                    f"threadId={thread_id}",
                    "-f",
                    f"query={query}",
                ],
                check=True,
                retries=3,
            )
            data = json.loads(result.stdout)
            thread_data = data.get("data", {}).get("resolveReviewThread", {}).get("thread", {})
            return bool(thread_data.get("isResolved", False))
        except (CommandError, json.JSONDecodeError) as e:
            logger.warning(
                "github.resolve_review_thread_failed",
                thread_id=thread_id,
                error=str(e),
            )
            return False

    def get_pr_issue_comments(
        self,
        pr_number: int,
    ) -> list[dict[str, str]]:
        """Fetch PR issue comments via gh API.

        Returns list of dicts with ``login`` and ``body`` keys.
        """
        try:
            nwo = self.get_repo_nwo()
        except CommandError:
            logger.warning("github.get_pr_issue_comments_nwo_failed")
            return []

        try:
            result = run(
                [
                    "gh",
                    "api",
                    f"repos/{nwo}/issues/{pr_number}/comments",
                    "--jq",
                    "[.[] | {login: .user.login, body: .body}]",
                ],
                check=True,
                retries=3,
            )
            return json.loads(result.stdout)  # type: ignore[no-any-return]
        except (CommandError, json.JSONDecodeError) as e:
            logger.warning(
                "github.get_pr_issue_comments_failed",
                pr_number=pr_number,
                error=str(e),
            )
            return []

    def get_pr_review_status(
        self,
        pr_number: int,
    ) -> PRReviewStatus:
        """Fetch comprehensive PR review status via a combined GraphQL query.

        Fetches review threads (paginated), PR-level reviews (last 100), and
        pending review requests (first 50) in a single initial call. Subsequent
        pages only fetch additional review threads.

        Reviews and review requests use fixed limits (not paginated) because
        we deduplicate by author (keeping the latest review), so only truly
        extreme edge cases (100+ review submissions) could miss data.
        """
        try:
            nwo = self.get_repo_nwo()
        except CommandError:
            logger.warning("github.get_review_status_nwo_failed")
            return PRReviewStatus(fetch_failed=True)

        owner, repo = nwo.split("/", 1)

        # First page: combined query with reviews + reviewRequests + latest commit
        threads: list[ReviewThread] = []
        reviews: list[PRReview] = []
        pending_reviewers: list[PendingReviewer] = []
        latest_commit_pushed_at: datetime | None = None
        fetch_failed = False

        try:
            page_threads, has_next, cursor, page_reviews, page_pending, latest_commit_pushed_at = (
                self._fetch_review_status_page(owner, repo, pr_number, cursor=None)
            )
            threads.extend(page_threads)
            reviews.extend(page_reviews)
            pending_reviewers.extend(page_pending)

            # Subsequent pages: only threads (reviews/requests don't paginate here)
            while has_next and cursor:
                page_threads, has_next, cursor = self._fetch_review_threads_page(
                    owner, repo, pr_number, cursor
                )
                threads.extend(page_threads)
        except (CommandError, json.JSONDecodeError) as e:
            logger.warning("github.review_status_fetch_failed", error=str(e))
            fetch_failed = True

        # Detect bot status from issue comments
        bot_status: ReviewBotStatus | None = None
        try:
            comments = self.get_pr_issue_comments(pr_number)
            bot_status = detect_coderabbit_review_status(comments)
        except Exception:
            logger.debug("github.review_status_bot_check_failed", exc_info=True)

        # Generic PR-level bot reviews: treat any pending bot review as in-progress
        if bot_status is None and any(r.is_bot and r.state == ReviewState.PENDING for r in reviews):
            bot_status = ReviewBotStatus.IN_PROGRESS

        return PRReviewStatus(
            actionable_threads=filter_actionable_threads(threads),
            reviews=reviews,
            pending_reviewers=pending_reviewers,
            bot_status=bot_status,
            fetch_failed=fetch_failed,
            latest_commit_pushed_at=latest_commit_pushed_at,
        )

    def _fetch_review_status_page(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        cursor: str | None = None,
    ) -> tuple[
        list[ReviewThread], bool, str | None, list[PRReview], list[PendingReviewer], datetime | None
    ]:
        """Fetch first page with threads, reviews, review requests, and latest commit.

        Returns (threads, has_next, end_cursor, reviews, pending_reviewers,
        latest_commit_pushed_at).
        """
        query = """
query($owner: String!, $repo: String!, $pr: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $after) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          isResolved
          isOutdated
          comments(first: 50) {
            nodes {
              body
              path
              line
              author { login }
              createdAt
              url
            }
          }
        }
      }
      reviews(last: 100) {
        nodes {
          author { login }
          state
          body
          submittedAt
        }
      }
      reviewRequests(first: 50) {
        nodes {
          requestedReviewer {
            ... on User { login }
            ... on Team { name }
            ... on Bot { login }
          }
        }
      }
      commits(last: 1) {
        nodes {
          commit {
            committedDate
          }
        }
      }
    }
  }
}"""

        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={repo}",
            "-F",
            f"pr={pr_number}",
            "-f",
            f"query={query}",
        ]
        if cursor:
            cmd.extend(["-f", f"after={cursor}"])

        result = run(cmd, check=True, retries=3)
        data = json.loads(result.stdout)

        pr_data = data.get("data", {}).get("repository", {}).get("pullRequest") or {}

        # Parse review threads (reuse shared helper)
        threads_data = pr_data.get("reviewThreads", {})
        page_info = threads_data.get("pageInfo", {})
        nodes = threads_data.get("nodes", [])
        threads = self._parse_thread_nodes(nodes)

        # Parse PR-level reviews
        reviews: list[PRReview] = []
        for rnode in pr_data.get("reviews", {}).get("nodes", []):
            author_login = ""
            if rnode.get("author"):
                author_login = rnode["author"].get("login", "")
            state_str = rnode.get("state", "COMMENTED")
            try:
                state = ReviewState(state_str)
            except ValueError:
                state = ReviewState.COMMENTED
            normalized = author_login.lower()
            is_bot = (
                normalized == "bot"
                or normalized.startswith(("bot-", "bot_"))
                or normalized.endswith(("[bot]", "-bot", "_bot"))
            )
            reviews.append(
                PRReview(
                    author=author_login,
                    state=state,
                    body=rnode.get("body", ""),
                    submitted_at=rnode.get("submittedAt"),
                    is_bot=is_bot,
                )
            )

        # Parse pending review requests
        pending: list[PendingReviewer] = []
        for req_node in pr_data.get("reviewRequests", {}).get("nodes", []):
            reviewer = req_node.get("requestedReviewer", {}) or {}
            name = reviewer.get("login") or reviewer.get("name") or ""
            is_team = "name" in reviewer and "login" not in reviewer
            if name:
                pending.append(PendingReviewer(name=name, is_team=is_team))

        # Parse latest commit timestamp
        latest_commit_pushed_at: datetime | None = None
        commits_nodes = pr_data.get("commits", {}).get("nodes", [])
        if commits_nodes:
            committed_date_str = commits_nodes[0].get("commit", {}).get("committedDate")
            if committed_date_str:
                try:
                    latest_commit_pushed_at = datetime.fromisoformat(
                        str(committed_date_str).replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    logger.debug("github.latest_commit_date_parse_failed", raw=committed_date_str)

        # Warn if hard query limits may have been hit (potential truncation)
        if len(reviews) == 100:
            logger.warning(
                "github.reviews_limit_reached",
                pr_number=pr_number,
                limit=100,
                message=(
                    "reviews(last: 100) limit reached — older reviews may be missing;"
                    " manual inspection recommended"
                ),
            )
        if len(pending) == 50:
            logger.warning(
                "github.review_requests_limit_reached",
                pr_number=pr_number,
                limit=50,
                message=(
                    "reviewRequests(first: 50) limit reached — some pending reviewers"
                    " may be missing; manual inspection recommended"
                ),
            )

        return (
            threads,
            page_info.get("hasNextPage", False),
            page_info.get("endCursor"),
            reviews,
            pending,
            latest_commit_pushed_at,
        )

    # --- Repository info ---

    def get_repo_nwo(self) -> str:
        """Get the repo name-with-owner via gh repo view."""
        result = run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            check=True,
            retries=3,
        )
        return result.stdout.strip()

    # --- Parent issue detection ---

    def find_parent_issue(self, task_id: str, label: str | None = None) -> str | None:
        """Find parent/tracking issue that references task_id in a checklist.

        Searches open issues for a body containing `- [ ] #<task_id>`.
        Returns the parent issue number or None.
        """
        cmd = ["gh", "issue", "list", "--state", "open", "--limit", "50"]
        if label:
            cmd.extend(["--label", label])
        cmd.extend(["--json", "number,body"])

        try:
            result = run(cmd, check=True, retries=3)
            issues = json.loads(result.stdout)
        except (CommandError, json.JSONDecodeError):
            return None

        # Primary path: use the shared checklist parser so parent detection stays
        # aligned with tracking/batch flows (indented items, backticked refs, etc.).
        legacy_no_hash_pattern = re.compile(
            rf"^[ \t]*-\s*\[\s*\]\s*{re.escape(task_id)}(?:\.\s| |$)",
            re.MULTILINE,
        )

        for issue in issues:
            body = issue.get("body", "") or ""
            if task_id in parse_tracking_child_ids(body):
                return str(issue["number"])
            if legacy_no_hash_pattern.search(body):
                return str(issue["number"])

        return None

    # --- Project board operations ---

    def move_to_in_progress(self, task_id: str) -> bool:
        """Move an issue to 'In Progress' on GitHub Projects v2.

        Uses GraphQL to find the project item and update its status field.
        Returns True if successful, False otherwise (non-fatal).
        """
        try:
            nwo = self.get_repo_nwo()
        except CommandError:
            return False

        owner, repo = nwo.split("/", 1)

        # GraphQL query to find project item and status field options
        query = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      projectItems(first: 10) {
        nodes {
          id
          project {
            id
            field(name: "Status") {
              ... on ProjectV2SingleSelectField {
                id
                options { id name }
              }
            }
          }
        }
      }
    }
  }
}"""

        try:
            result = run(
                [
                    "gh",
                    "api",
                    "graphql",
                    "-f",
                    f"owner={owner}",
                    "-f",
                    f"repo={repo}",
                    "-F",
                    f"number={task_id}",
                    "-f",
                    f"query={query}",
                ],
                check=True,
                retries=3,
            )

            data = json.loads(result.stdout)
            items = (
                data.get("data", {})
                .get("repository", {})
                .get("issue", {})
                .get("projectItems", {})
                .get("nodes", [])
            )
        except (CommandError, json.JSONDecodeError):
            return False

        # Find "In Progress" option across all linked projects
        for item in items:
            project_id = item.get("project", {}).get("id")
            item_id = item.get("id")
            field = item.get("project", {}).get("field") or {}
            field_id = field.get("id")
            options = field.get("options", [])

            if not all([project_id, item_id, field_id]):
                continue

            # Find the "In Progress" option (case-insensitive)
            option_id = None
            for opt in options:
                if re.search(r"in.progress", opt.get("name", ""), re.IGNORECASE):
                    option_id = opt["id"]
                    break

            if not option_id:
                continue

            # Mutation to update status
            mutation = """
mutation($project_id: ID!, $item_id: ID!, $field_id: ID!, $option_id: String!) {
  updateProjectV2ItemFieldValue(
    input: {
      projectId: $project_id
      itemId: $item_id
      fieldId: $field_id
      value: { singleSelectOptionId: $option_id }
    }
  ) { projectV2Item { id } }
}"""

            try:
                run(
                    [
                        "gh",
                        "api",
                        "graphql",
                        "-f",
                        f"project_id={project_id}",
                        "-f",
                        f"item_id={item_id}",
                        "-f",
                        f"field_id={field_id}",
                        "-f",
                        f"option_id={option_id}",
                        "-f",
                        f"query={mutation}",
                    ],
                    check=True,
                    retries=3,
                )
                logger.info(
                    "github.moved_to_in_progress",
                    task_id=task_id,
                )
                return True
            except CommandError as e:
                if "INSUFFICIENT_SCOPES" in e.stderr:
                    logger.warning(
                        "github.project_scope_missing",
                        hint="Run: gh auth refresh -s project",
                    )
                return False

        return False
