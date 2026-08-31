"""Replaceable built-ins must remain workflow-agnostic."""

from __future__ import annotations

from pathlib import Path

import pytest

from wade.skills.catalog import BUILTIN_METHODOLOGY_SKILLS

_SKILLS = Path(__file__).resolve().parents[3] / "templates/skills"
_FORBIDDEN = ("wade ", ".wade/", "implementation-session", "plan-session", "reviewed@")


@pytest.mark.parametrize("name", BUILTIN_METHODOLOGY_SKILLS)
def test_replaceable_builtin_contains_no_wade_lifecycle_tokens(name: str) -> None:
    content = (_SKILLS / name / "SKILL.md").read_text(encoding="utf-8").lower()
    for token in _FORBIDDEN:
        assert token not in content, f"{name} contains workflow-owned token {token!r}"
