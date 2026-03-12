"""Tests for task service — CRUD, labels, and plan summary."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wade.models.config import ProjectConfig, ProjectSettings
from wade.models.task import Task, TaskState
from wade.services.task_service import (
    LABEL_COLOR_IMPLEMENTED,
    LABEL_COLOR_IN_PROGRESS,
    LABEL_COLOR_ISSUE,
    LABEL_COLOR_PLANNED,
    PLAN_SUMMARY_MARKER_END,
    PLAN_SUMMARY_MARKER_START,
    _strip_plan_summary,
    add_implemented_by_labels,
    add_in_progress_label,
    add_planned_by_labels,
    apply_plan_token_usage,
    build_plan_summary_block,
    close_task,
    create_from_plan_file,
    create_task,
    ensure_in_progress_label,
    ensure_task_label,
    list_tasks,
    read_task,
    remove_in_progress_label,
    update_task,
)


@pytest.fixture
def mock_provider():
    """Create a mock task provider."""
    provider = MagicMock()
    provider.create_task.return_value = Task(
        id="42",
        title="Test Issue",
        body="Test body",
        url="https://github.com/owner/repo/issues/42",
    )
    provider.read_task.return_value = Task(
        id="42",
        title="Test Issue",
        body="Test body\nwith multiple lines\n",
    )
    provider.close_task.return_value = Task(id="42", title="Test Issue", state=TaskState.CLOSED)
    provider.list_tasks.return_value = [
        Task(id="1", title="First Issue", state=TaskState.OPEN),
        Task(id="2", title="Second Issue", state=TaskState.OPEN),
    ]
    return provider


@pytest.fixture
def config():
    """Create a test config."""
    return ProjectConfig(
        project=ProjectSettings(issue_label="test-label"),
    )


# ---------------------------------------------------------------------------
# Label tests
# ---------------------------------------------------------------------------


class TestLabels:
    def test_ensure_task_label(self, mock_provider: MagicMock) -> None:
        ensure_task_label(mock_provider, "feature-plan")
        mock_provider.ensure_label.assert_called_once()
        label = mock_provider.ensure_label.call_args[0][0]
        assert label.name == "feature-plan"
        assert label.color == LABEL_COLOR_ISSUE

    def test_ensure_in_progress_label(self, mock_provider: MagicMock) -> None:
        ensure_in_progress_label(mock_provider)
        mock_provider.ensure_label.assert_called_once()
        label = mock_provider.ensure_label.call_args[0][0]
        assert label.name == "in-progress"
        assert label.color == LABEL_COLOR_IN_PROGRESS

    def test_add_in_progress_label(self, mock_provider: MagicMock) -> None:
        add_in_progress_label(mock_provider, "42")
        mock_provider.ensure_label.assert_called_once()
        mock_provider.add_label.assert_called_once_with("42", "in-progress")

    def test_remove_in_progress_label(self, mock_provider: MagicMock) -> None:
        remove_in_progress_label(mock_provider, "42")
        mock_provider.remove_label.assert_called_once_with("42", "in-progress")

    def test_add_planned_by_labels_tool_only(self, mock_provider: MagicMock) -> None:
        add_planned_by_labels(mock_provider, "42", ai_tool="claude")
        # Should create tool label and add it
        assert mock_provider.ensure_label.call_count == 1
        assert mock_provider.add_label.call_count == 1
        label = mock_provider.ensure_label.call_args[0][0]
        assert label.name == "planned-by:claude"
        assert label.color == LABEL_COLOR_PLANNED

    def test_add_planned_by_labels_with_model(self, mock_provider: MagicMock) -> None:
        add_planned_by_labels(mock_provider, "42", ai_tool="claude", model="claude-opus-4-6")
        # Should create two labels: tool + model
        assert mock_provider.ensure_label.call_count == 2
        assert mock_provider.add_label.call_count == 2
        calls = mock_provider.add_label.call_args_list
        assert calls[0][0] == ("42", "planned-by:claude")
        assert calls[1][0] == ("42", "planned-model:claude-opus-4-6")

    def test_add_planned_by_labels_no_tool(self, mock_provider: MagicMock) -> None:
        add_planned_by_labels(mock_provider, "42", ai_tool=None)
        # Should be a no-op
        mock_provider.ensure_label.assert_not_called()
        mock_provider.add_label.assert_not_called()

    def test_add_implemented_by_labels(self, mock_provider: MagicMock) -> None:
        add_implemented_by_labels(mock_provider, "42", ai_tool="copilot", model="claude-sonnet-4-6")
        assert mock_provider.ensure_label.call_count == 2
        labels = [mock_provider.ensure_label.call_args_list[i][0][0] for i in range(2)]
        assert labels[0].name == "implemented-by:copilot"
        assert labels[0].color == LABEL_COLOR_IMPLEMENTED
        assert labels[1].name == "implemented-model:claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Plan summary tests
# ---------------------------------------------------------------------------


class TestPlanSummary:
    def test_build_block_with_tokens(self) -> None:
        block = build_plan_summary_block(
            ai_tool="claude",
            model="claude-opus-4-6",
            total_tokens=12345,
            input_tokens=10000,
            output_tokens=2000,
            cached_tokens=345,
        )
        assert PLAN_SUMMARY_MARKER_START in block
        assert PLAN_SUMMARY_MARKER_END in block
        assert "| Metric | Value |" in block
        assert "| Tool | `claude` |" in block
        assert "| Model | `claude-opus-4-6` |" in block
        assert "| Total tokens | **12,345** |" in block
        assert "| Input tokens | **10,000** |" in block
        assert "| Output tokens | **2,000** |" in block
        assert "| Cached tokens | **345** |" in block
        assert "### Usage" not in block

    def test_build_block_unavailable(self) -> None:
        block = build_plan_summary_block(
            ai_tool="copilot",
        )
        assert "| Total tokens | *unavailable* |" in block

    def test_build_block_with_breakdown(self) -> None:
        block = build_plan_summary_block(
            total_tokens=5000,
            model_breakdown=[
                {"model": "claude-opus-4-6", "input": 3000, "output": 1000, "cached": 500},
                {"model": "claude-haiku-4-5", "input": 400, "output": 100, "cached": 0},
            ],
        )
        assert "### Model Breakdown" not in block
        assert "| `claude-opus-4-6`" in block
        assert "| `claude-haiku-4-5`" in block
        assert "**3,000** in · **1,000** out · **500** cached" in block
        assert "**400** in · **100** out · **0** cached" in block

    def test_build_block_derives_totals_from_breakdown(self) -> None:
        block = build_plan_summary_block(
            model_breakdown=[
                {"model": "claude-opus-4-6", "input": 3000, "output": 1000, "cached": 500},
                {"model": "claude-haiku-4-5", "input": 400, "output": 100, "cached": 0},
            ],
        )
        assert "| Total tokens | **5,000** |" in block
        assert "| Input tokens | **3,400** |" in block
        assert "| Output tokens | **1,100** |" in block
        assert "| Cached tokens | **500** |" in block

    def test_build_block_with_premium(self) -> None:
        block = build_plan_summary_block(
            total_tokens=5000,
            premium_requests=15,
        )
        assert "| Premium requests (est.) | **15** |" in block

    def test_build_block_per_issue_estimate(self) -> None:
        block = build_plan_summary_block(
            total_tokens=10000,
            per_issue_estimate=3333,
        )
        assert "| This issue (est.) | **3,333** |" in block

    def test_strip_plan_summary_removes_block(self) -> None:
        body = (
            "# Title\n\nSome content.\n\n"
            f"{PLAN_SUMMARY_MARKER_START}\n"
            "## Token Usage (Planning)\n\nUsage info\n"
            f"{PLAN_SUMMARY_MARKER_END}\n"
        )
        stripped = _strip_plan_summary(body)
        assert PLAN_SUMMARY_MARKER_START not in stripped
        assert "# Title" in stripped
        assert "Some content" in stripped

    def test_strip_plan_summary_no_block(self) -> None:
        body = "# Title\n\nSome content.\n"
        stripped = _strip_plan_summary(body)
        assert stripped == body

    def test_apply_token_usage(self, mock_provider: MagicMock) -> None:
        mock_provider.read_task.side_effect = [
            Task(id="1", title="A", body="Line 1\nLine 2\nLine 3\n"),
            Task(id="2", title="B", body="Line 1\n"),
        ]
        apply_plan_token_usage(
            provider=mock_provider,
            issue_numbers=["1", "2"],
            ai_tool="claude",
            total_tokens=12000,
            input_tokens=10000,
            output_tokens=2000,
        )
        # Should update both issues
        assert mock_provider.update_task.call_count == 2


# ---------------------------------------------------------------------------
# CRUD tests
# ---------------------------------------------------------------------------


class TestCreateTask:
    def test_create_success(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        task = create_task("My Bug", body="Details", config=config, provider=mock_provider)
        assert task is not None
        assert task.id == "42"
        mock_provider.create_task.assert_called_once()
        call_kwargs = mock_provider.create_task.call_args[1]
        assert call_kwargs["title"] == "My Bug"
        assert call_kwargs["body"] == "Details"
        assert "test-label" in call_kwargs["labels"]

    def test_create_applies_project_label(
        self, mock_provider: MagicMock, config: ProjectConfig
    ) -> None:
        create_task("Fix", config=config, provider=mock_provider)
        call_kwargs = mock_provider.create_task.call_args[1]
        assert "test-label" in call_kwargs["labels"]

    def test_create_applies_extra_labels(
        self, mock_provider: MagicMock, config: ProjectConfig
    ) -> None:
        create_task("Fix", extra_labels=["bug", "urgent"], config=config, provider=mock_provider)
        call_kwargs = mock_provider.create_task.call_args[1]
        assert "test-label" in call_kwargs["labels"]
        assert "bug" in call_kwargs["labels"]
        assert "urgent" in call_kwargs["labels"]

    def test_create_empty_body_by_default(
        self, mock_provider: MagicMock, config: ProjectConfig
    ) -> None:
        create_task("Fix", config=config, provider=mock_provider)
        call_kwargs = mock_provider.create_task.call_args[1]
        assert call_kwargs["body"] == ""

    def test_create_ensures_label(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        create_task("Fix", config=config, provider=mock_provider)
        mock_provider.ensure_label.assert_called_once()

    def test_create_failure_returns_none(
        self, mock_provider: MagicMock, config: ProjectConfig
    ) -> None:
        mock_provider.create_task.side_effect = Exception("API error")
        task = create_task("Fix", config=config, provider=mock_provider)
        assert task is None


class TestCreateFromPlanFile:
    def test_create_success(
        self, tmp_path: Path, mock_provider: MagicMock, config: ProjectConfig
    ) -> None:
        plan = tmp_path / "PLAN.md"
        plan.write_text("# My Feature\n\n## Tasks\n\n- Do thing A\n- Do thing B\n")

        task = create_from_plan_file(plan, config=config, provider=mock_provider)
        assert task is not None
        assert task.id == "42"
        mock_provider.create_task.assert_called_once()
        call_kwargs = mock_provider.create_task.call_args
        assert call_kwargs[1]["title"] == "My Feature"
        assert "Do thing A" in call_kwargs[1]["body"]

    def test_create_invalid_file(
        self, tmp_path: Path, mock_provider: MagicMock, config: ProjectConfig
    ) -> None:
        plan = tmp_path / "bad.md"
        plan.write_text("No title heading here\n")

        task = create_from_plan_file(plan, config=config, provider=mock_provider)
        assert task is None
        mock_provider.create_task.assert_not_called()

    def test_create_missing_file(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        task = create_from_plan_file(
            Path("/nonexistent/PLAN.md"), config=config, provider=mock_provider
        )
        assert task is None

    def test_create_ensures_label(
        self, tmp_path: Path, mock_provider: MagicMock, config: ProjectConfig
    ) -> None:
        plan = tmp_path / "PLAN.md"
        plan.write_text("# Feature\n\nBody content\n")

        create_from_plan_file(plan, config=config, provider=mock_provider)
        mock_provider.ensure_label.assert_called_once()


class TestListTasks:
    def test_list_open(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        tasks = list_tasks(config=config, provider=mock_provider, state="open")
        assert len(tasks) == 2
        mock_provider.list_tasks.assert_called_once()

    def test_list_json(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        tasks = list_tasks(config=config, provider=mock_provider, json_mode=True)
        assert len(tasks) == 2

    def test_list_empty(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        mock_provider.list_tasks.return_value = []
        tasks = list_tasks(config=config, provider=mock_provider)
        assert len(tasks) == 0

    def test_list_all_passes_none_state(
        self, mock_provider: MagicMock, config: ProjectConfig
    ) -> None:
        """state='all' must pass state=None to provider, not fall back to OPEN."""
        list_tasks(config=config, provider=mock_provider, state="all")
        call_kwargs = mock_provider.list_tasks.call_args.kwargs
        assert call_kwargs["state"] is None, (
            "state='all' should pass None to provider; got OPEN instead (regression)"
        )

    def test_list_closed_passes_closed_state(
        self, mock_provider: MagicMock, config: ProjectConfig
    ) -> None:
        """state='closed' must pass TaskState.CLOSED to provider."""
        from wade.models.task import TaskState

        list_tasks(config=config, provider=mock_provider, state="closed")
        call_kwargs = mock_provider.list_tasks.call_args.kwargs
        assert call_kwargs["state"] == TaskState.CLOSED


class TestReadTask:
    def test_read_success(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        task = read_task("42", config=config, provider=mock_provider)
        assert task is not None
        assert task.id == "42"

    def test_read_json(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        task = read_task("42", config=config, provider=mock_provider, json_mode=True)
        assert task is not None

    def test_read_failure(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        mock_provider.read_task.side_effect = Exception("Not found")
        task = read_task("999", config=config, provider=mock_provider)
        assert task is None


class TestUpdateTask:
    def test_update_with_body_file(
        self,
        tmp_path: Path,
        mock_provider: MagicMock,
        config: ProjectConfig,
    ) -> None:
        plan = tmp_path / "update.md"
        plan.write_text("# Updated Title\n\nNew body content.\n")

        mock_provider.update_task.return_value = Task(id="42", title="Updated Title")
        result = update_task("42", body_file=plan, config=config, provider=mock_provider)
        assert result is True
        mock_provider.update_task.assert_called_once()

    def test_update_with_comment(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        result = update_task("42", comment="A comment", config=config, provider=mock_provider)
        assert result is True
        mock_provider.comment_on_task.assert_called_once_with("42", "A comment")

    def test_update_no_args(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        result = update_task("42", config=config, provider=mock_provider)
        assert result is False


class TestCloseTask:
    def test_close_success(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        result = close_task("42", config=config, provider=mock_provider)
        assert result is True
        mock_provider.close_task.assert_called_once_with("42")

    def test_close_with_comment(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        result = close_task("42", comment="Done!", config=config, provider=mock_provider)
        assert result is True
        mock_provider.comment_on_task.assert_called_once_with("42", "Done!")
        mock_provider.close_task.assert_called_once()

    def test_close_removes_in_progress(
        self, mock_provider: MagicMock, config: ProjectConfig
    ) -> None:
        close_task("42", config=config, provider=mock_provider)
        mock_provider.remove_label.assert_called_once_with("42", "in-progress")

    def test_close_failure(self, mock_provider: MagicMock, config: ProjectConfig) -> None:
        mock_provider.close_task.side_effect = Exception("API error")
        result = close_task("42", config=config, provider=mock_provider)
        assert result is False
