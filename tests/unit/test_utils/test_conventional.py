"""Tests for the canonical conventional-commit title validator."""

from __future__ import annotations

import pytest

from wade.utils.conventional import (
    CONVENTIONAL_COMMIT_RE,
    CONVENTIONAL_COMMIT_TYPES,
    ConventionalTitleError,
    conventional_title_error,
    is_conventional_title,
)


class TestIsConventionalTitle:
    @pytest.mark.parametrize(
        "title",
        [
            "feat: add retry logic",
            "fix: correct off-by-one",
            "docs: update readme",
            "refactor: split module",
            "chore: bump deps",
            "style: reflow imports",
            "perf: cache lookups",
            "test: add coverage",
            "ci: tweak workflow",
            "build: pin toolchain",
            "revert: undo change",
            "update: refresh pins",
            "feat(scope): scoped change",
            "feat!: breaking change",
            "feat(scope)!: scoped breaking change",
        ],
    )
    def test_accepts_conventional(self, title: str) -> None:
        assert is_conventional_title(title)

    @pytest.mark.parametrize(
        "title",
        [
            "E3: Session-start & resume context injection",
            "Just a plain title",
            "Fix the bug",  # capitalized non-type word
            "feature: not a real type",
            "feat:no space after colon",
            "feat:",  # no description
            "",
        ],
    )
    def test_rejects_non_conventional(self, title: str) -> None:
        assert not is_conventional_title(title)

    def test_every_canonical_type_is_accepted(self) -> None:
        for t in CONVENTIONAL_COMMIT_TYPES:
            assert is_conventional_title(f"{t}: something")

    def test_canonical_list_matches_ci_lint(self) -> None:
        # The 12 types must match pr-title-lint.yml exactly (the CI check this
        # feature exists to satisfy). Guards against silent drift.
        assert set(CONVENTIONAL_COMMIT_TYPES) == {
            "feat",
            "fix",
            "docs",
            "refactor",
            "chore",
            "style",
            "perf",
            "test",
            "ci",
            "build",
            "revert",
            "update",
        }


class TestPlanValidationSharesRegex:
    def test_plan_validation_imports_canonical_regex(self) -> None:
        # plan_validation must source the regex from utils/conventional so
        # `wade plan` and issue-creation enforcement never diverge.
        from wade.utils import plan_validation

        assert plan_validation._CONVENTIONAL_COMMIT_RE is CONVENTIONAL_COMMIT_RE


class TestErrorHelpers:
    def test_error_string_is_actionable(self) -> None:
        msg = conventional_title_error("E3: foo")
        assert "E3: foo" in msg
        assert "conventional commit prefix" in msg
        assert "feat" in msg
        assert "update" in msg

    def test_exception_carries_title_and_message(self) -> None:
        err = ConventionalTitleError("bad title")
        assert err.title == "bad title"
        assert isinstance(err, ValueError)
        assert "bad title" in str(err)
