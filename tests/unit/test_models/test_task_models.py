"""Tests for task domain models."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghaiw.models.task import (
    Complexity,
    Label,
    LabelType,
    PlanFile,
    Task,
    TaskState,
    parse_complexity_from_body,
)


class TestComplexity:
    def test_values(self) -> None:
        assert Complexity.EASY == "easy"
        assert Complexity.MEDIUM == "medium"
        assert Complexity.COMPLEX == "complex"
        assert Complexity.VERY_COMPLEX == "very_complex"

    def test_from_string(self) -> None:
        assert Complexity("easy") == Complexity.EASY
        assert Complexity("very_complex") == Complexity.VERY_COMPLEX


class TestTask:
    def test_create_minimal(self) -> None:
        task = Task(id="42", title="Test task")
        assert task.id == "42"
        assert task.state == TaskState.OPEN
        assert task.complexity is None
        assert task.labels == []

    def test_create_full(self) -> None:
        task = Task(
            id="42",
            title="Add auth",
            body="Implement OAuth",
            state=TaskState.IN_PROGRESS,
            complexity=Complexity.COMPLEX,
            labels=[Label(name="feature-plan", label_type=LabelType.ISSUE_LABEL)],
            parent_id="10",
            subtask_ids=["43", "44"],
        )
        assert task.complexity == Complexity.COMPLEX
        assert len(task.labels) == 1
        assert task.parent_id == "10"

    def test_title_max_length(self) -> None:
        with pytest.raises(ValueError):
            Task(id="1", title="x" * 257)


class TestParseComplexityFromBody:
    def test_easy(self) -> None:
        assert parse_complexity_from_body("## Complexity\neasy\n") == Complexity.EASY

    def test_complex(self) -> None:
        body = "Some intro.\n\n## Complexity\ncomplex\n\n## Tasks\n- do stuff"
        assert parse_complexity_from_body(body) == Complexity.COMPLEX

    def test_very_complex(self) -> None:
        assert parse_complexity_from_body("## Complexity\nvery_complex\n") == Complexity.VERY_COMPLEX

    def test_missing_section(self) -> None:
        assert parse_complexity_from_body("## Tasks\n- do stuff\n") is None

    def test_empty_body(self) -> None:
        assert parse_complexity_from_body("") is None

    def test_malformed_value(self) -> None:
        assert parse_complexity_from_body("## Complexity\nunknown_level\n") is None

    def test_case_insensitive_heading(self) -> None:
        assert parse_complexity_from_body("## COMPLEXITY\neasy\n") == Complexity.EASY


class TestPlanFile:
    def test_from_markdown(self, tmp_path: Path) -> None:
        plan = tmp_path / "PLAN.md"
        plan.write_text(
            "# Add user authentication\n\n"
            "## Complexity\n\ncomplex\n\n"
            "## Context\n\nWe need OAuth support.\n\n"
            "## Tasks\n\n- [ ] Add login endpoint\n- [ ] Add tests\n"
        )

        result = PlanFile.from_markdown(plan)
        assert result.title == "Add user authentication"
        assert result.complexity == Complexity.COMPLEX
        assert "context" in result.sections
        assert "tasks" in result.sections
        assert "OAuth" in result.sections["context"]

    def test_from_markdown_no_title_raises(self, tmp_path: Path) -> None:
        plan = tmp_path / "PLAN.md"
        plan.write_text("No heading here\n\nJust text.\n")

        with pytest.raises(ValueError, match="must have a '# Title'"):
            PlanFile.from_markdown(plan)

    def test_from_markdown_easy_complexity(self, tmp_path: Path) -> None:
        plan = tmp_path / "PLAN.md"
        plan.write_text("# Fix typo\n\n## Complexity\n\neasy\n")

        result = PlanFile.from_markdown(plan)
        assert result.complexity == Complexity.EASY

    def test_from_markdown_no_complexity(self, tmp_path: Path) -> None:
        plan = tmp_path / "PLAN.md"
        plan.write_text("# Simple fix\n\nJust a one-liner.\n")

        result = PlanFile.from_markdown(plan)
        assert result.complexity is None
