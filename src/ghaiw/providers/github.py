"""GitHub provider — wraps the gh CLI for issue, label, and PR operations.

All GitHub interactions go through the gh CLI binary, which handles
authentication, pagination, rate limits, and token refresh transparently.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

import structlog

from ghaiw.models.task import (
    Label,
    Task,
    TaskState,
    parse_complexity_from_body,
    parse_complexity_from_labels,
)
from ghaiw.providers.base import AbstractTaskProvider
from ghaiw.utils.process import CommandError, run

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

        result = run(cmd, check=True)
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
        )

        raw = json.loads(result.stdout)
        return _parse_gh_task(raw)

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
                )
            finally:
                Path(body_file).unlink(missing_ok=True)

        if title is not None:
            run(
                ["gh", "issue", "edit", task_id, "--title", title],
                check=True,
            )

        return self.read_task(task_id)

    def close_task(self, task_id: str) -> Task:
        """Close an issue."""
        run(["gh", "issue", "close", task_id], check=True)
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
            run(cmd, check=True)
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
            )
        except CommandError:
            logger.warning(
                "github.label_remove_failed",
                task_id=task_id,
                label=label_name,
            )

    # --- Snapshot/diff ---

    def snapshot_task_numbers(
        self,
        label: str | None = None,
        state: TaskState = TaskState.OPEN,
    ) -> set[str]:
        """Get current issue numbers for pre/post session diffing."""
        tasks = self.list_tasks(label=label, state=state)
        return {t.id for t in tasks}

    # --- PR operations ---

    def create_pr(
        self,
        title: str,
        body: str,
        base_branch: str,
        draft: bool = False,
    ) -> str:
        """Create a pull request via gh pr create. Returns the PR URL."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(body)
            body_file = f.name

        try:
            cmd = [
                "gh",
                "pr",
                "create",
                "--title",
                title,
                "--body-file",
                body_file,
                "--base",
                base_branch,
            ]
            if draft:
                cmd.append("--draft")

            result = run(cmd, check=True)
            url = result.stdout.strip()
            logger.info("github.pr_created", url=url)
            return url
        finally:
            Path(body_file).unlink(missing_ok=True)

    def merge_pr(
        self,
        pr_number: str,
        strategy: str = "squash",
        delete_branch: bool = True,
    ) -> None:
        """Merge a PR via gh pr merge."""
        cmd = ["gh", "pr", "merge", pr_number, f"--{strategy}"]
        if delete_branch:
            cmd.append("--delete-branch")
        run(cmd, check=True)
        logger.info("github.pr_merged", number=pr_number, strategy=strategy)

    def get_pr_for_branch(self, branch: str) -> dict[str, Any] | None:
        """Get PR info for a branch. Returns dict with number/body or None."""
        try:
            result = run(
                ["gh", "pr", "view", branch, "--json", "number,body"],
                check=True,
            )
            data: dict[str, Any] = json.loads(result.stdout)
            return data
        except CommandError as e:
            logger.warning("get_pr_for_branch failed", branch=branch, error=str(e))
            return None
        except json.JSONDecodeError as e:
            logger.warning(
                "get_pr_for_branch: invalid JSON response",
                branch=branch,
                error=str(e),
            )
            return None

    def update_pr_body(self, pr_number: str, body: str) -> None:
        """Update a PR's body text."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(body)
            body_file = f.name

        try:
            run(
                ["gh", "pr", "edit", pr_number, "--body-file", body_file],
                check=True,
            )
        finally:
            Path(body_file).unlink(missing_ok=True)

    # --- Repository info ---

    def get_repo_nwo(self) -> str:
        """Get the repo name-with-owner via gh repo view."""
        result = run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            check=True,
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
            result = run(cmd, check=True)
            issues = json.loads(result.stdout)
        except (CommandError, json.JSONDecodeError):
            return None

        # Match checklist patterns: - [ ] #42 or - [ ] 42
        # re.MULTILINE so $ matches end-of-line, not just end-of-string
        pattern = re.compile(
            rf"-\s*\[\s*\]\s*#?{re.escape(task_id)}(?:\.\s| |$)",
            re.MULTILINE,
        )

        for issue in issues:
            body = issue.get("body", "") or ""
            if pattern.search(body):
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
