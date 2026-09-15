"""Tool-neutral contents of one native planning artifact."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BUNDLE_MARKER = "<!-- wade:plan-bundle:v1 -->"


class PlanMember(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    filename: str
    markdown: str = Field(min_length=1, max_length=256_000)
    depends_on: tuple[str, ...] = ()

    @field_validator("filename")
    @classmethod
    def _safe_name(cls, value: str) -> str:
        if not re.fullmatch(r"PLAN(?:-[A-Za-z0-9][A-Za-z0-9_-]{0,100})?\.md", value):
            raise ValueError("plan filenames must be PLAN.md or PLAN-<slug>.md")
        return value


class PlanKnowledgeVote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    direction: Literal["up", "down", "stale"]


class PlanBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plans: tuple[PlanMember, ...] = Field(min_length=1, max_length=50)
    # None means no knowledge handoff was supplied, not "nothing was evaluated".
    knowledge_votes: tuple[PlanKnowledgeVote, ...] | None = None

    @model_validator(mode="after")
    def _unambiguous_members(self) -> Self:
        names = {member.filename for member in self.plans}
        if len({name.casefold() for name in names}) != len(self.plans):
            raise ValueError("plan filenames must be unique, including case")
        pending = {member.filename: set(member.depends_on) for member in self.plans}
        for member in self.plans:
            if len(set(member.depends_on)) != len(member.depends_on):
                raise ValueError("duplicate plan dependency")
            if not set(member.depends_on) <= names:
                raise ValueError("plan dependency names an unknown member")
        completed: set[str] = set()
        while pending:
            ready = {name for name, deps in pending.items() if deps <= completed}
            if not ready:
                raise ValueError("plan dependencies contain a cycle")
            completed.update(ready)
            for name in ready:
                del pending[name]
        votes = self.knowledge_votes or ()
        if len({vote.entry_id for vote in votes}) != len(votes):
            raise ValueError("knowledge entries must have at most one vote")
        return self
