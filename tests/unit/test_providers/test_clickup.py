"""Tests for the ClickUp provider — mocked HTTP interactions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from wade.models.config import ProviderConfig, ProviderID
from wade.models.task import Label, TaskState
from wade.providers.clickup import ClickUpProvider, _parse_clickup_task
from wade.utils.http import APIError

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

CLICKUP_CONFIG = ProviderConfig(
    name=ProviderID.CLICKUP,
    api_token_env="CLICKUP_API_TOKEN",
    settings={"list_id": "901", "team_id": "123"},
)


@pytest.fixture
def provider() -> ClickUpProvider:
    with patch.dict("os.environ", {"CLICKUP_API_TOKEN": "pk_test_token"}):
        return ClickUpProvider(CLICKUP_CONFIG)


def _make_raw_task(
    task_id: str = "abc123",
    name: str = "Test task",
    description: str = "Some body",
    status: str = "open",
    tags: list[dict[str, str]] | None = None,
    url: str = "https://app.clickup.com/t/abc123",
    date_created: str | None = "1704067200000",
    date_updated: str | None = "1704153600000",
) -> dict:
    """Build a fake ClickUp task JSON object."""
    return {
        "id": task_id,
        "name": name,
        "description": description,
        "markdown_description": description,
        "status": {"status": status, "type": "open", "color": "#87909e"},
        "tags": tags or [],
        "url": url,
        "date_created": date_created,
        "date_updated": date_updated,
    }


# ---------------------------------------------------------------------------
# Unit tests for _parse_clickup_task
# ---------------------------------------------------------------------------


class TestParseClickupTask:
    def test_open_task(self) -> None:
        raw = _make_raw_task()
        task = _parse_clickup_task(raw)
        assert task.id == "abc123"
        assert task.title == "Test task"
        assert task.body == "Some body"
        assert task.state == TaskState.OPEN
        assert task.url == "https://app.clickup.com/t/abc123"

    def test_closed_task(self) -> None:
        raw = _make_raw_task(status="closed")
        task = _parse_clickup_task(raw)
        assert task.state == TaskState.CLOSED

    def test_complete_task(self) -> None:
        raw = _make_raw_task(status="complete")
        task = _parse_clickup_task(raw)
        assert task.state == TaskState.CLOSED

    def test_done_task(self) -> None:
        raw = _make_raw_task(status="done")
        task = _parse_clickup_task(raw)
        assert task.state == TaskState.CLOSED

    def test_tags_parsed_as_labels(self) -> None:
        raw = _make_raw_task(
            tags=[
                {"name": "bug", "tag_bg": "#d73a4a"},
                {"name": "feature", "tag_bg": "#0e8a16"},
            ]
        )
        task = _parse_clickup_task(raw)
        assert len(task.labels) == 2
        assert task.labels[0].name == "bug"
        assert task.labels[0].color == "d73a4a"
        assert task.labels[1].name == "feature"

    def test_null_body(self) -> None:
        raw = _make_raw_task(description="")
        raw["markdown_description"] = None
        raw["description"] = None
        task = _parse_clickup_task(raw)
        assert task.body == ""

    def test_timestamps_parsed(self) -> None:
        raw = _make_raw_task(date_created="1704067200000")
        task = _parse_clickup_task(raw)
        assert task.created_at is not None

    def test_null_timestamps(self) -> None:
        raw = _make_raw_task(date_created=None, date_updated=None)
        task = _parse_clickup_task(raw)
        assert task.created_at is None
        assert task.updated_at is None


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------


class TestClickUpProviderInit:
    def test_missing_api_token_raises(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ValueError, match="API token not found"),
        ):
            ClickUpProvider(CLICKUP_CONFIG)

    def test_missing_list_id_raises(self) -> None:
        bad_config = ProviderConfig(
            name=ProviderID.CLICKUP,
            api_token_env="CLICKUP_API_TOKEN",
            settings={"team_id": "123"},
        )
        with (
            patch.dict("os.environ", {"CLICKUP_API_TOKEN": "pk_test"}),
            pytest.raises(ValueError, match="list_id"),
        ):
            ClickUpProvider(bad_config)

    def test_missing_team_id_raises(self) -> None:
        bad_config = ProviderConfig(
            name=ProviderID.CLICKUP,
            api_token_env="CLICKUP_API_TOKEN",
            settings={"list_id": "901"},
        )
        with (
            patch.dict("os.environ", {"CLICKUP_API_TOKEN": "pk_test"}),
            pytest.raises(ValueError, match="team_id"),
        ):
            ClickUpProvider(bad_config)

    def test_custom_token_env(self) -> None:
        custom_config = ProviderConfig(
            name=ProviderID.CLICKUP,
            api_token_env="MY_CUSTOM_TOKEN",
            settings={"list_id": "901", "team_id": "123"},
        )
        with patch.dict("os.environ", {"MY_CUSTOM_TOKEN": "pk_custom"}):
            p = ClickUpProvider(custom_config)
            assert p._list_id == "901"


# ---------------------------------------------------------------------------
# CRUD tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestListTasks:
    def test_list_open_tasks(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.side_effect = [
            {"tasks": [_make_raw_task(), _make_raw_task(task_id="def456", name="Second")]},
            {"tasks": []},
        ]

        tasks = provider.list_tasks()

        assert len(tasks) == 2
        assert tasks[0].id == "abc123"
        assert tasks[1].id == "def456"

    def test_list_filters_by_state(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.side_effect = [
            {
                "tasks": [
                    _make_raw_task(status="open"),
                    _make_raw_task(task_id="closed1", status="closed"),
                ]
            },
            {"tasks": []},
        ]

        tasks = provider.list_tasks(state=TaskState.OPEN)
        assert len(tasks) == 1
        assert tasks[0].state == TaskState.OPEN

    def test_list_excludes_labels(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.side_effect = [
            {
                "tasks": [
                    _make_raw_task(tags=[{"name": "skip-me", "tag_bg": "#000"}]),
                    _make_raw_task(task_id="keep", tags=[{"name": "ok", "tag_bg": "#fff"}]),
                ]
            },
            {"tasks": []},
        ]

        tasks = provider.list_tasks(exclude_labels=["skip-me"])
        assert len(tasks) == 1
        assert tasks[0].id == "keep"

    def test_list_respects_limit(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.return_value = {
            "tasks": [_make_raw_task(task_id=f"t{i}") for i in range(10)]
        }

        tasks = provider.list_tasks(limit=3)
        assert len(tasks) == 3

    def test_list_with_label_filter(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.side_effect = [
            {"tasks": [_make_raw_task(tags=[{"name": "bug", "tag_bg": "#d73a4a"}])]},
            {"tasks": []},
        ]

        tasks = provider.list_tasks(label="bug")
        assert len(tasks) == 1

        # Verify tags[] param was passed
        call_params = provider._client.get.call_args_list[0][1]["params"]
        assert call_params["tags[]"] == "bug"

    def test_list_stops_on_partial_page(self, provider: ClickUpProvider) -> None:
        """When a page has fewer tasks than _PAGE_SIZE, pagination stops."""
        provider._client = MagicMock()
        # Return fewer tasks than page size — should not request another page
        provider._client.get.return_value = {
            "tasks": [_make_raw_task(task_id=f"t{i}") for i in range(5)]
        }

        tasks = provider.list_tasks(limit=50)
        assert len(tasks) == 5
        # Only one API call (no second page request)
        assert provider._client.get.call_count == 1

    def test_list_empty(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.return_value = {"tasks": []}

        tasks = provider.list_tasks()
        assert tasks == []


class TestCreateTask:
    def test_create_task(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.post.return_value = _make_raw_task(task_id="new1", name="New Task")

        task = provider.create_task("New Task", "Description here")

        assert task.id == "new1"
        assert task.title == "New Task"
        provider._client.post.assert_called_once()
        call_args = provider._client.post.call_args
        assert "/api/v2/list/901/task" in call_args[0][0]

    def test_create_task_with_labels(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.post.return_value = _make_raw_task(
            tags=[{"name": "bug", "tag_bg": "#d73a4a"}]
        )

        provider.create_task("Bug fix", "Fix it", labels=["bug"])

        call_json = provider._client.post.call_args[1]["json"]
        assert call_json["tags"] == ["bug"]


class TestReadTask:
    def test_read_task(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.return_value = _make_raw_task()

        task = provider.read_task("abc123")

        assert task.id == "abc123"
        assert task.title == "Test task"
        provider._client.get.assert_called_once()


class TestUpdateTask:
    def test_update_body(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.return_value = _make_raw_task()

        task = provider.update_task("abc123", body="New body")

        provider._client.put.assert_called_once()
        call_json = provider._client.put.call_args[1]["json"]
        assert call_json["markdown_description"] == "New body"
        assert task.id == "abc123"

    def test_update_title(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.return_value = _make_raw_task()

        provider.update_task("abc123", title="New title")

        call_json = provider._client.put.call_args[1]["json"]
        assert call_json["name"] == "New title"

    def test_update_nothing(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.return_value = _make_raw_task()

        provider.update_task("abc123")

        provider._client.put.assert_not_called()


class TestCloseTask:
    def test_close_task(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.return_value = _make_raw_task(status="closed")

        task = provider.close_task("abc123")

        provider._client.put.assert_called_once()
        call_json = provider._client.put.call_args[1]["json"]
        assert call_json["status"] == "closed"
        assert task.state == TaskState.CLOSED


class TestCommentOnTask:
    def test_comment(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.post.return_value = {}

        provider.comment_on_task("abc123", "A comment")

        provider._client.post.assert_called_once()
        call_json = provider._client.post.call_args[1]["json"]
        assert call_json["comment_text"] == "A comment"


# ---------------------------------------------------------------------------
# Label (tag) management tests
# ---------------------------------------------------------------------------


class TestEnsureLabel:
    def test_label_already_exists(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        # _resolve_space_id returns cached value
        provider._space_id = "space1"
        provider._client.get.return_value = {"tags": [{"name": "existing-tag"}]}

        provider.ensure_label(Label(name="existing-tag"))

        # Should not call post to create
        provider._client.post.assert_not_called()

    def test_label_created(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._space_id = "space1"
        provider._client.get.return_value = {"tags": []}
        provider._client.post.return_value = {}

        provider.ensure_label(Label(name="new-tag", color="ff0000"))

        provider._client.post.assert_called_once()
        call_json = provider._client.post.call_args[1]["json"]
        assert call_json["tag"]["name"] == "new-tag"

    def test_label_create_conflict_is_ok(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._space_id = "space1"
        provider._client.get.return_value = {"tags": []}
        provider._client.post.side_effect = APIError(400, "already exists")

        # Should not raise
        provider.ensure_label(Label(name="race-tag"))


class TestAddLabel:
    def test_add_label(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.post.return_value = {}

        provider.add_label("abc123", "my-tag")

        provider._client.post.assert_called_once()
        assert "/tag/my-tag" in provider._client.post.call_args[0][0]

    def test_add_label_failure_non_fatal(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.post.side_effect = APIError(404, "not found")

        # Should not raise
        provider.add_label("abc123", "missing-tag")


class TestRemoveLabel:
    def test_remove_label(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()

        provider.remove_label("abc123", "my-tag")

        provider._client.delete.assert_called_once()

    def test_remove_label_failure_non_fatal(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.delete.side_effect = APIError(404, "not found")

        # Should not raise
        provider.remove_label("abc123", "missing-tag")


# ---------------------------------------------------------------------------
# Project board operations
# ---------------------------------------------------------------------------


class TestMoveToInProgress:
    def test_success(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.put.return_value = _make_raw_task(status="in progress")

        result = provider.move_to_in_progress("abc123")

        assert result is True
        call_json = provider._client.put.call_args[1]["json"]
        assert call_json["status"] == "in progress"

    def test_failure_returns_false(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.put.side_effect = APIError(500, "server error")

        result = provider.move_to_in_progress("abc123")

        assert result is False


# ---------------------------------------------------------------------------
# Space ID resolution
# ---------------------------------------------------------------------------


class TestResolveSpaceId:
    def test_from_settings(self, provider: ClickUpProvider) -> None:
        provider._config = ProviderConfig(
            name=ProviderID.CLICKUP,
            settings={"list_id": "901", "team_id": "123", "space_id": "sp1"},
        )
        provider._space_id = None

        assert provider._resolve_space_id() == "sp1"

    def test_from_list_metadata(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.return_value = {"space": {"id": "sp2"}}

        assert provider._resolve_space_id() == "sp2"

    def test_cached(self, provider: ClickUpProvider) -> None:
        provider._space_id = "cached"

        assert provider._resolve_space_id() == "cached"

    def test_raises_if_unresolvable(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.return_value = {"space": {}}

        with pytest.raises(ValueError, match="Could not resolve space_id"):
            provider._resolve_space_id()
