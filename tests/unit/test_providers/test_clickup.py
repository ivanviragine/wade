"""Tests for the ClickUp provider — mocked HTTP interactions."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from wade.models.config import ProviderConfig, ProviderID
from wade.models.review import PRComment, PRReviewStatus
from wade.models.task import Complexity, Label, TaskState
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
    status_type: str | None = "open",
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
        "status": {"status": status, "type": status_type, "color": "#87909e"},
        "list": {"id": "901"},
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
        raw = _make_raw_task(status="closed", status_type="closed")
        task = _parse_clickup_task(raw)
        assert task.state == TaskState.CLOSED

    def test_complete_task(self) -> None:
        raw = _make_raw_task(status="complete", status_type="closed")
        task = _parse_clickup_task(raw)
        assert task.state == TaskState.CLOSED

    def test_done_task(self) -> None:
        raw = _make_raw_task(status="done", status_type="done")
        task = _parse_clickup_task(raw)
        assert task.state == TaskState.OPEN

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

    def test_complexity_tag_is_case_insensitive(self) -> None:
        raw = _make_raw_task(tags=[{"name": " Complexity:VERY_COMPLEX ", "tag_bg": "#c5def5"}])
        task = _parse_clickup_task(raw)
        assert task.complexity == Complexity.VERY_COMPLEX

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
                    _make_raw_task(task_id="closed1", status="closed", status_type="closed"),
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

    def test_finds_marker_beyond_the_first_page(self, provider: ClickUpProvider) -> None:
        marker = "<!-- wade:plan-handoff:recovery -->"
        provider._client = MagicMock()
        provider._client.get.side_effect = [
            {"tasks": [_make_raw_task(task_id=f"first-{index}") for index in range(100)]},
            {
                "tasks": [
                    _make_raw_task(
                        task_id="recovered",
                        description=f"recovered task\n\n{marker}",
                    )
                ]
            },
        ]

        matches = provider.find_tasks_by_body_marker(marker)

        assert [task.id for task in matches] == ["recovered"]
        assert provider._client.get.call_count == 2


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
    @pytest.mark.parametrize("name", ["Shipped", "complete", " Closed "])
    @pytest.mark.parametrize("reason", [None, "not planned"])
    def test_discovers_home_list_and_preserves_status_name(
        self, provider: ClickUpProvider, name: str, reason: str | None
    ) -> None:
        from wade.models.task import CloseReason

        before = _make_raw_task()
        before["list"] = {"id": "home-list"}
        provider._client = MagicMock()
        provider._client.get.side_effect = [
            before,
            {
                "override_statuses": False,
                "statuses": [
                    {"status": "Done", "type": "done"},
                    {"status": name, "type": "closed"},
                ],
            },
            _make_raw_task(status=name, status_type="closed"),
        ]
        task = provider.close_task("abc123", reason=CloseReason(reason) if reason else None)
        assert task.state == TaskState.CLOSED
        assert provider._client.get.call_args_list == [
            call("/api/v2/task/abc123", params={"include_markdown_description": "true"}),
            call("/api/v2/list/home-list"),
            call("/api/v2/task/abc123", params={"include_markdown_description": "true"}),
        ]
        provider._client.put.assert_called_once_with("/api/v2/task/abc123", json={"status": name})
        assert provider._client.method_calls == [
            call.get("/api/v2/task/abc123", params={"include_markdown_description": "true"}),
            call.get("/api/v2/list/home-list"),
            call.put("/api/v2/task/abc123", json={"status": name}),
            call.get("/api/v2/task/abc123", params={"include_markdown_description": "true"}),
        ]

    @pytest.mark.parametrize(
        "metadata",
        [
            {},
            {"statuses": None},
            {"statuses": []},
            {"statuses": [{"status": "done", "type": "done"}]},
            {"statuses": [{"status": "closed"}]},
            {
                "statuses": [
                    {"status": "shipped", "type": "closed"},
                    {"status": "complete", "type": "closed"},
                ]
            },
            {"statuses": [{"type": "closed"}]},
            {"statuses": [{"status": "   ", "type": "closed"}]},
        ],
    )
    def test_unusable_destinations_do_not_write(
        self, provider: ClickUpProvider, metadata: dict
    ) -> None:
        provider._client = MagicMock()
        provider._client.get.side_effect = [_make_raw_task(), metadata]
        with pytest.raises(ValueError, match="Closed status"):
            provider.close_task("abc123")
        provider._client.put.assert_not_called()

    @pytest.mark.parametrize("home", [None, {}, {"id": None}, {"id": ""}])
    def test_missing_home_list_does_not_guess(
        self, provider: ClickUpProvider, home: dict | None
    ) -> None:
        raw = _make_raw_task()
        raw["list"] = home
        provider._client = MagicMock()
        provider._client.get.return_value = raw
        with pytest.raises(ValueError, match="home List"):
            provider.close_task("abc123")
        provider._client.put.assert_not_called()

    def test_canonical_closed_is_idempotent(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.return_value = _make_raw_task(status="shipped", status_type="closed")
        assert provider.close_task("abc123").state == TaskState.CLOSED
        assert provider._client.get.call_count == 1
        provider._client.put.assert_not_called()

    def test_legacy_closed_name_still_requires_canonical_closure(
        self, provider: ClickUpProvider
    ) -> None:
        provider._client = MagicMock()
        provider._client.get.side_effect = [
            _make_raw_task(status="closed", status_type=None),
            {"statuses": [{"status": "Shipped", "type": "closed"}]},
            _make_raw_task(status="Shipped", status_type="closed"),
        ]
        assert provider.close_task("abc123").state == TaskState.CLOSED
        provider._client.put.assert_called_once_with(
            "/api/v2/task/abc123", json={"status": "Shipped"}
        )

    @pytest.mark.parametrize("status_type", ["done", "open", None])
    def test_final_read_must_be_canonically_closed(
        self, provider: ClickUpProvider, status_type: str | None
    ) -> None:
        provider._client = MagicMock()
        provider._client.get.side_effect = [
            _make_raw_task(),
            {"statuses": [{"status": "Shipped", "type": "closed"}]},
            _make_raw_task(status="closed", status_type=status_type),
        ]
        with pytest.raises(ValueError, match="not canonically closed"):
            provider.close_task("abc123")
        provider._client.put.assert_called_once()

    @pytest.mark.parametrize("step", ["task", "list", "put", "verify"])
    def test_api_failures_propagate(self, provider: ClickUpProvider, step: str) -> None:
        error = APIError(503, "unavailable")
        provider._client = MagicMock()
        replies = [
            _make_raw_task(),
            {"statuses": [{"status": "Shipped", "type": "closed"}]},
            _make_raw_task(status="Shipped", status_type="closed"),
        ]
        if step == "put":
            provider._client.put.side_effect = error
        else:
            replies[{"task": 0, "list": 1, "verify": 2}[step]] = error
        provider._client.get.side_effect = replies
        with pytest.raises(APIError):
            provider.close_task("abc123")
        if step in {"task", "list"}:
            provider._client.put.assert_not_called()
        else:
            assert provider._client.put.call_count == 1


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


# ---------------------------------------------------------------------------
# PR-review delegation to GitHub
# ---------------------------------------------------------------------------


class TestPRReviewDelegation:
    """ClickUp tasks live in ClickUp; PRs flow through GitHub."""

    def _build(self) -> tuple[ClickUpProvider, MagicMock]:
        gh = MagicMock()
        with patch.dict("os.environ", {"CLICKUP_API_TOKEN": "pk_test"}):
            provider = ClickUpProvider(CLICKUP_CONFIG, github_provider=gh)
        return provider, gh

    def test_get_pr_review_threads_delegates(self) -> None:
        provider, gh = self._build()
        gh.get_pr_review_threads.return_value = ["t1"]
        assert provider.get_pr_review_threads(42) == ["t1"]
        gh.get_pr_review_threads.assert_called_once_with(42)

    def test_resolve_review_thread_delegates(self) -> None:
        provider, gh = self._build()
        gh.resolve_review_thread.return_value = True
        assert provider.resolve_review_thread("THREAD_ID") is True
        gh.resolve_review_thread.assert_called_once_with("THREAD_ID")

    def test_get_pr_issue_comments_delegates(self) -> None:
        provider, gh = self._build()
        expected = [PRComment(login="u", body="hi")]
        gh.get_pr_issue_comments.return_value = expected
        assert provider.get_pr_issue_comments(7) == expected
        gh.get_pr_issue_comments.assert_called_once_with(7)

    def test_get_pr_review_status_delegates(self) -> None:
        provider, gh = self._build()
        status = PRReviewStatus()
        gh.get_pr_review_status.return_value = status
        assert provider.get_pr_review_status(99) is status
        gh.get_pr_review_status.assert_called_once_with(99)

    def test_get_repo_nwo_delegates(self) -> None:
        provider, gh = self._build()
        gh.get_repo_nwo.return_value = "owner/repo"
        assert provider.get_repo_nwo() == "owner/repo"
        gh.get_repo_nwo.assert_called_once_with()

    def test_inner_github_provider_lazy_init(self) -> None:
        with patch.dict("os.environ", {"CLICKUP_API_TOKEN": "pk_test"}):
            provider = ClickUpProvider(CLICKUP_CONFIG)
        assert provider._pr_github is None
        gh = provider._pr_gh()
        assert gh is not None
        assert provider._pr_gh() is gh


@pytest.mark.parametrize(
    "name,kind,expected",
    [
        ("shipped", "closed", TaskState.CLOSED),
        ("Complete", " CLOSED ", TaskState.CLOSED),
        ("done", "done", TaskState.OPEN),
        ("Ready to publish", "done", TaskState.OPEN),
        ("closed", "open", TaskState.OPEN),
        ("complete", "custom", TaskState.OPEN),
        ("done", "unstarted", TaskState.OPEN),
        ("closed", "future-type", TaskState.OPEN),
        ("active", "active", TaskState.OPEN),
        ("CLOSED", None, TaskState.CLOSED),
        ("complete", "", TaskState.CLOSED),
        (" DONE ", "   ", TaskState.CLOSED),
        ("shipped", None, TaskState.OPEN),
    ],
)
def test_status_type_precedence_and_legacy_fallback(
    name: str, kind: str | None, expected: TaskState
) -> None:
    assert _parse_clickup_task(_make_raw_task(status=name, status_type=kind)).state == expected


@pytest.mark.parametrize("status", [None, {}, {"status": "closed"}, {"status": "open"}])
def test_missing_status_or_type_compatibility(status: dict | None) -> None:
    raw = _make_raw_task()
    raw["status"] = status
    expected = TaskState.CLOSED if status and status.get("status") == "closed" else TaskState.OPEN
    assert _parse_clickup_task(raw).state == expected


@pytest.mark.parametrize(
    "state,expected",
    [
        (TaskState.OPEN, ["done", "misleading"]),
        (TaskState.CLOSED, ["shipped"]),
        (None, ["shipped", "done", "misleading"]),
    ],
)
@pytest.mark.parametrize("marker_scan", [False, True])
def test_list_and_recovery_share_canonical_filtering(
    provider: ClickUpProvider, state: TaskState | None, expected: list[str], marker_scan: bool
) -> None:
    provider._client = MagicMock()
    provider._client.get.return_value = {
        "tasks": [
            _make_raw_task(task_id="shipped", status="Shipped", status_type="closed"),
            _make_raw_task(task_id="done", status="Ready", status_type="done"),
            _make_raw_task(task_id="misleading", status="closed", status_type="custom"),
        ]
    }
    tasks = (
        provider.find_tasks_by_body_marker("Some body", state=state)
        if marker_scan
        else provider.list_tasks(state=state)
    )
    assert [task.id for task in tasks] == expected
    assert provider._client.get.call_args.kwargs["params"]["include_closed"] == (
        "false" if state == TaskState.OPEN else "true"
    )


@pytest.mark.parametrize("marker_scan", [False, True])
def test_filtered_full_page_does_not_hide_later_matches(
    provider: ClickUpProvider, marker_scan: bool
) -> None:
    provider._client = MagicMock()
    provider._client.get.side_effect = [
        {
            "tasks": [
                _make_raw_task(task_id=f"closed-{index}", status="Shipped", status_type="closed")
                for index in range(100)
            ]
        },
        {"tasks": [_make_raw_task(task_id="retained-done", status="Ready", status_type="done")]},
    ]
    tasks = (
        provider.find_tasks_by_body_marker("Some body")
        if marker_scan
        else provider.list_tasks(limit=1)
    )
    assert [task.id for task in tasks] == ["retained-done"]
    assert [c.kwargs["params"]["page"] for c in provider._client.get.call_args_list] == ["0", "1"]


class TestFindParentIssue:
    """Exact tracking-parent lookup across the configured List (#490)."""

    def test_scans_every_page_for_the_exact_child(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.side_effect = [
            {
                "tasks": [_make_raw_task(task_id="near", description="- [ ] clickup:abc12345")]
                + [_make_raw_task(task_id=f"filler-{index}") for index in range(99)]
            },
            {
                "tasks": [
                    _make_raw_task(task_id="abc1234", description="- [ ] clickup:abc1234"),
                    _make_raw_task(task_id="parent", description="- [x] clickup:abc1234 — done"),
                ]
            },
        ]

        assert provider.find_parent_issue("abc1234", label="feature-plan") == "parent"

        first, second = provider._client.get.call_args_list
        assert first.kwargs["params"]["page"] == "0"
        assert second.kwargs["params"]["page"] == "1"
        assert first.kwargs["params"]["tags[]"] == "feature-plan"
        assert first.kwargs["params"]["list_ids[]"] == "901"
        assert first.kwargs["params"]["include_closed"] == "false"

    def test_legacy_hash_checklist_matches_exactly(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.return_value = {
            "tasks": [
                _make_raw_task(task_id="other", description="- [ ] #ABC1234\n- [ ] #abc12"),
                _make_raw_task(task_id="legacy", description="- [ ] #abc1234 — child"),
            ]
        }
        assert provider.find_parent_issue("abc1234") == "legacy"

    def test_closed_candidates_and_prose_are_ignored(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.return_value = {
            "tasks": [
                _make_raw_task(
                    task_id="closed",
                    status="closed",
                    status_type="closed",
                    description="- [ ] clickup:abc1234",
                ),
                _make_raw_task(task_id="prose", description="Mentions clickup:abc1234 only."),
            ]
        }
        assert provider.find_parent_issue("abc1234") is None
        assert provider._client.get.call_count == 1

    def test_lookup_failure_is_not_absence(self, provider: ClickUpProvider) -> None:
        provider._client = MagicMock()
        provider._client.get.side_effect = [
            {"tasks": [_make_raw_task(task_id=f"filler-{index}") for index in range(100)]},
            APIError(500, "outage"),
        ]
        with pytest.raises(APIError):
            provider.find_parent_issue("abc1234")
