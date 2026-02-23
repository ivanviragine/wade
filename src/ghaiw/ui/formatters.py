"""Output formatting — human-readable and JSON modes."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table


_console = Console()


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
        """Display tasks as a Rich table."""
        if self.json_mode:
            self._json_output(tasks)
            return

        table = Table(title="Tasks")
        table.add_column("#", style="cyan", no_wrap=True)
        table.add_column("Title", style="default")
        table.add_column("State", style="green")
        table.add_column("Complexity", style="yellow")

        for task in tasks:
            table.add_row(
                str(task.get("id", "")),
                task.get("title", ""),
                task.get("state", ""),
                task.get("complexity", ""),
            )

        _console.print(table)

    def worktree_table(self, worktrees: list[dict[str, Any]]) -> None:
        """Display worktrees as a Rich table."""
        if self.json_mode:
            self._json_output(worktrees)
            return

        table = Table(title="Worktrees")
        table.add_column("Task", style="cyan", no_wrap=True)
        table.add_column("Branch", style="default")
        table.add_column("Path", style="dim")
        table.add_column("State", style="green")

        for wt in worktrees:
            table.add_row(
                str(wt.get("task_id", "")),
                wt.get("branch", ""),
                wt.get("path", ""),
                wt.get("state", ""),
            )

        _console.print(table)

    def event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Output a structured event (for --json mode on sync etc.)."""
        if self.json_mode:
            event_data = {"event": event_type, **(data or {})}
            print(json.dumps(event_data))
