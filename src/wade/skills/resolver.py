"""Pure precedence and skill-reference resolution."""

from __future__ import annotations

from dataclasses import dataclass

from wade.models.skill import SkillRef, SkillSource
from wade.skills.catalog import builtin_skills
from wade.skills.discovery import DiscoveredSkill, SkillInventory


class SkillResolutionError(ValueError):
    """An active skill reference cannot be resolved deterministically."""


@dataclass(frozen=True)
class SkillSelection:
    """A caller's canonical reference paired with the chosen descriptor."""

    ref: SkillRef
    skill: DiscoveredSkill


def resolve_skill_refs(
    refs: tuple[SkillRef, ...],
    inventory: SkillInventory,
) -> tuple[SkillSelection, ...]:
    """Resolve ordered refs; active unknown, ambiguous, or excluded refs fail."""

    if not refs:
        raise SkillResolutionError("A skill binding must contain at least one reference")
    builtins = builtin_skills(inventory.builtin_templates)
    resolved: list[SkillSelection] = []
    for ref in refs:
        if ref.source is SkillSource.BUILTIN:
            skill = builtins.get(ref.value)
            if skill is None:
                raise SkillResolutionError(f"Unknown built-in skill: {ref.canonical}")
            resolved.append(SkillSelection(ref=ref, skill=skill))
            continue

        if ref.source is SkillSource.PROJECT:
            matches = [item for item in inventory.skills if item.descriptor.name == ref.value]
        else:
            matches = [
                item for item in inventory.skills if item.descriptor.source_path == ref.value
            ]
        if not matches:
            raise SkillResolutionError(
                f"Skill {ref.canonical!r} was not found in the selected project inventory"
            )
        digests = {item.descriptor.content_digest for item in matches}
        if len(digests) > 1:
            paths = ", ".join(item.descriptor.source_path for item in matches)
            raise SkillResolutionError(
                f"Skill {ref.canonical!r} is ambiguous ({paths}); use an explicit path: ref"
            )
        resolved.append(SkillSelection(ref=ref, skill=matches[0]))
    return tuple(resolved)
