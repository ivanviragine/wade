"""Console class — all terminal message functions.

Rich-based equivalent of lib/ui.sh from the Bash version.
All output respects Rich markup and goes to the appropriate stream
(stdout for info, stderr for errors/warnings).
"""

from __future__ import annotations

import sys

from rich.console import Console as RichConsole
from rich.theme import Theme

# Custom theme matching the Bash color scheme
_theme = Theme(
    {
        "success": "green",
        "error": "red bold",
        "warning": "yellow",
        "info": "default",
        "step": "blue",
        "hint": "dim",
        "header": "bold",
        "detail": "default",
    }
)


class Console:
    """Unified CLI output — mirrors the _ui_* functions from lib/ui.sh."""

    # Symbol constants (matching Bash _UI_* constants)
    OK = "\u2713"       # ✓
    ERR = "\u2717"      # ✗
    WARN = "\u26a0"     # ⚠
    ARROW = "\u2192"    # →
    INFO = "\u2139"     # ℹ
    STEP = "\u25cf"     # ●
    BULLET = "\u00b7"   # ·

    def __init__(self) -> None:
        self.out = RichConsole(theme=_theme, stderr=False)
        self.err = RichConsole(theme=_theme, stderr=True)

    def success(self, message: str) -> None:
        """Green checkmark line to stdout."""
        self.out.print(f"[success]{self.OK}[/] {message}")

    def error(self, message: str) -> None:
        """Red error line to stderr."""
        self.err.print(f"[error]{self.ERR} Error:[/] {message}")

    def warn(self, message: str) -> None:
        """Yellow warning line to stderr."""
        self.err.print(f"[warning]{self.WARN}[/] {message}")

    def info(self, message: str) -> None:
        """Info line to stdout."""
        self.out.print(f"{self.INFO} {message}")

    def step(self, message: str) -> None:
        """Step indicator with [●] bullet to stdout."""
        self.out.print(f"[step]\\[{self.STEP}][/] {message}")

    def step_n(self, n: int, total: int, message: str) -> None:
        """Numbered step: [n/total] text."""
        self.out.print(f"[step]\\[{n}/{total}][/] {message}")

    def hint(self, message: str) -> None:
        """Dim hint text with · bullet."""
        self.out.print(f"[hint]  {self.BULLET} {message}[/]")

    def empty(self) -> None:
        """Print a blank line."""
        self.out.print()

    def detail(self, message: str) -> None:
        """Indented detail line with │ prefix."""
        self.out.print(f"\u2502  {message}")

    def raw(self, text: str) -> None:
        """Print raw text without any formatting."""
        self.out.print(text, highlight=False)

    def section(self, title: str) -> None:
        """Bold section heading (minor sub-headings)."""
        self.out.print(f"[header]{title}[/]")

    def header(self, title: str, width: int = 62) -> None:
        """Bold section heading with ─── Title ──── separator."""
        prefix = f"\u2500\u2500\u2500 {title} "
        pad = max(0, width - len(prefix))
        separator = prefix + "\u2500" * pad
        self.out.print(f"[header]{separator}[/]")

    def banner(self, message: str, width: int = 62) -> None:
        """Full-width separator + announcement."""
        line = "\u2500" * width
        self.out.print(f"[header]{line}[/]")
        self.out.print(f"[header]{message}[/]")
        self.out.print(f"[header]{line}[/]")

    def badge(self, label: str, style: str = "info") -> None:
        """Inline [ LABEL ] tag (no newline)."""
        self.out.print(f"[{style}]\\[ {label.upper()} ][/]", end="")

    def plain(self, message: str) -> None:
        """Plain text to stdout."""
        self.out.print(message, highlight=False)


# Singleton instance for convenience
console = Console()
