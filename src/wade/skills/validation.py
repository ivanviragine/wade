"""Filesystem-safe skill inspection and deterministic content hashing."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

MAX_SKILL_FILES = 256
MAX_SKILL_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024


class SkillValidationError(ValueError):
    """A skill source is unreadable, unsafe, malformed, or oversized."""


@dataclass(frozen=True)
class InspectedSkill:
    """Validated regular files and their stable digest."""

    files: tuple[str, ...]
    digest: str
    total_bytes: int


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _collect_files(
    directory: Path,
    *,
    project_root: Path,
    relative_prefix: Path = Path(),
    ancestors: frozenset[Path] = frozenset(),
) -> list[tuple[str, Path]]:
    try:
        resolved_dir = directory.resolve(strict=True)
    except OSError as exc:
        raise SkillValidationError(f"Cannot resolve skill directory {directory}: {exc}") from exc
    project_real = project_root.resolve(strict=True)
    if not _is_within(resolved_dir, project_real):
        raise SkillValidationError(f"Skill path escapes the project: {directory}")
    if resolved_dir in ancestors:
        raise SkillValidationError(f"Symlink cycle in skill directory: {directory}")

    collected: list[tuple[str, Path]] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise SkillValidationError(f"Cannot enumerate skill directory {directory}: {exc}") from exc

    next_ancestors = ancestors | {resolved_dir}
    for entry in entries:
        relative = relative_prefix / entry.name
        try:
            mode = entry.lstat().st_mode
        except OSError as exc:
            raise SkillValidationError(f"Cannot inspect skill entry {entry}: {exc}") from exc

        if stat.S_ISLNK(mode):
            try:
                target = entry.resolve(strict=True)
            except OSError as exc:
                raise SkillValidationError(f"Broken skill symlink {entry}: {exc}") from exc
            if not _is_within(target, project_real):
                raise SkillValidationError(f"Skill symlink escapes the project: {entry}")
            try:
                target_mode = target.stat().st_mode
            except OSError as exc:
                raise SkillValidationError(
                    f"Cannot inspect skill symlink target {entry}: {exc}"
                ) from exc
            if stat.S_ISDIR(target_mode):
                collected.extend(
                    _collect_files(
                        entry,
                        project_root=project_real,
                        relative_prefix=relative,
                        ancestors=next_ancestors,
                    )
                )
            elif stat.S_ISREG(target_mode):
                collected.append((relative.as_posix(), target))
            else:
                raise SkillValidationError(f"Skill symlink targets a special file: {entry}")
        elif stat.S_ISDIR(mode):
            collected.extend(
                _collect_files(
                    entry,
                    project_root=project_real,
                    relative_prefix=relative,
                    ancestors=next_ancestors,
                )
            )
        elif stat.S_ISREG(mode):
            collected.append((relative.as_posix(), entry))
        else:
            raise SkillValidationError(f"Special files are not allowed in skills: {entry}")

    return collected


def inspect_skill(directory: Path, *, project_root: Path) -> InspectedSkill:
    """Validate a complete skill tree and calculate an order-stable digest."""

    if not (directory / "SKILL.md").is_file():
        raise SkillValidationError(f"Skill directory has no SKILL.md: {directory}")
    files = _collect_files(directory, project_root=project_root)
    if len(files) > MAX_SKILL_FILES:
        raise SkillValidationError(
            f"Skill has {len(files)} files; limit is {MAX_SKILL_FILES}: {directory}"
        )

    digest = hashlib.sha256()
    total = 0
    relative_names: list[str] = []
    for relative, source in sorted(files):
        try:
            size = source.stat().st_size
        except OSError as exc:
            raise SkillValidationError(f"Cannot stat skill file {source}: {exc}") from exc
        if size > MAX_FILE_BYTES:
            raise SkillValidationError(
                f"Skill file is {size} bytes; per-file limit is {MAX_FILE_BYTES}: {relative}"
            )
        total += size
        if total > MAX_SKILL_BYTES:
            raise SkillValidationError(
                f"Skill is {total} bytes; limit is {MAX_SKILL_BYTES}: {directory}"
            )
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise SkillValidationError(f"Cannot read skill file {source}: {exc}") from exc
        encoded_name = relative.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        relative_names.append(relative)

    return InspectedSkill(
        files=tuple(relative_names),
        digest=f"sha256:{digest.hexdigest()}",
        total_bytes=total,
    )


def copy_inspected_skill(
    source: Path,
    destination: Path,
    *,
    project_root: Path,
    inspected: InspectedSkill,
) -> None:
    """Copy validated files without preserving symlinks or metadata side effects."""

    for relative in inspected.files:
        source_file = (source / relative).resolve(strict=True)
        if not _is_within(source_file, project_root.resolve(strict=True)):
            raise SkillValidationError(f"Skill source changed to escape the project: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(source_file, flags)
            with os.fdopen(descriptor, "rb") as handle:
                content = handle.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            raise SkillValidationError(f"Cannot safely read skill file {relative}: {exc}") from exc
        if len(content) > MAX_FILE_BYTES:
            raise SkillValidationError(f"Skill file grew beyond its limit: {relative}")
        target.write_bytes(content)

    copied = inspect_skill(destination, project_root=destination)
    if copied.digest != inspected.digest or copied.files != inspected.files:
        raise SkillValidationError("Skill source changed while it was being snapshotted")
