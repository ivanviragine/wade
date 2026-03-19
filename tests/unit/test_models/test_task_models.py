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
    has_checklist_items,
    infer_label_type,
    is_tracking_issue,
    parse_all_issue_refs,
    parse_complexity_from_body,
    parse_complexity_from_labels,
    parse_tracking_child_ids,
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


class TestIsTrackingIssue:
    def test_tracking_prefix(self) -> None:
        assert is_tracking_issue("Tracking: #167, #169, #171") is True

    def test_tracking_prefix_no_space(self) -> None:
        assert is_tracking_issue("Tracking:#167") is True

    def test_regular_issue_not_detected(self) -> None:
        assert is_tracking_issue("Add user authentication") is False

    def test_title_containing_tracking_elsewhere(self) -> None:
        assert is_tracking_issue("Fix Tracking pixel bug") is False

    def test_empty_title(self) -> None:
        assert is_tracking_issue("") is False


class TestParseTrackingChildIds:
    def test_unchecked_items(self) -> None:
        body = "- [ ] #167\n- [ ] #169\n- [ ] #171\n"
        assert parse_tracking_child_ids(body) == ["167", "169", "171"]

    def test_skips_checked_items(self) -> None:
        body = "- [x] #167\n- [ ] #169\n- [x] #171\n"
        assert parse_tracking_child_ids(body) == ["169"]

    def test_mixed_content(self) -> None:
        body = "## Children\n- [ ] #42\n- [x] #43\nSome text mentioning #99\n- [ ] #44\n"
        assert parse_tracking_child_ids(body) == ["42", "44"]

    def test_no_checklist_items(self) -> None:
        body = "Just a regular issue body with #42 reference."
        assert parse_tracking_child_ids(body) == []

    def test_empty_body(self) -> None:
        assert parse_tracking_child_ids("") == []

    def test_only_checked_items(self) -> None:
        body = "- [x] #167\n- [x] #169\n"
        assert parse_tracking_child_ids(body) == []

    def test_supports_backticked_refs(self) -> None:
        body = "- [ ] `#167`\n- [x] `#169`\n- [ ] `#171`\n"
        assert parse_tracking_child_ids(body) == ["167", "171"]

    def test_supports_indented_items(self) -> None:
        body = "  - [ ] #42\n\t- [ ] `#44`\n"
        assert parse_tracking_child_ids(body) == ["42", "44"]

    def test_include_checked_returns_all_checklist_issue_refs(self) -> None:
        body = "- [x] #167\n- [ ] `#169`\n- [X] #171\n"
        assert parse_tracking_child_ids(body, include_checked=True) == ["167", "169", "171"]


class TestHasChecklistItems:
    def test_detects_unchecked_with_ref(self) -> None:
        assert has_checklist_items("- [ ] #42\n") is True

    def test_detects_checked_with_ref(self) -> None:
        assert has_checklist_items("- [x] #42\n") is True

    def test_detects_uppercase_checked(self) -> None:
        assert has_checklist_items("- [X] `#42`\n") is True

    def test_detects_indented_items(self) -> None:
        assert has_checklist_items("  - [ ] #42\n\t- [x] `#43`\n") is True

    def test_detects_unchecked_without_ref(self) -> None:
        # Checklist line has no inline #N — the ref is on a separate line.
        # has_checklist_items() must still return True so the checklist
        # branch is taken in smart_start, not parse_all_issue_refs().
        body = "- [ ] docs\nSee `#123`"
        assert has_checklist_items(body) is True

    def test_no_checklist(self) -> None:
        assert has_checklist_items("Just a body with #42 ref.") is False

    def test_empty_body(self) -> None:
        assert has_checklist_items("") is False

    def test_mixed_format_regression(self) -> None:
        """Regression: checklist with separate ref must not fall through to parse_all_issue_refs."""
        body = "- [x] Completed task\n- [ ] Pending task\nSee also `#99`\n"
        # has_checklist_items detects the checklist markers
        assert has_checklist_items(body) is True
        # parse_tracking_child_ids only returns unchecked items with inline #N
        assert parse_tracking_child_ids(body) == []
        # parse_all_issue_refs sees every #N in the body
        assert parse_all_issue_refs(body) == ["99"]


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
