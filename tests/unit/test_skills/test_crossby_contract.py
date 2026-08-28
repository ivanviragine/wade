"""Pinned Crossby contracts used by WADE project-skill discovery.

Re-run and inspect this file whenever the Crossby dependency range changes.
These APIs intentionally include a semi-private scan-order contract.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from crossby.config.skills import (
    SKILLS_DIR,
    detect_skills_source,
    get_skills_target,
    list_skills,
)
from crossby.models.ai import AIToolID
from crossby.sync.readers import detect_skills


def _skill(root: Path, relative: str, name: str) -> Path:
    skill = root / relative / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    return skill


def test_crossby_version_and_skill_root_mapping_contract() -> None:
    assert version("crossby") == "0.24.4"
    assert SKILLS_DIR == {
        AIToolID.CLAUDE: ".claude/skills",
        AIToolID.CURSOR: ".cursor/skills",
        AIToolID.CODEX: ".agents/skills",
        AIToolID.ANTIGRAVITY_CLI: ".agents/skills",
        AIToolID.COPILOT: ".github/skills",
    }
    assert set(AIToolID) - set(SKILLS_DIR) == {
        AIToolID.ANTIGRAVITY,
        AIToolID.VSCODE,
        AIToolID.OPENCODE,
    }


def test_crossby_detects_symlinked_roots_and_skill_directories(tmp_path: Path) -> None:
    _skill(tmp_path, ".claude/skills", "alpha")
    agents = tmp_path / ".agents"
    agents.mkdir()
    (agents / "skills").symlink_to(Path("../.claude/skills"))
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "skills").symlink_to(Path("../.claude/skills"))

    found = detect_skills(tmp_path)
    assert found[AIToolID.CLAUDE] == ".claude/skills"
    assert found[AIToolID.CODEX] == ".agents/skills"
    assert found[AIToolID.ANTIGRAVITY_CLI] == ".agents/skills"
    assert found[AIToolID.CURSOR] == ".cursor/skills"
    assert list_skills(tmp_path / found[AIToolID.CODEX]) == ["alpha"]


def test_crossby_source_prefers_real_claude_root_and_targets_are_project_relative(
    tmp_path: Path,
) -> None:
    _skill(tmp_path, ".claude/skills", "alpha")
    _skill(tmp_path, ".agents/skills", "beta")

    assert detect_skills_source(tmp_path) == tmp_path / ".claude/skills"
    assert get_skills_target(AIToolID.CODEX, tmp_path) == tmp_path / ".agents/skills"
    assert get_skills_target(AIToolID.OPENCODE, tmp_path) is None


def test_crossby_list_skills_is_sorted_and_requires_skill_markdown(
    tmp_path: Path,
) -> None:
    _skill(tmp_path, ".claude/skills", "zeta")
    _skill(tmp_path, ".claude/skills", "alpha")
    (tmp_path / ".claude/skills/not-a-skill").mkdir()

    assert list_skills(tmp_path / ".claude/skills") == ["alpha", "zeta"]
