"""Rendered session-start payload budget regression test.

Every wade session opens with a launch prompt that inlines the phase
``SKILL.md`` (via the bare ``@`` reference). This test pins the combined size of
that payload — launch prompt + rendered ``SKILL.md`` (partials expanded, reviews
enabled) — under a fixed char ceiling so the context budget cannot silently
regress. See ``docs/dev/skills-system.md`` for the ownership model behind the
budget.

The unit is **chars** (a deliberate proxy for tokens).
"""

from __future__ import annotations

import pytest

from wade.skills.doc_targets import format_doc_targets
from wade.skills.installer import (
    _expand_partials,
    get_skills_templates_dir,
    load_prompt_template,
)

# Session-start payload ceiling (launch prompt + rendered SKILL.md), in chars.
# Bumped 8000 -> 8200 for #392: the implementation-session skill gained a short,
# load-bearing note that the issue title (and thus the derived PR title) must be
# conventional-commit format — code now enforces this at `done`, and the note
# tells the agent how to react.
# Bumped 8200 -> 8400 for #367: `done` now records the review outcome (reviewed /
# skipped / gate-disabled, with the pass count) as a `## Review Status` block in
# the PR body, and the closing-step partial gained a short, load-bearing note that
# `--skip-review` is therefore visible, not a silent shortcut. The guard still
# catches *silent* regressions; this bump is the explicit, reviewed adjustment it
# is designed to force.
# Bumped 8400 -> 9100 for #401: the shared `{user_interaction_prompt}` partial
# gained a short "Communication style — report by exception" section (inherited by
# all three session skills) so sessions stay terse on success and surface
# problems/decisions with complexity + a recommendation. The added text was
# trimmed to its minimum first (and offset by cutting per-step narration); this
# bump is the explicit, reviewed adjustment the guard is designed to force, and it
# still catches *silent* regressions.
# Bumped 9100 -> 9400 for #423: the implementation-session "First action" block
# gained a short, load-bearing `WORKTREE_GIT_BLOCKED` case (exit code 3) telling
# the agent how to react when a worktree's git metadata is not writable under a
# Codex sandbox. The note was trimmed to its minimum first; this bump is the
# explicit, reviewed adjustment the guard is designed to force.
# Bumped 9400 -> 10500 for #435: the shared `{user_interaction_prompt}` partial
# gained three load-bearing communication rules — a sharpened terse/nudge-vs-noise
# split, a broadened "always state the recommended action" clause (previously
# scoped to formal decisions only), and a new session-completion vocabulary rule
# (never signal complete/done/safe-to-exit before the `done` gate succeeds; after
# it, pair "complete" only with an imperative "exit"). `{doc_update_step}` also
# gained a one-line "state the outcome in one line" nudge. The text was written at
# minimal length and offset by trimming the "Present results" narration; this bump
# is the explicit, reviewed adjustment the guard is designed to force, and it
# still catches *silent* regressions.
# Bumped 10500 -> 11000 for #443: the shared `{user_interaction_prompt}` partial
# gained the standardized session-summary convention — end every session with an
# emoji step-status summary (✅/⚠️/❌ per step, handles, an explicit attention line,
# a bold `Next:` action) and make every finite-choice decision a native dialog with
# a `(recommended)` first option whose labels name the next step (open-ended prompts
# stay plain text). The detailed legend + worked examples live in the on-demand
# reference file `task/reference/session-summary-format.md` (NOT budget-counted); only
# a terse, self-sufficient trigger is inline, and the per-session "Present results"
# steps were rewritten to the convention. The inline text was trimmed to its minimum
# first; this bump is the explicit, reviewed adjustment the guard is designed to
# force, and it still catches *silent* regressions.
# Bumped 11000 -> 13700 for #450: a new canonical `{review_budget_notes}` partial
# (time budget, pass-cap asymmetry across implementation/plan/pr-comments,
# timeout-vs-error pattern-matching, and the trivial-change skip criteria +
# AI-judgment trade-off) is now referenced by all three session skills — by
# design the *same* ~2.2k-char block appears in each render so every review
# workflow states identical guidance (replacing the drifting "600s" /
# "at most 2 times" literals it superseded). This is the largest single bump so
# far because the content itself — not incidental prose — is new; it was
# trimmed for length before bumping. Still catches *silent* regressions.
BUDGET_CHARS = 13700

# (session label, launch prompt template, phase skill dir name)
_SESSIONS = [
    ("implement", "implement-context.md", "implementation-session"),
    ("plan", "plan-session.md", "plan-session"),
    ("review", "review-pr-comments.md", "review-pr-comments-session"),
]

# ``{doc_targets}`` is computed per project at install time, not read from a
# partial file, so ``_expand_partials`` alone would leave the 14-char
# placeholder in place and under-measure every real install. Render it with the
# largest set the detector can produce (all root doc files + ``docs/``) so the
# budget reflects a worst-case project rather than the literal template.
_DOC_TARGETS_WORST_CASE = format_doc_targets(
    ["README.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "docs/"]
)


def _rendered_skill(skill_name: str) -> str:
    """Render a phase SKILL.md the way the installer does (default partials)."""
    skills_dir = get_skills_templates_dir()
    raw = (skills_dir / skill_name / "SKILL.md").read_text(encoding="utf-8")
    return _expand_partials(
        raw,
        skills_dir,
        extra_partials={"{doc_targets}": _DOC_TARGETS_WORST_CASE},
    )


@pytest.mark.parametrize(
    ("label", "prompt_file", "skill_name"),
    _SESSIONS,
    ids=[s[0] for s in _SESSIONS],
)
def test_session_start_payload_within_budget(label: str, prompt_file: str, skill_name: str) -> None:
    """Launch prompt + rendered SKILL.md stays within the char budget."""
    prompt = load_prompt_template(prompt_file)
    rendered_skill = _rendered_skill(skill_name)

    total = len(prompt) + len(rendered_skill)
    assert total <= BUDGET_CHARS, (
        f"{label} session-start payload is {total} chars "
        f"(prompt={len(prompt)}, skill={len(rendered_skill)}), "
        f"over the {BUDGET_CHARS}-char budget by {total - BUDGET_CHARS}."
    )
