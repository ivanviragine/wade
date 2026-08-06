"""Tests for the lean plan-validation core (``wade.utils.plan_validation``).

Covers ``has_valid_plan`` (the per-file notion the plan Stop guard needs) plus
the back-compat re-exports that keep ``from wade.services.plan_service import …``
working after the extraction.
"""

from __future__ import annotations

from pathlib import Path

from wade.utils.plan_validation import (
    discover_plan_files,
    has_valid_plan,
    validate_plan_dir,
)

_VALID = "# feat: add retry logic\n\n## Complexity\ncomplex\n\n## Tasks\n- Do it\n"
_NO_COMPLEXITY = "# feat: add retry logic\n\n## Tasks\n- Do it\n"
_BAD_TITLE = "# add retry logic\n\n## Complexity\ncomplex\n\n## Tasks\n- Do it\n"


def _write(plan_dir: Path, name: str, content: str) -> None:
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / name).write_text(content, encoding="utf-8")


class TestHasValidPlan:
    def test_true_for_a_single_valid_plan(self, tmp_path: Path) -> None:
        _write(tmp_path, "PLAN.md", _VALID)
        assert has_valid_plan(tmp_path) is True

    def test_false_when_dir_missing(self, tmp_path: Path) -> None:
        assert has_valid_plan(tmp_path / "nope") is False

    def test_false_when_no_plan_files(self, tmp_path: Path) -> None:
        # A stray non-PLAN markdown file must not count.
        _write(tmp_path, "README.md", _VALID)
        assert has_valid_plan(tmp_path) is False

    def test_false_when_complexity_missing(self, tmp_path: Path) -> None:
        _write(tmp_path, "PLAN.md", _NO_COMPLEXITY)
        assert has_valid_plan(tmp_path) is False

    def test_false_when_title_prefix_missing(self, tmp_path: Path) -> None:
        _write(tmp_path, "PLAN.md", _BAD_TITLE)
        assert has_valid_plan(tmp_path) is False

    def test_true_when_at_least_one_of_many_is_valid(self, tmp_path: Path) -> None:
        # Mixed batch: one good, two bad — a single valid file is enough.
        _write(tmp_path, "PLAN.md", _VALID)
        _write(tmp_path, "PLAN-2.md", _NO_COMPLEXITY)
        _write(tmp_path, "PLAN-3.md", _BAD_TITLE)
        assert has_valid_plan(tmp_path) is True

    def test_differs_from_validate_plan_dir_has_errors(self, tmp_path: Path) -> None:
        # The key distinction: one valid + one invalid file makes the aggregate
        # ``has_errors`` True, but ``has_valid_plan`` True — the session produced
        # something usable, which is what the Stop nudge cares about.
        _write(tmp_path, "PLAN.md", _VALID)
        _write(tmp_path, "PLAN-2.md", _NO_COMPLEXITY)
        assert validate_plan_dir(tmp_path).has_errors is True
        assert has_valid_plan(tmp_path) is True


class TestBackCompatReExports:
    """``plan_service`` must still re-export the validation API after extraction."""

    def test_names_resolve_from_plan_service(self) -> None:
        from wade.services import plan_service as ps

        # Same object identity — these are re-exports, not copies.
        assert ps.validate_plan_dir is validate_plan_dir
        assert ps.discover_plan_files is discover_plan_files
        assert ps.has_valid_plan is has_valid_plan

    def test_diagnostic_types_re_exported(self) -> None:
        from wade.services.plan_service import (
            PlanDiagnostic,
            PlanDiagnosticLevel,
            PlanValidationResult,
            plan_done,
        )
        from wade.utils import plan_validation as pv

        assert PlanDiagnostic is pv.PlanDiagnostic
        assert PlanDiagnosticLevel is pv.PlanDiagnosticLevel
        assert PlanValidationResult is pv.PlanValidationResult
        assert plan_done is pv.plan_done

    def test_discover_plan_files_still_works_via_re_export(self, tmp_path: Path) -> None:
        from wade.services.plan_service import discover_plan_files as ps_discover

        _write(tmp_path, "PLAN.md", _VALID)
        _write(tmp_path, "PLAN-2.md", _VALID)
        found = ps_discover(tmp_path)
        assert [p.name for p in found] == ["PLAN-2.md", "PLAN.md"]
