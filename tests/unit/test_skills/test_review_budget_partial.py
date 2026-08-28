"""Tests for the canonical fixed-workflow review-budget partial."""

from __future__ import annotations

from wade.skills.installer import SKILL_FILES, get_skills_templates_dir
from wade.utils.templates import get_workflows_templates_dir

_WORKFLOWS_REFERENCING_REVIEW_BUDGET = ["implementation.md", "review-pr-comments.md"]


class TestReviewBudgetWorkflowPartial:
    def test_each_referencing_workflow_declares_the_placeholder(self) -> None:
        workflows_dir = get_workflows_templates_dir()
        for workflow_name in _WORKFLOWS_REFERENCING_REVIEW_BUDGET:
            raw = (workflows_dir / workflow_name).read_text(encoding="utf-8")
            assert "{review_budget}" in raw

    def test_canonical_content_is_lifecycle_not_methodology(self) -> None:
        partial = (get_workflows_templates_dir() / "_partials" / "review-budget.md").read_text(
            encoding="utf-8"
        )
        assert "done.max_review_passes" in partial
        assert "may be skipped only" in partial
        assert "WADE" not in partial


class TestNoStaleBudgetLiterals:
    """The old hardcoded 600s timeout and 'at most 2 times' pass count must be gone."""

    def test_no_skill_file_hardcodes_the_old_literals(self) -> None:
        skills_dir = get_skills_templates_dir()
        checked = 0
        for skill_name in SKILL_FILES:
            skill_dir = skills_dir / skill_name
            if not skill_dir.is_dir():
                continue
            for md_file in skill_dir.rglob("*.md"):
                checked += 1
                text = md_file.read_text(encoding="utf-8")
                assert "600s" not in text, f"{md_file} still hardcodes the old 600s timeout"
                assert "at most 2 times" not in text.lower(), (
                    f"{md_file} still hardcodes the old pass-count literal"
                )
        assert checked > 0  # sanity: the walk actually found files

    def test_no_partial_hardcodes_the_old_literals(self) -> None:
        md_files = list((get_skills_templates_dir() / "_partials").glob("*.md"))
        md_files.extend((get_workflows_templates_dir() / "_partials").glob("*.md"))
        assert md_files  # sanity: the directory has partials
        for md_file in md_files:
            text = md_file.read_text(encoding="utf-8")
            assert "600s" not in text, f"{md_file} still hardcodes the old 600s timeout"
            assert "at most 2 times" not in text.lower(), (
                f"{md_file} still hardcodes the old pass-count literal"
            )
