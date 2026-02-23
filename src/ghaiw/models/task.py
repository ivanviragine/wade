"""Task domain models — Task, PlanFile, Complexity, Label, TaskState."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class Complexity(StrEnum):
    """Task complexity level — maps to AI model tiers."""

    EASY = "easy"
    MEDIUM = "medium"
    COMPLEX = "complex"
    VERY_COMPLEX = "very_complex"


class TaskState(StrEnum):
    """Task lifecycle state."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


class LabelType(StrEnum):
    """Categories of labels managed by ghaiw."""

    ISSUE_LABEL = "issue_label"
    IN_PROGRESS = "in_progress"
    PLANNED_BY = "planned_by"
    PLANNED_MODEL = "planned_model"
    WORKED_BY = "worked_by"
    WORKED_MODEL = "worked_model"
    AI_LABEL = "ai_label"


class Label(BaseModel):
    """A GitHub label with metadata."""

    name: str
    color: str = "ededed"
    description: str = ""
    label_type: LabelType = LabelType.ISSUE_LABEL


class Task(BaseModel):
    """A task from the external provider (GitHub Issue, etc.)."""

    id: str
    title: str = Field(max_length=256)
    body: str = ""
    state: TaskState = TaskState.OPEN
    complexity: Complexity | None = None
    labels: list[Label] = []
    url: str | None = None
    parent_id: str | None = None
    subtask_ids: list[str] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PlanFile(BaseModel):
    """A parsed plan markdown file."""

    path: Path
    title: str = Field(max_length=256)
    complexity: Complexity | None = None
    body: str
    sections: dict[str, str] = {}

    @classmethod
    def from_markdown(cls, path: Path) -> PlanFile:
        """Parse a plan markdown file.

        The first line must be a `# Title` heading.
        Sections are extracted from `## Heading` markers.
        Complexity is parsed from a `## Complexity` section if present.
        """
        content = path.read_text(encoding="utf-8")
        lines = content.split("\n")

        # Extract title from first heading
        title = ""
        body_start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                title = stripped[2:].strip()
                body_start = i + 1
                break

        if not title:
            raise ValueError(f"Plan file {path} must have a '# Title' as the first heading")

        body = "\n".join(lines[body_start:]).strip()

        # Parse sections (## headings)
        sections: dict[str, str] = {}
        current_section = ""
        current_lines: list[str] = []

        for line in lines[body_start:]:
            if line.strip().startswith("## "):
                if current_section:
                    sections[current_section] = "\n".join(current_lines).strip()
                current_section = line.strip()[3:].strip().lower()
                current_lines = []
            elif current_section:
                current_lines.append(line)

        if current_section:
            sections[current_section] = "\n".join(current_lines).strip()

        # Parse complexity from section
        complexity = None
        complexity_text = sections.get("complexity", "").strip().lower()
        if complexity_text:
            # Match the first word that looks like a complexity level
            match = re.match(r"(easy|medium|complex|very_complex)", complexity_text)
            if match:
                complexity = Complexity(match.group(1))

        return cls(
            path=path,
            title=title,
            complexity=complexity,
            body=body,
            sections=sections,
        )
