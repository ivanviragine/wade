"""Output formatting — human-readable and JSON modes."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

_console = Console()

# Badge style mapping for worktree states
_STALENESS_STYLES: dict[str, str] = {
    "active": "badge.active",
    "stale_empty": "badge.stale_empty",
    "stale_merged": "badge.stale_merged",
    "stale_remote_gone": "badge.stale_remote_gone",
    "stale": "badge.stale",
}

# Badge style mapping for task states
_STATE_STYLES: dict[str, str] = {
    "open": "badge.open",
    "closed": "badge.closed",
    "in_progress": "badge.in_progress",
}

# Complexity color mapping
_COMPLEXITY_STYLES: dict[str, str] = {
    "easy": "task.complexity.easy",
    "medium": "task.complexity.medium",
    "complex": "task.complexity.complex",
    "very_complex": "task.complexity.very_complex",
}


def _styled_badge(label: str, style_map: dict[str, str], default: str = "dim") -> Text:
    """Return a styled Text badge for table cells."""
    clean = label.lower().replace(" ", "_")
    style = style_map.get(clean, default)
    return Text(label.upper().replace("_", " "), style=style)


class OutputFormatter:
    """Formats command output in human-readable or JSON mode."""

    def __init__(self, json_mode: bool = False) -> None:
        self.json_mode = json_mode

    def output(self, data: Any) -> None:
        """Output data in the configured format."""
        if self.json_mode:
            self._json_output(data)
        else:
            self._human_output(data)

    def _json_output(self, data: Any) -> None:
        """Output as JSON to stdout."""
        if hasattr(data, "model_dump"):
            print(json.dumps(data.model_dump(mode="json"), indent=2))
        elif isinstance(data, list) and data and hasattr(data[0], "model_dump"):
            items = [item.model_dump(mode="json") for item in data]
            print(json.dumps(items, indent=2))
        else:
            print(json.dumps(data, indent=2, default=str))

    def _human_output(self, data: Any) -> None:
        """Output as human-readable text."""
        if isinstance(data, list):
            for item in data:
                _console.print(str(item))
        else:
            _console.print(str(data))

    def task_table(self, tasks: list[dict[str, Any]]) -> None:
        """Display tasks as a Rich table with styled badges."""
        if self.json_mode:
            self._json_output(tasks)
            return

        table = Table(
            show_header=True,
            header_style="bold",
            box=None,
            padding=(0, 2),
            show_edge=False,
        )
        table.add_column("#", style="task.number", no_wrap=True, width=6, justify="right")
        table.add_column("Title", style="task.title", ratio=3)
        table.add_column("State", no_wrap=True, width=12)
        table.add_column("Complexity", no_wrap=True, width=14)

        for task in tasks:
            number = str(task.get("id", ""))
            title = task.get("title", "")
            state = task.get("state", "")
            complexity = task.get("complexity", "")

            state_badge = _styled_badge(state, _STATE_STYLES)
            complexity_text = (
                _styled_badge(complexity, _COMPLEXITY_STYLES) if complexity else Text("")
            )

            table.add_row(number, title, state_badge, complexity_text)

        _console.print()
        _console.print(table)

    def worktree_table(self, worktrees: list[dict[str, Any]]) -> None:
        """Display worktrees as a Rich table with styled badges."""
        if self.json_mode:
            self._json_output(worktrees)
            return

        table = Table(
            show_header=True,
            header_style="bold",
            box=None,
            padding=(0, 2),
            show_edge=False,
        )
        table.add_column("#", style="task.number", no_wrap=True, width=6, justify="right")
        table.add_column("Issue", ratio=2)
        table.add_column("Branch", style="git.branch", ratio=2)
        table.add_column("Commits", no_wrap=True, width=10, justify="right")
        table.add_column("Status", no_wrap=True, width=14)

        for wt in worktrees:
            task_id = str(wt.get("task_id", wt.get("issue", "")))
            issue_title = wt.get("issue_title", "")
            issue_str = f"{issue_title}" if issue_title else ""
            branch = wt.get("branch", "")
            ahead = wt.get("commits_ahead", 0)
            staleness = wt.get("state", wt.get("staleness", "active"))

            commits_str = f"{ahead} ahead" if ahead else "0"
            staleness_badge = _styled_badge(staleness, _STALENESS_STYLES)

            table.add_row(task_id, issue_str, branch, commits_str, staleness_badge)

        _console.print()
        _console.print(table)

    def event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Output a structured event (for --json mode on sync etc.)."""
        if self.json_mode:
            event_data = {"event": event_type, **(data or {})}
            print(json.dumps(event_data))
