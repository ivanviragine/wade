"""Review domain models — ReviewComment, ReviewThread, and formatting helpers.

Used by the ``address-reviews`` flow to represent unresolved PR review
threads and render them as structured markdown for AI consumption.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel


class ReviewComment(BaseModel):
    """A single comment within a PR review thread."""

    author: str = ""
    body: str = ""
    path: str | None = None
    line: int | None = None
    created_at: datetime | None = None
    url: str | None = None


class ReviewThread(BaseModel):
    """A PR review thread — a group of comments on the same code location."""

    is_resolved: bool = False
    is_outdated: bool = False
    comments: list[ReviewComment] = []

    @property
    def first_comment(self) -> ReviewComment | None:
        """The thread-starting comment (convenience accessor)."""
        return self.comments[0] if self.comments else None


# ---------------------------------------------------------------------------
# CodeRabbit AI-agent prompt extraction
# ---------------------------------------------------------------------------

_CODERABBIT_PROMPT_RE = re.compile(
    r"<details>\s*<summary>🤖\s*Prompt for AI Agents</summary>\s*(.*?)\s*</details>",
    re.DOTALL,
)

_CODE_FENCE_RE = re.compile(r"```[\w]*\n(.*?)```", re.DOTALL)


def extract_coderabbit_ai_prompt(body: str) -> str | None:
    """Extract the ``🤖 Prompt for AI Agents`` block from a CodeRabbit comment.

    CodeRabbit wraps its AI-agent-specific instructions in::

        <details>
        <summary>🤖 Prompt for AI Agents</summary>

        ```
        <instruction text>
        ```

        </details>

    Returns the inner text (stripped of the code fence), or ``None`` if not found.
    """
    match = _CODERABBIT_PROMPT_RE.search(body)
    if not match:
        return None

    inner = match.group(1).strip()
    # Strip code fences if present
    fence_match = _CODE_FENCE_RE.search(inner)
    if fence_match:
        return fence_match.group(1).strip()
    return inner


# ---------------------------------------------------------------------------
# Thread filtering
# ---------------------------------------------------------------------------


def filter_actionable_threads(threads: list[ReviewThread]) -> list[ReviewThread]:
    """Return only unresolved, non-outdated threads with at least one comment."""
    return [t for t in threads if not t.is_resolved and not t.is_outdated and t.comments]


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------


def format_review_threads_markdown(threads: list[ReviewThread]) -> str:
    """Format review threads as structured markdown grouped by file.

    For CodeRabbit comments, the extracted ``🤖 Prompt for AI Agents`` content
    is used as the primary instruction, with the full comment body collapsed
    below.  For human comments, the full body is the instruction.
    """
    # Group threads by file path
    by_file: dict[str, list[ReviewThread]] = {}
    no_file: list[ReviewThread] = []

    for thread in threads:
        first = thread.first_comment
        if not first:
            continue
        path = first.path or ""
        if path:
            by_file.setdefault(path, []).append(thread)
        else:
            no_file.append(thread)

    lines: list[str] = ["# Review Comments to Address", ""]

    total = len(threads)
    file_count = len(by_file) + (1 if no_file else 0)
    lines.append(f"**{total}** unresolved comment(s) across **{file_count}** file(s).")
    lines.append("")

    # Render grouped by file
    for path in sorted(by_file.keys()):
        lines.append(f"## `{path}`")
        lines.append("")
        for thread in by_file[path]:
            lines.extend(_format_thread(thread))
        lines.append("")

    # General comments (no file)
    if no_file:
        lines.append("## General Comments")
        lines.append("")
        for thread in no_file:
            lines.extend(_format_thread(thread))
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _format_thread(thread: ReviewThread) -> list[str]:
    """Format a single thread as markdown."""
    first = thread.first_comment
    if not first:
        return []

    lines: list[str] = []

    # Location header
    loc_parts: list[str] = []
    if first.path:
        loc = first.path
        if first.line:
            loc += f":{first.line}"
        loc_parts.append(f"`{loc}`")
    if first.author:
        loc_parts.append(f"by **@{first.author}**")
    if first.url:
        loc_parts.append(f"([link]({first.url}))")

    lines.append(f"### {' '.join(loc_parts)}" if loc_parts else "### Comment")
    lines.append("")

    # CodeRabbit: extract AI-agent prompt as primary instruction
    ai_prompt = extract_coderabbit_ai_prompt(first.body)
    if ai_prompt:
        lines.append("**Instruction (from CodeRabbit):**")
        lines.append("")
        lines.append(ai_prompt)
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>Full CodeRabbit comment</summary>")
        lines.append("")
        lines.append(first.body)
        lines.append("")
        lines.append("</details>")
        lines.append("")
    else:
        # Human comment — full body is the instruction
        lines.append(first.body)
        lines.append("")

    # Follow-up comments in the thread
    if len(thread.comments) > 1:
        lines.append("**Follow-up comments:**")
        lines.append("")
        for comment in thread.comments[1:]:
            author = f"**@{comment.author}**: " if comment.author else ""
            lines.append(f"- {author}{comment.body}")
        lines.append("")

    lines.append("---")
    lines.append("")
    return lines
