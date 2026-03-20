"""Tests for the GitHub provider — mocked gh CLI interactions."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from wade.models.config import ProjectConfig, ProviderConfig, ProviderID
from wade.models.task import Complexity, Label, LabelType, TaskState
from wade.providers.github import GitHubProvider, _extract_number_from_url, _parse_gh_task
from wade.providers.registry import get_provider
from wade.utils.process import CommandError

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider() -> GitHubProvider:
    return GitHubProvider()


def _make_completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    """Build a fake CompletedProcess for mocking."""
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


class TestExtractNumberFromUrl:
    def test_issue_url(self) -> None:
        assert _extract_number_from_url("https://github.com/owner/repo/issues/42") == "42"

    def test_pr_url(self) -> None:
        assert _extract_number_from_url("https://github.com/owner/repo/pull/99") == "99"

    def test_url_with_trailing_whitespace(self) -> None:
        assert _extract_number_from_url("https://github.com/o/r/issues/7 \n") == "7"

    def test_invalid_url(self) -> None:
        with pytest.raises(ValueError, match="Could not extract"):
            _extract_number_from_url("https://github.com/owner/repo")


class TestParseGhTask:
    def test_full_issue(self) -> None:
        raw = {
            "number": 42,
            "title": "Test issue",
            "body": "Some body",
            "state": "OPEN",
            "labels": [
                {"name": "bug", "color": "d73a4a", "description": "A bug"},
            ],
            "url": "https://github.com/o/r/issues/42",
            "createdAt": "2025-01-01T00:00:00Z",
            "updatedAt": "2025-01-02T00:00:00Z",
        }
        task = _parse_gh_task(raw)
        assert task.id == "42"
        assert task.title == "Test issue"
        assert task.body == "Some body"
        assert task.state == TaskState.OPEN
        assert len(task.labels) == 1
        assert task.labels[0].name == "bug"
        assert task.url == "https://github.com/o/r/issues/42"

    def test_closed_state(self) -> None:
        raw = {"number": 1, "title": "t", "state": "CLOSED"}
        task = _parse_gh_task(raw)
        assert task.state == TaskState.CLOSED

    def test_null_body(self) -> None:
        raw = {"number": 1, "title": "t", "body": None}
        task = _parse_gh_task(raw)
        assert task.body == ""

    def test_empty_labels(self) -> None:
        raw = {"number": 1, "title": "t", "labels": []}
        task = _parse_gh_task(raw)
        assert task.labels == []

    def test_complexity_from_body(self) -> None:
        raw = {
            "number": 10,
            "title": "Add feature",
            "body": "## Summary\nDo stuff.\n\n## Complexity\neasy\n\n## Tasks\n- a\n",
        }
        task = _parse_gh_task(raw)
        assert task.complexity == Complexity.EASY

    def test_no_complexity_in_body(self) -> None:
        raw = {"number": 10, "title": "Simple", "body": "No sections here."}
        task = _parse_gh_task(raw)
        assert task.complexity is None

    def test_planned_by_label_infers_label_type(self) -> None:
        raw = {
            "number": 42,
            "title": "Planned issue",
            "body": "",
            "state": "OPEN",
            "labels": [{"name": "planned-by:claude", "color": "BFD4F2", "description": ""}],
        }
        task = _parse_gh_task(raw)
        assert len(task.labels) == 1
        assert task.labels[0].label_type == LabelType.PLANNED_BY


# ---------------------------------------------------------------------------
# Issue CRUD tests
# ---------------------------------------------------------------------------


class TestListTasks:
    @patch("wade.providers.github.run")
    def test_list_open_issues(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        issues_json = json.dumps(
            [
                {"number": 1, "title": "Issue 1", "state": "OPEN", "labels": [], "body": ""},
                {"number": 2, "title": "Issue 2", "state": "OPEN", "labels": [], "body": ""},
            ]
        )
        mock_run.return_value = _make_completed(issues_json)

        tasks = provider.list_tasks(label="feature-plan")
        assert len(tasks) == 2
        assert tasks[0].id == "1"
        assert tasks[1].title == "Issue 2"

        # Verify the command was called with the right flags
        cmd = mock_run.call_args[0][0]
        assert "gh" in cmd
        assert "--label" in cmd
        assert "feature-plan" in cmd
        assert "--state" in cmd
        assert "open" in cmd

    @patch("wade.providers.github.run")
    def test_list_with_exclude(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        mock_run.return_value = _make_completed("[]")
        provider.list_tasks(exclude_labels=["in-progress", "blocked"])

        cmd = mock_run.call_args[0][0]
        assert "--search" in cmd
        search_idx = cmd.index("--search")
        assert "-label:in-progress" in cmd[search_idx + 1]
        assert "-label:blocked" in cmd[search_idx + 1]


class TestCreateTask:
    @patch("wade.providers.github.run")
    def test_create_issue(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        mock_run.return_value = _make_completed("https://github.com/owner/repo/issues/42\n")

        task = provider.create_task(
            title="New feature",
            body="## Description\nA new feature",
            labels=["feature-plan"],
        )

        assert task.id == "42"
        assert task.title == "New feature"
        assert task.url == "https://github.com/owner/repo/issues/42"

        cmd = mock_run.call_args[0][0]
        assert "--title" in cmd
        assert "--body-file" in cmd
        assert "--label" in cmd

    @patch("wade.providers.github.run")
    def test_create_without_labels(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        mock_run.return_value = _make_completed("https://github.com/owner/repo/issues/1\n")
        task = provider.create_task(title="Simple", body="body")
        assert task.id == "1"
        cmd = mock_run.call_args[0][0]
        assert "--label" not in cmd


class TestReadTask:
    @patch("wade.providers.github.run")
    def test_read_issue(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        raw = {
            "number": 42,
            "title": "Read me",
            "body": "content",
            "state": "OPEN",
            "labels": [{"name": "feature-plan", "color": "0E8A16", "description": ""}],
            "url": "https://github.com/o/r/issues/42",
        }
        mock_run.return_value = _make_completed(json.dumps(raw))

        task = provider.read_task("42")
        assert task.id == "42"
        assert task.title == "Read me"
        assert len(task.labels) == 1


class TestReadTaskOrNone:
    @patch("wade.providers.github.run")
    def test_read_existing_issue(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        """read_task_or_none returns Task when issue exists."""
        raw = {
            "number": 42,
            "title": "Read me",
            "body": "content",
            "state": "OPEN",
            "labels": [],
            "url": "https://github.com/o/r/issues/42",
        }
        mock_run.return_value = _make_completed(json.dumps(raw))

        task = provider.read_task_or_none("42")
        assert task is not None
        assert task.id == "42"
        assert task.title == "Read me"

    @patch("wade.providers.github.run")
    def test_returns_none_on_deleted_issue(
        self, mock_run: MagicMock, provider: GitHubProvider
    ) -> None:
        """read_task_or_none returns None when issue is deleted (check=False)."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["gh", "issue", "view", "404"],
            returncode=1,
            stdout="",
            stderr="not found",
        )

        task = provider.read_task_or_none("404")
        assert task is None

    @patch("wade.providers.github.run")
    def test_returns_none_on_parse_error(
        self, mock_run: MagicMock, provider: GitHubProvider
    ) -> None:
        """read_task_or_none returns None on unparseable output (exit 0)."""
        mock_run.return_value = _make_completed("invalid json")

        assert provider.read_task_or_none("42") is None

    @patch("wade.providers.github.run")
    def test_uses_check_false(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        """read_task_or_none calls run with check=False."""
        mock_run.return_value = _make_completed(
            json.dumps(
                {
                    "number": 42,
                    "title": "test",
                    "body": "",
                    "state": "OPEN",
                    "labels": [],
                    "url": "https://github.com/o/r/issues/42",
                }
            )
        )

        provider.read_task_or_none("42")

        # Verify that run was called with check=False
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs.get("check") is False


class TestUpdateTask:
    @patch("wade.providers.github.run")
    def test_update_body(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        # First call: edit, second call: read_task
        read_response = json.dumps(
            {
                "number": 42,
                "title": "t",
                "body": "new body",
                "state": "OPEN",
                "labels": [],
            }
        )
        mock_run.return_value = _make_completed(read_response)

        provider.update_task("42", body="new body")
        assert mock_run.call_count == 2  # edit + read

    @patch("wade.providers.github.run")
    def test_update_title(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        read_response = json.dumps(
            {
                "number": 42,
                "title": "new title",
                "body": "",
                "state": "OPEN",
                "labels": [],
            }
        )
        mock_run.return_value = _make_completed(read_response)

        provider.update_task("42", title="new title")
        assert mock_run.call_count == 2  # edit + read


class TestCloseTask:
    @patch("wade.providers.github.run")
    def test_close(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        read_response = json.dumps(
            {
                "number": 42,
                "title": "t",
                "body": "",
                "state": "CLOSED",
                "labels": [],
            }
        )
        mock_run.return_value = _make_completed(read_response)

        provider.close_task("42")
        # First call is close, second is read
        first_cmd = mock_run.call_args_list[0][0][0]
        assert "close" in first_cmd


class TestCommentOnTask:
    @patch("wade.providers.github.run")
    def test_comment(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        mock_run.return_value = _make_completed("")

        provider.comment_on_task("42", "Great work!")
        cmd = mock_run.call_args[0][0]
        assert "comment" in cmd
        assert "--body" in cmd
        assert "Great work!" in cmd


# ---------------------------------------------------------------------------
# Label management tests
# ---------------------------------------------------------------------------


class TestEnsureLabel:
    @patch("wade.providers.github.run")
    def test_label_already_exists(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        mock_run.return_value = _make_completed("feature-plan\n")

        label = Label(name="feature-plan", color="0E8A16", description="Plan issue")
        provider.ensure_label(label)

        # Should only call list, not create
        assert mock_run.call_count == 1

    @patch("wade.providers.github.run")
    def test_label_created(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        # First call: list (no match), second call: create
        mock_run.side_effect = [
            _make_completed("other-label\n"),
            _make_completed(""),
        ]

        label = Label(name="new-label", color="FBCA04", description="New")
        provider.ensure_label(label)

        assert mock_run.call_count == 2
        create_cmd = mock_run.call_args_list[1][0][0]
        assert "create" in create_cmd
        assert "new-label" in create_cmd
        assert "--color" in create_cmd

    @patch("wade.providers.github.run")
    def test_label_race_condition(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        """Test that "already exists" error during creation is non-fatal."""
        mock_run.side_effect = [
            _make_completed(""),  # list: empty
            CommandError(["gh"], 1, "label already exists"),
        ]

        label = Label(name="race-label", color="000000")
        # Should not raise
        provider.ensure_label(label)


class TestAddLabel:
    @patch("wade.providers.github.run")
    def test_add_success(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        mock_run.return_value = _make_completed("")
        provider.add_label("42", "in-progress")

        cmd = mock_run.call_args[0][0]
        assert "--add-label" in cmd
        assert "in-progress" in cmd

    @patch("wade.providers.github.run")
    def test_add_failure_nonfatal(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        """Label add failures are logged but not raised."""
        mock_run.side_effect = CommandError(["gh"], 1, "not found")
        # Should not raise
        provider.add_label("999", "nonexistent")


class TestRemoveLabel:
    @patch("wade.providers.github.run")
    def test_remove_success(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        mock_run.return_value = _make_completed("")
        provider.remove_label("42", "in-progress")

        cmd = mock_run.call_args[0][0]
        assert "--remove-label" in cmd

    @patch("wade.providers.github.run")
    def test_remove_failure_nonfatal(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        mock_run.side_effect = CommandError(["gh"], 1, "label not on issue")
        provider.remove_label("42", "nonexistent")


# ---------------------------------------------------------------------------
# Repository info tests
# ---------------------------------------------------------------------------


class TestGetRepoNwo:
    @patch("wade.providers.github.run")
    def test_nwo(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        mock_run.return_value = _make_completed("owner/repo\n")
        assert provider.get_repo_nwo() == "owner/repo"


# ---------------------------------------------------------------------------
# Parent issue detection tests
# ---------------------------------------------------------------------------


class TestFindParentIssue:
    @patch("wade.providers.github.run")
    def test_found_with_hash(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        issues = [
            {"number": 10, "body": "## Tasks\n- [ ] #42\n- [ ] #43"},
            {"number": 20, "body": "Unrelated"},
        ]
        mock_run.return_value = _make_completed(json.dumps(issues))

        parent = provider.find_parent_issue("42", label="feature-plan")
        assert parent == "10"

    @patch("wade.providers.github.run")
    def test_found_without_hash(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        issues = [
            {"number": 10, "body": "## Tasks\n- [ ] 42. Add feature"},
        ]
        mock_run.return_value = _make_completed(json.dumps(issues))

        parent = provider.find_parent_issue("42")
        assert parent == "10"

    @patch("wade.providers.github.run")
    def test_not_found(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        issues = [
            {"number": 10, "body": "No checklist here"},
        ]
        mock_run.return_value = _make_completed(json.dumps(issues))

        parent = provider.find_parent_issue("42")
        assert parent is None

    @patch("wade.providers.github.run")
    def test_checked_items_not_matched(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        """Already-checked items should not match (pattern expects [ ])."""
        issues = [
            {"number": 10, "body": "- [x] #42"},
        ]
        mock_run.return_value = _make_completed(json.dumps(issues))

        parent = provider.find_parent_issue("42")
        assert parent is None


# ---------------------------------------------------------------------------
# move_to_in_progress tests
# ---------------------------------------------------------------------------


class TestMoveToInProgress:
    @patch("wade.providers.github.run")
    def test_calls_gh_api_graphql(self, mock_run: MagicMock, provider: GitHubProvider) -> None:
        """move_to_in_progress should call gh api graphql and return True on success."""
        nwo_response = _make_completed("owner/repo\n")
        query_response = _make_completed(
            json.dumps(
                {
                    "data": {
                        "repository": {
                            "issue": {
                                "projectItems": {
                                    "nodes": [
                                        {
                                            "id": "item-id",
                                            "project": {
                                                "id": "project-id",
                                                "field": {
                                                    "id": "field-id",
                                                    "options": [
                                                        {"id": "opt-id", "name": "In Progress"}
                                                    ],
                                                },
                                            },
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            )
        )
        mutation_response = _make_completed(
            json.dumps(
                {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "item-id"}}}}
            )
        )
        mock_run.side_effect = [nwo_response, query_response, mutation_response]

        result = provider.move_to_in_progress("42")

        assert result is True
        # Verify both the query and the mutation were made (exactly 2 graphql calls)
        graphql_calls = [c for c in mock_run.call_args_list if "graphql" in c[0][0]]
        assert len(graphql_calls) == 2, (
            f"Expected query + mutation graphql calls, got {len(graphql_calls)}: {graphql_calls}"
        )
        # Verify the mutation received the IDs extracted from the query response
        mutation_args = graphql_calls[1][0][0]
        assert "project_id=project-id" in mutation_args
        assert "item_id=item-id" in mutation_args
        assert "field_id=field-id" in mutation_args
        assert "option_id=opt-id" in mutation_args

    @patch("wade.providers.github.run")
    def test_returns_false_on_gh_failure(
        self, mock_run: MagicMock, provider: GitHubProvider
    ) -> None:
        """move_to_in_progress returns False when gh api graphql fails."""
        nwo_response = _make_completed("owner/repo\n")
        mock_run.side_effect = [nwo_response, CommandError(["gh"], 1, "GraphQL error")]

        result = provider.move_to_in_progress("42")

        assert result is False


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_default_is_github(self) -> None:
        provider = get_provider()
        assert isinstance(provider, GitHubProvider)

    def test_github_from_config(self) -> None:
        config = ProjectConfig()
        provider = get_provider(config)
        assert isinstance(provider, GitHubProvider)

    @patch.dict("os.environ", {"CLICKUP_API_TOKEN": "pk_test"})
    def test_clickup_from_config(self) -> None:
        from wade.providers.clickup import ClickUpProvider

        config = ProjectConfig(
            provider=ProviderConfig(
                name=ProviderID.CLICKUP,
                api_token_env="CLICKUP_API_TOKEN",
                settings={"list_id": "123", "team_id": "456"},
            )
        )
        provider = get_provider(config)
        assert isinstance(provider, ClickUpProvider)

    def test_unknown_provider_raises(self) -> None:
        config = ProjectConfig()
        # Bypass Pydantic validation to inject an invalid provider name
        config.provider.__dict__["name"] = "nonexistent"
        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider(config)
