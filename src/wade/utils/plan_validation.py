"""Lean plan-file validation core — pure, import-cheap, no UI/crossby deps.

Extracted from :mod:`wade.services.plan_service` so the lean ``wade-hook`` entry
point (:mod:`wade.hooks.cli`) can reuse the strict validator on the Stop path
**without** importing ``plan_service`` — which eagerly pulls in
``crossby.ai_tools`` (~450ms cold start). Everything here imports only stdlib +
pydantic + :class:`wade.models.task.PlanFile` (itself stdlib + pydantic), so
importing it adds negligible cost on top of pydantic, which is already loaded.

``plan_service`` re-exports these names for back-compat, so existing
``from wade.services.plan_service import validate_plan_dir, plan_done, …`` imports
keep working. The one validator that stays in ``plan_service`` is
``validate_plan_files`` — it calls ``console.warn`` (a UI dependency that must not
leak into ``utils/``).

Layering: ``utils/`` may import ``models/``; ``hooks/policies.py`` already imports
``wade.utils.markers``, so a second lean ``wade.utils`` module on the hook path is
consistent.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from wade.models.task import PlanFile
from wade.utils.conventional import (
    CONVENTIONAL_COMMIT_RE as _CONVENTIONAL_COMMIT_RE,
)
from wade.utils.conventional import (
    conventional_title_error,
)

# ---------------------------------------------------------------------------
# Plan file discovery
# ---------------------------------------------------------------------------


def discover_plan_files(plan_dir: Path) -> list[Path]:
    """Find PLAN*.md files in the plan directory, sorted by name."""
    if not plan_dir.is_dir():
        return []
    return sorted(plan_dir.glob("PLAN*.md"))


# ---------------------------------------------------------------------------
# Plan-done validation (deterministic gate for planning sessions)
# ---------------------------------------------------------------------------

_RECOMMENDED_SECTIONS = ("tasks", "acceptance criteria")


class PlanDiagnosticLevel(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class PlanDiagnostic(BaseModel):
    """A single diagnostic message for a plan file."""

    file: str
    level: PlanDiagnosticLevel
    message: str


class PlanValidationResult(BaseModel):
    """Aggregated validation result for all plan files in a directory."""

    diagnostics: list[PlanDiagnostic] = []

    @property
    def errors(self) -> list[PlanDiagnostic]:
        return [d for d in self.diagnostics if d.level == PlanDiagnosticLevel.ERROR]

    @property
    def warnings(self) -> list[PlanDiagnostic]:
        return [d for d in self.diagnostics if d.level == PlanDiagnosticLevel.WARNING]

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


def validate_plan_dir(plan_dir: Path) -> PlanValidationResult:
    """Validate all plan files in the directory.

    Collects all errors and warnings across every discovered ``PLAN*.md`` file.

    Errors (exit 1):
    - No plan files found
    - Missing ``# Title`` heading
    - Missing or invalid ``## Complexity`` section

    Warnings (exit 0):
    - Missing recommended sections (``## Tasks``, ``## Acceptance Criteria``)
    """
    result = PlanValidationResult()
    md_files = discover_plan_files(plan_dir)

    if not md_files:
        result.diagnostics.append(
            PlanDiagnostic(
                file="(none)",
                level=PlanDiagnosticLevel.ERROR,
                message="No plan files (PLAN*.md) found in the plan directory.",
            )
        )
        return result

    for md_file in md_files:
        try:
            plan = PlanFile.from_markdown(md_file)
        except (ValueError, OSError) as e:
            result.diagnostics.append(
                PlanDiagnostic(file=md_file.name, level=PlanDiagnosticLevel.ERROR, message=str(e))
            )
            continue

        if plan.complexity is None:
            result.diagnostics.append(
                PlanDiagnostic(
                    file=md_file.name,
                    level=PlanDiagnosticLevel.ERROR,
                    message=(
                        "Missing or invalid '## Complexity' section. "
                        "Must be one of: easy, medium, complex, very_complex."
                    ),
                )
            )

        if not _CONVENTIONAL_COMMIT_RE.match(plan.title):
            result.diagnostics.append(
                PlanDiagnostic(
                    file=md_file.name,
                    level=PlanDiagnosticLevel.ERROR,
                    message=conventional_title_error(plan.title),
                )
            )

        for section in _RECOMMENDED_SECTIONS:
            if section not in plan.sections:
                heading = section.title()
                result.diagnostics.append(
                    PlanDiagnostic(
                        file=md_file.name,
                        level=PlanDiagnosticLevel.WARNING,
                        message=f"Missing recommended section: '## {heading}'.",
                    )
                )

    return result


def plan_done(plan_dir: Path) -> PlanValidationResult:
    """Validate plan files and return aggregated diagnostics.

    The caller is responsible for rendering results and determining the exit code.
    Use ``result.has_errors`` to check whether validation passed.
    """
    return validate_plan_dir(plan_dir)


def has_valid_plan(plan_dir: Path) -> bool:
    """True iff at least one ``PLAN*.md`` file has **no error-level** diagnostics.

    This is the per-file notion the plan Stop guard needs: a single parseable
    title (with a conventional-commit prefix) **and** a valid ``## Complexity`` is
    enough to say "the session produced something wade can turn into an issue".

    Note this differs from ``validate_plan_dir(...).has_errors``, which is ``True``
    if *any* file is invalid even when a valid one also exists — that aggregate is
    the right gate for ``plan-session done`` (fix everything), but the wrong one
    for the Stop nudge (did the session produce *anything* usable).
    """
    md_files = discover_plan_files(plan_dir)
    if not md_files:
        return False
    files_with_errors = {d.file for d in validate_plan_dir(plan_dir).errors}
    return any(md_file.name not in files_with_errors for md_file in md_files)
