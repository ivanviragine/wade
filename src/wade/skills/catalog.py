"""Built-in and project skill catalogs."""

from __future__ import annotations

from pathlib import Path

from wade.models.skill import SkillDescriptor
from wade.skills.discovery import DiscoveredSkill
from wade.skills.validation import inspect_skill
from wade.utils.templates import get_skills_templates_dir

BUILTIN_METHODOLOGY_SKILLS: tuple[str, ...] = (
    "planning",
    "implementation",
    "review-comments",
    "plan-review",
    "code-review",
    "batch-review",
    "dependency-analysis",
)


def builtin_skills(templates: Path | None = None) -> dict[str, DiscoveredSkill]:
    """Return validated descriptors for replaceable methodology skills.

    ``templates`` is set only for WADE self-init worktrees, where live branch
    edits must be frozen from that worktree rather than from the installed
    package checkout.
    """

    templates = templates or get_skills_templates_dir()
    package_root = templates.parent.parent
    result: dict[str, DiscoveredSkill] = {}
    for name in BUILTIN_METHODOLOGY_SKILLS:
        source = templates / name
        inspected = inspect_skill(source, project_root=package_root)
        descriptor = SkillDescriptor(
            name=name,
            canonical_ref=f"builtin:{name}",
            source_root="templates/skills",
            source_path=f"templates/skills/{name}",
            description=_builtin_description(source / "SKILL.md"),
            files=inspected.files,
            content_digest=inspected.digest,
        )
        result[name] = DiscoveredSkill(
            descriptor=descriptor,
            source=source,
            project_root=package_root,
            origin="builtin",
            inspected=inspected,
        )
    return result


def _builtin_description(skill_md: Path) -> str:
    for line in skill_md.read_text(encoding="utf-8").splitlines():
        if line.startswith("description:"):
            return line.partition(":")[2].strip()
    return ""
