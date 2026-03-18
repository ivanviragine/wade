"""Tests for task domain models."""

from __future__ import annotations

from pathlib import Path

import pytest

from wade.models.task import (
    Complexity,
    Label,
    LabelType,
    PlanFile,
    Task,
    TaskState,
    infer_label_type,
    parse_complexity_from_body,
    parse_complexity_from_labels,
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


class TestLabelTypeInference:
    def test_planned_by_prefix(self) -> None:
        assert infer_label_type("planned-by:claude") == LabelType.PLANNED_BY

    def test_planned_model_prefix(self) -> None:
        assert infer_label_type("planned-model:claude-opus-4-6") == LabelType.PLANNED_MODEL

    def test_implemented_by_prefix(self) -> None:
        assert infer_label_type("implemented-by:claude") == LabelType.IMPLEMENTED_BY

    def test_implemented_model_prefix(self) -> None:
        assert (
            infer_label_type("implemented-model:claude-sonnet-4-6") == LabelType.IMPLEMENTED_MODEL
        )

    def test_review_addressed_by_prefix(self) -> None:
        assert infer_label_type("review-addressed-by:claude") == LabelType.REVIEW_ADDRESSED_BY

    def test_review_addressed_model_prefix(self) -> None:
        assert (
            infer_label_type("review-addressed-model:claude-haiku-4-5")
            == LabelType.REVIEW_ADDRESSED_MODEL
        )

    def test_complexity_prefix(self) -> None:
        assert infer_label_type("complexity:easy") == LabelType.COMPLEXITY

    def test_generic_label_defaults_to_issue_label(self) -> None:
        assert infer_label_type("bug") == LabelType.ISSUE_LABEL
        assert infer_label_type("feature-plan") == LabelType.ISSUE_LABEL

    def test_label_model_validator_infers_type(self) -> None:
        label = Label(name="planned-by:claude")
        assert label.label_type == LabelType.PLANNED_BY

    def test_label_model_validator_preserves_explicit_type(self) -> None:
        label = Label(name="planned-by:claude", label_type=LabelType.PLANNED_BY)
        assert label.label_type == LabelType.PLANNED_BY

    def test_label_model_validator_does_not_override_non_default(self) -> None:
        # Explicit non-ISSUE_LABEL type must not be overridden
        label = Label(name="some-label", label_type=LabelType.IN_PROGRESS)
        assert label.label_type == LabelType.IN_PROGRESS

    def test_label_model_validator_generic_label_stays_issue_label(self) -> None:
        label = Label(name="bug")
        assert label.label_type == LabelType.ISSUE_LABEL


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
        assert (
            parse_complexity_from_body("## Complexity\nvery_complex\n") == Complexity.VERY_COMPLEX
        )

    def test_missing_section(self) -> None:
        assert parse_complexity_from_body("## Tasks\n- do stuff\n") is None

    def test_empty_body(self) -> None:
        assert parse_complexity_from_body("") is None

    def test_malformed_value(self) -> None:
        assert parse_complexity_from_body("## Complexity\nunknown_level\n") is None

    def test_case_insensitive_heading(self) -> None:
        assert parse_complexity_from_body("## COMPLEXITY\neasy\n") == Complexity.EASY


class TestParseComplexityFromLabels:
    def test_parses_lowercase_label(self) -> None:
        labels = [Label(name="complexity:complex")]
        assert parse_complexity_from_labels(labels) == Complexity.COMPLEX

    def test_parses_case_insensitive_label(self) -> None:
        labels = [Label(name=" Complexity:VERY_COMPLEX ")]
        assert parse_complexity_from_labels(labels) == Complexity.VERY_COMPLEX

    def test_invalid_complexity_label_returns_none(self) -> None:
        labels = [Label(name="complexity:unknown")]
        assert parse_complexity_from_labels(labels) is None


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
