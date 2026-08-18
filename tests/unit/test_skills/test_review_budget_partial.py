"""Tests for the canonical review-budget skill partial (#450)."""

from __future__ import annotations

from wade.skills.installer import SKILL_FILES, _expand_partials, get_skills_templates_dir

# The three session skills that reference {review_budget_notes} — review_batch
# has no session skill by design (see the comment above _SKILL_PARTIALS), so it
# is intentionally excluded here.
_SKILLS_REFERENCING_REVIEW_BUDGET = [
    "implementation-session",
    "plan-session",
    "review-pr-comments-session",
]


class TestReviewBudgetPartialExpansion:
    def test_each_referencing_skill_declares_the_placeholder(self) -> None:
        skills_dir = get_skills_templates_dir()
        for skill_name in _SKILLS_REFERENCING_REVIEW_BUDGET:
            raw = (skills_dir / skill_name / "SKILL.md").read_text(encoding="utf-8")
            assert "{review_budget_notes}" in raw, (
                f"{skill_name}/SKILL.md no longer references {{review_budget_notes}}"
            )

    def test_placeholder_expands_and_pulls_in_the_canonical_content(self) -> None:
        skills_dir = get_skills_templates_dir()
        for skill_name in _SKILLS_REFERENCING_REVIEW_BUDGET:
            raw = (skills_dir / skill_name / "SKILL.md").read_text(encoding="utf-8")
            rendered = _expand_partials(raw, skills_dir)
            assert "{review_budget_notes}" not in rendered
            assert "Review budget & skip guidance" in rendered
            assert "done.max_review_passes" in rendered
            assert "Trade-off, stated plainly" in rendered


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
        partials_dir = get_skills_templates_dir() / "_partials"
        md_files = list(partials_dir.glob("*.md"))
        assert md_files  # sanity: the directory has partials
        for md_file in md_files:
            text = md_file.read_text(encoding="utf-8")
            assert "600s" not in text, f"{md_file} still hardcodes the old 600s timeout"
            assert "at most 2 times" not in text.lower(), (
                f"{md_file} still hardcodes the old pass-count literal"
            )
