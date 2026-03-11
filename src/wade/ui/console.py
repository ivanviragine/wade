"""Console class — all terminal message functions.

All output respects Rich markup and goes to the appropriate stream
(stdout for info, stderr for errors/warnings).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from rich.console import Console as RichConsole
from rich.theme import Theme

if TYPE_CHECKING:
    from collections.abc import Generator

# Semantic color palette — ~35 entries
_theme = Theme(
    {
        # Primary
        "success": "#2ecc71 bold",
        "error": "#e74c3c bold",
        "warning": "#f39c12 bold",
        "info": "dim",
        # Progress
        "step": "#7c8aff",
        "step.count": "#7c8aff bold",
        "hint": "dim italic",
        "detail": "default",
        # Structure
        "header.rule": "#6366f1",
        "header.title": "#6366f1 bold",
        "banner.border": "#6366f1",
        "banner.text": "bold",
        # State badges
        "badge.open": "#2ecc71",
        "badge.closed": "#95a5a6",
        "badge.in_progress": "#f39c12",
        "badge.active": "#2ecc71",
        "badge.stale": "#e67e22",
        "badge.stale_empty": "#95a5a6",
        "badge.stale_merged": "#9b59b6",
        "badge.stale_remote_gone": "#e74c3c",
        "badge.draft": "#95a5a6 italic",
        "badge.planned": "#3498db",
        "badge.unplanned": "#7f8c8d",
        # Task
        "task.number": "#3498db bold",
        "task.title": "default",
        "task.complexity.easy": "#2ecc71",
        "task.complexity.medium": "#f39c12",
        "task.complexity.complex": "#e74c3c",
        "task.complexity.very_complex": "#e74c3c bold",
        # Git
        "git.branch": "#e67e22",
        "git.commit": "#95a5a6",
        "git.path": "dim",
        "git.conflict": "#e74c3c bold",
        # AI tools
        "ai.claude": "#d97706",
        "ai.copilot": "#6366f1",
        "ai.gemini": "#3b82f6",
        "ai.codex": "#10b981",
        # Prompts
        "prompt.selected": "#7c8aff bold",
        "prompt.option": "default",
        "prompt.cursor": "#7c8aff bold",
        "prompt.dimmed": "dim",
        # Panels
        "panel.border": "#6366f1",
        "panel.title": "#6366f1 bold",
        # URLs
        "url": "#3498db underline",
    }
)


class Console:
    """Unified CLI output."""

    # Symbol constants (matching Bash _UI_* constants)
    OK = "\u2713"  # ✓
    ERR = "\u2717"  # ✗
    WARN = "!"  # clean bang
    ARROW = "\u2192"  # →
    INFO = "\u00b7"  # · subtle dot
    STEP = "\u25b8"  # ▸ right triangle
    BULLET = "\u00b7"  # ·

    def __init__(self) -> None:
        self.out = RichConsole(theme=_theme, stderr=False)
        self.err = RichConsole(theme=_theme, stderr=True)

    # ------------------------------------------------------------------
    # Original methods (unchanged signatures)
    # ------------------------------------------------------------------

    def success(self, message: str) -> None:
        """Green checkmark line to stdout."""
        self.out.print(f"  [success]{self.OK}[/] {message}")

    def error(self, message: str) -> None:
        """Red error line to stderr."""
        self.err.print(f"  [error]{self.ERR} Error:[/] {message}")

    def warn(self, message: str) -> None:
        """Yellow warning line to stderr."""
        self.err.print(f"  [warning]{self.WARN}[/] {message}")

    def info(self, message: str) -> None:
        """Info line to stdout."""
        self.out.print(f"  [info]{self.INFO}[/] {message}")

    def step(self, message: str) -> None:
        """Step indicator with [●] bullet to stdout."""
        self.out.print(f"  [step]{self.STEP}[/] {message}")

    def step_n(self, n: int, total: int, message: str) -> None:
        """Numbered step: [n/total] text."""
        self.out.print(f"  [step.count]\\[{n}/{total}][/] {message}")

    def hint(self, message: str) -> None:
        """Dim hint text with → arrow."""
        self.out.print(f"[hint]    {self.ARROW} {message}[/]")

    def empty(self) -> None:
        """Print a blank line."""
        self.out.print()

    def detail(self, message: str) -> None:
        """Indented detail line (continuation under info/step)."""
        self.out.print(f"[dim]      {message}[/]")

    def raw(self, text: str) -> None:
        """Print raw text without any formatting or word-wrapping.

        Uses Python's built-in print() instead of Rich's Console.print()
        to avoid Rich inserting line breaks in JSON output.
        """
        print(text)

    def section(self, title: str) -> None:
        """Bold section heading (minor sub-headings)."""
        self.out.print(f"[header.title]{title}[/]")

    def header(self, title: str, width: int = 62) -> None:
        """Bold section heading with ─── Title ──── separator."""
        from rich.rule import Rule

        self.out.print()
        self.out.print(
            Rule(title=title, style="header.rule", characters="\u2500"),
        )
        self.out.print()

    def banner(self, message: str, width: int = 62) -> None:
        """Full-width separator + announcement — now uses a panel."""
        from rich.panel import Panel

        self.out.print()
        self.out.print(
            Panel(
                f"  [banner.text]{message}[/]",
                border_style="banner.border",
                padding=(0, 1),
            )
        )

    def badge(self, label: str, style: str = "info") -> None:
        """Inline [ LABEL ] tag (no newline)."""
        self.out.print(f"[{style}]\\[ {label.upper()} ][/]", end="")

    def plain(self, message: str) -> None:
        """Plain text to stdout."""
        self.out.print(message, highlight=False)

    # ------------------------------------------------------------------
    # New methods — Phase 1b
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def status(self, message: str) -> Generator[None, None, None]:
        """Context-manager spinner for long operations.

        Falls back to a plain info line when not a TTY.
        """
        if not self.out.is_terminal:
            self.info(message)
            yield
            return
        from rich.status import Status

        with Status(f"  {message}", console=self.out, spinner="dots"):
            yield

    def panel(
        self,
        content: str,
        title: str | None = None,
        border_style: str = "panel.border",
    ) -> None:
        """Bordered card for summaries."""
        from rich.panel import Panel

        self.out.print()
        self.out.print(
            Panel(
                content,
                title=f"[panel.title]{title}[/]" if title else None,
                border_style=border_style,
                padding=(1, 2),
            )
        )

    def rule(self, title: str = "", style: str = "header.rule") -> None:
        """Horizontal separator."""
        from rich.rule import Rule

        self.out.print()
        self.out.print(Rule(title=title, style=style, characters="\u2500"))

    def kv(self, key: str, value: str, key_style: str = "dim") -> None:
        """Aligned key-value pair for metadata display."""
        self.out.print(f"    [{key_style}]{key:<14}[/] {value}")

    def error_with_fix(
        self,
        msg: str,
        fix: str,
        command: str | None = None,
    ) -> None:
        """Error + suggested fix + optional runnable command."""
        self.error(msg)
        self.hint(fix)
        if command:
            self.out.print(f"    [prompt.dimmed]$ {command}[/]")

    def summary_table(
        self,
        rows: list[tuple[str, str]],
        title: str | None = None,
    ) -> None:
        """Two-column label:value summary table."""
        from rich.table import Table

        table = Table(
            show_header=False,
            box=None,
            padding=(0, 2),
            title=title,
            title_style="header.title",
        )
        table.add_column("Key", style="dim", no_wrap=True)
        table.add_column("Value")
        for key, value in rows:
            table.add_row(key, value)
        self.out.print(table)

    def dep_tree(
        self,
        edges: list[tuple[str, str, str]],
        titles: dict[str, str],
    ) -> None:
        """Visual dependency graph using Rich Tree.

        Args:
            edges: List of (from_id, to_id, reason) tuples.
            titles: Map of issue id to title.
        """
        from rich.tree import Tree

        tree = Tree("[header.title]Dependency Graph[/]")

        # Group edges by 'from' task
        grouped: dict[str, list[tuple[str, str]]] = {}
        for from_id, to_id, reason in edges:
            grouped.setdefault(from_id, []).append((to_id, reason))

        for from_id, deps in grouped.items():
            from_label = f"[task.number]#{from_id}[/] {titles.get(from_id, '')}"
            branch = tree.add(from_label)
            for to_id, reason in deps:
                to_label = f"[task.number]#{to_id}[/] {titles.get(to_id, '')}"
                if reason:
                    to_label += f"  [dim]({reason})[/]"
                branch.add(to_label)

        self.out.print(tree)

    def markdown(self, text: str) -> None:
        """Render markdown text."""
        from rich.markdown import Markdown

        self.out.print(Markdown(text))

    def badge_str(self, label: str, variant: str = "open") -> str:
        """Return a styled state badge string for inline use.

        Variant maps to badge.* theme entries. The label is wrapped in
        literal square brackets, e.g. "[OPEN]" or "[PLANNED]".
        """
        style = f"badge.{variant}"
        return f"[{style}]\\[{label.upper()}\\][/]"

    def issue_ref(self, number: str, title: str = "") -> str:
        """Return a styled #N Title reference string."""
        ref = f"[task.number]#{number}[/]"
        if title:
            ref += f"  {title}"
        return ref

    def git_ref(self, branch: str) -> str:
        """Return a styled branch name string."""
        return f"[git.branch]{branch}[/]"

    @contextlib.contextmanager
    def progress(
        self,
        total: int,
        description: str = "",
    ) -> Generator[Any, None, None]:
        """Progress bar for multi-step operations.

        Falls back to plain text when not a TTY.

        Usage:
            with console.progress(10, "Processing") as advance:
                for i in range(10):
                    do_work()
                    advance()
        """
        if not self.out.is_terminal:
            self.info(description)

            def _noop() -> None:
                pass

            yield _noop
            return

        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            TextColumn,
            TimeElapsedColumn,
        )

        with Progress(
            TextColumn("  {task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.out,
        ) as progress:
            task = progress.add_task(description, total=total)

            def advance() -> None:
                progress.advance(task)

            yield advance


# Singleton instance for convenience
console = Console()
