"""Crossby-backed project skill discovery with deterministic deduplication."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml
from crossby.config.skills import list_skills
from crossby.sync.readers import detect_skills

from wade.models.skill import SkillDescriptor
from wade.skills.validation import InspectedSkill, SkillValidationError, inspect_skill
from wade.utils.templates import get_skills_templates_dir, get_wade_repo_root


@dataclass(frozen=True)
class DiscoveredSkill:
    """A public descriptor paired with its process-local source path."""

    descriptor: SkillDescriptor
    source: Path
    project_root: Path
    origin: str
    inspected: InspectedSkill


@dataclass(frozen=True)
class SkillInventory:
    """Filtered discovered skills plus non-fatal tool coverage warnings."""

    skills: tuple[DiscoveredSkill, ...]
    warnings: tuple[str, ...] = ()
    builtin_templates: Path | None = None


def self_init_builtin_templates(worktree_root: Path, main_root: Path) -> Path | None:
    """Return the live WADE-worktree builtin source, or ``None`` for target projects."""

    try:
        is_self_init = main_root.resolve() == get_wade_repo_root().resolve()
    except OSError:
        return None
    candidate = worktree_root / "templates" / "skills"
    return candidate if is_self_init and candidate.is_dir() else None


def _description(skill_md: Path) -> str:
    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    if not content.startswith("---\n"):
        return ""
    _, separator, _rest = content.partition("\n---\n")
    if not separator:
        return ""
    try:
        frontmatter = yaml.safe_load(content[4:].split("\n---\n", 1)[0])
    except yaml.YAMLError:
        return ""
    if isinstance(frontmatter, dict) and isinstance(frontmatter.get("description"), str):
        return " ".join(frontmatter["description"].split())
    return ""


def _matches(value: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def _selected_by_filters(
    name: str,
    source_path: str,
    include_patterns: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
) -> bool:
    match_values = (name, source_path, f"project:{name}")
    return any(_matches(value, include_patterns) for value in match_values) and not any(
        _matches(value, exclude_patterns) for value in match_values
    )


def _root_label(relative_root: str) -> str:
    return relative_root.strip(".").replace("/", "-").replace(".", "") or "root"


def _scan_checkout(
    root: Path,
    *,
    origin: str,
    exclude_real_roots: tuple[Path, ...],
    include_patterns: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
) -> list[DiscoveredSkill]:
    results: list[DiscoveredSkill] = []
    seen_roots: set[Path] = set()
    found = detect_skills(root)
    for relative_root in found.values():
        skills_root = root / relative_root
        try:
            real_root = skills_root.resolve(strict=True)
        except OSError:
            continue
        if real_root in seen_roots:
            continue
        seen_roots.add(real_root)
        if any(_is_relative_to(real_root, excluded) for excluded in exclude_real_roots):
            continue

        for name in list_skills(skills_root):
            source_path = f"{relative_root}/{name}"
            if not _selected_by_filters(
                name,
                source_path,
                include_patterns,
                exclude_patterns,
            ):
                continue
            source = skills_root / name
            try:
                real_source = source.resolve(strict=True)
            except OSError:
                continue
            if any(_is_relative_to(real_source, excluded) for excluded in exclude_real_roots):
                continue
            inspected = inspect_skill(source, project_root=root)
            descriptor = SkillDescriptor(
                name=name,
                canonical_ref=f"project:{name}",
                source_root=relative_root,
                source_path=source_path,
                description=_description(source / "SKILL.md"),
                files=inspected.files,
                content_digest=inspected.digest,
            )
            results.append(
                DiscoveredSkill(
                    descriptor=descriptor,
                    source=source,
                    project_root=root,
                    origin=origin,
                    inspected=inspected,
                )
            )
    return results


def _projection_warning(root: Path) -> str | None:
    """Detect Crossby's filtered scene projection and require a full anchor root."""

    projection = root / ".crossby" / "scene" / "active" / "skills"
    try:
        projection_real = projection.resolve(strict=True)
    except OSError:
        return None
    projected = False
    full_source = False
    for relative_root in detect_skills(root).values():
        try:
            resolved = (root / relative_root).resolve(strict=True)
        except OSError:
            continue
        if _is_relative_to(resolved, projection_real):
            projected = True
        else:
            full_source = True
    if projected and not full_source:
        raise SkillValidationError(
            "Crossby scene skill projection is active, but no unfiltered project skill "
            "source is discoverable"
        )
    if projected:
        return (
            "Crossby scene projection detected; WADE ignored it as a complete inventory "
            "and also scanned the unfiltered skill source"
        )
    return None


def discover_project_skills(
    worktree_root: Path,
    main_root: Path,
    *,
    include: Iterable[str] = ("*",),
    exclude: Iterable[str] = (),
) -> SkillInventory:
    """Discover supported worktree and main-checkout skills without mutating sources."""

    include_patterns = tuple(include)
    exclude_patterns = tuple(exclude)
    if not include_patterns:
        return SkillInventory(
            skills=(),
            builtin_templates=self_init_builtin_templates(worktree_root, main_root),
        )
    packaged = get_skills_templates_dir().resolve()
    live_builtins = self_init_builtin_templates(worktree_root, main_root)
    excluded_roots = tuple(
        dict.fromkeys(
            (
                packaged,
                *((live_builtins.resolve(),) if live_builtins is not None else ()),
            )
        )
    )

    warnings = tuple(
        dict.fromkeys(
            warning
            for root in (worktree_root, main_root)
            if (warning := _projection_warning(root)) is not None
        )
    )

    # Worktree wins for the same source-relative identity. Main-only local or
    # ignored paths are then added. Identical real paths and digests deduplicate.
    candidates = _scan_checkout(
        worktree_root,
        origin="worktree",
        exclude_real_roots=excluded_roots,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
    )
    if main_root.resolve() != worktree_root.resolve():
        candidates.extend(
            _scan_checkout(
                main_root,
                origin="main",
                exclude_real_roots=excluded_roots,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
            )
        )

    selected: list[DiscoveredSkill] = []
    seen_identity: set[tuple[str, str]] = set()
    seen_real_or_digest: set[tuple[str, str]] = set()
    for candidate in candidates:
        descriptor = candidate.descriptor
        identity = (descriptor.source_root, descriptor.name)
        if identity in seen_identity:
            continue
        seen_identity.add(identity)
        real_key = (str(candidate.source.resolve()), descriptor.content_digest)
        digest_key = (descriptor.name, descriptor.content_digest)
        if real_key in seen_real_or_digest or digest_key in seen_real_or_digest:
            continue
        seen_real_or_digest.add(real_key)
        seen_real_or_digest.add(digest_key)
        selected.append(candidate)

    selected.sort(
        key=lambda item: (
            item.descriptor.name,
            0 if item.origin == "worktree" else 1,
            item.descriptor.source_path,
        )
    )
    return SkillInventory(
        skills=tuple(selected),
        warnings=warnings,
        builtin_templates=live_builtins,
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def project_materialized_subpath(skill: DiscoveredSkill) -> Path:
    """Return the collision-safe relative snapshot path for a project skill."""

    return (
        Path("skills/project") / _root_label(skill.descriptor.source_root) / skill.descriptor.name
    )


__all__ = [
    "DiscoveredSkill",
    "SkillInventory",
    "SkillValidationError",
    "discover_project_skills",
    "project_materialized_subpath",
    "self_init_builtin_templates",
]
