"""Pure skill identity and binding models.

These models describe *what* methodology a workflow or bounded delegation uses.
They deliberately contain no WADE lifecycle behavior and perform no filesystem
access; discovery, validation, and materialization live in :mod:`wade.skills`.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

_NAMED_SKILL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class SkillSource(StrEnum):
    """Namespace used to resolve a skill reference."""

    BUILTIN = "builtin"
    PROJECT = "project"
    PATH = "path"


class SkillSlot(StrEnum):
    """Stable methodology slots exposed by session and delegation definitions."""

    WORK = "work"
    REVIEW = "review"


class SkillRef(BaseModel, frozen=True):
    """A validated ``builtin:``, ``project:``, or repository-relative ``path:`` ref."""

    source: SkillSource
    value: str

    @model_validator(mode="before")
    @classmethod
    def _parse_string(cls, raw: Any) -> Any:
        if not isinstance(raw, str):
            return raw
        prefix, separator, value = raw.partition(":")
        if not separator:
            raise ValueError(
                "Skill references require an explicit builtin:, project:, or path: prefix"
            )
        return {"source": prefix, "value": value}

    @field_validator("value")
    @classmethod
    def _valid_value(cls, value: str, info: Any) -> str:
        if not value or value != value.strip():
            raise ValueError("Skill reference value must be non-empty and have no edge whitespace")
        source = info.data.get("source")
        if source in (SkillSource.BUILTIN, SkillSource.PROJECT):
            if not _NAMED_SKILL_RE.fullmatch(value):
                raise ValueError(
                    "Named skill references may contain only letters, digits, '.', '_', and '-'"
                )
            return value

        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or "\\" in value
            or path.as_posix() != value
            or any(part in ("", ".", "..") for part in path.parts)
        ):
            raise ValueError("path: skill references must be normalized repository-relative paths")
        return path.as_posix()

    @property
    def canonical(self) -> str:
        """Return the stable serialized reference."""

        return f"{self.source.value}:{self.value}"

    def __str__(self) -> str:
        return self.canonical


class BindingComponent(BaseModel, frozen=True):
    """One ordered input to a composite skill-binding digest."""

    position: int = Field(ge=0)
    canonical_ref: str
    content_digest: str

    @field_validator("canonical_ref")
    @classmethod
    def _canonical_ref(cls, value: str) -> str:
        parsed = SkillRef.model_validate(value)
        if parsed.canonical != value:
            raise ValueError("canonical_ref must use the canonical skill-reference spelling")
        return value

    @field_validator("content_digest")
    @classmethod
    def _content_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("content_digest must be sha256:<64 lowercase hex characters>")
        return value


class SkillDescriptor(BaseModel, frozen=True):
    """A discovered skill and its deterministic source metadata."""

    name: str
    canonical_ref: str
    source_root: str
    source_path: str
    description: str = ""
    files: tuple[str, ...]
    content_digest: str

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAMED_SKILL_RE.fullmatch(value):
            raise ValueError("Invalid skill name")
        return value

    @field_validator("canonical_ref")
    @classmethod
    def _ref(cls, value: str) -> str:
        return SkillRef.model_validate(value).canonical

    @field_validator("source_root", "source_path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or "\\" in value
            or path.as_posix() != value
            or any(part in ("", ".", "..") for part in path.parts)
        ):
            raise ValueError("Skill provenance paths must be normalized repository-relative paths")
        return path.as_posix()

    @field_validator("files")
    @classmethod
    def _files(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or "SKILL.md" not in value:
            raise ValueError("A skill descriptor must include SKILL.md")
        if tuple(sorted(set(value))) != value:
            raise ValueError("Skill descriptor files must be unique and sorted")
        for item in value:
            path = PurePosixPath(item)
            if (
                path.is_absolute()
                or "\\" in item
                or path.as_posix() != item
                or any(part in ("", ".", "..") for part in path.parts)
            ):
                raise ValueError("Skill files must be normalized relative paths")
        return value

    @field_validator("content_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("content_digest must be sha256:<64 lowercase hex characters>")
        return value


class ResolvedSkill(BaseModel, frozen=True):
    """An immutable skill snapshot recorded in a session or delegation manifest."""

    canonical_ref: str
    source_path: str
    materialized_path: str
    content_digest: str
    files: tuple[str, ...]

    @field_validator("canonical_ref")
    @classmethod
    def _ref(cls, value: str) -> str:
        return SkillRef.model_validate(value).canonical

    @field_validator("source_path", "materialized_path")
    @classmethod
    def _path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or "\\" in value
            or path.as_posix() != value
            or any(part in ("", ".", "..") for part in path.parts)
        ):
            raise ValueError("Resolved skill paths must be normalized repository-relative paths")
        return path.as_posix()

    @field_validator("content_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("content_digest must be sha256:<64 lowercase hex characters>")
        return value


class SessionSkillBindings(BaseModel, frozen=True):
    """Ordered active references for the two stable interactive-session slots."""

    work: tuple[SkillRef, ...] = ()
    review: tuple[SkillRef, ...] = ()
