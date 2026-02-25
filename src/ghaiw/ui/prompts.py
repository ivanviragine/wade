"""Interactive prompts — confirm, input, select, menu.

TTY-aware: prompts are only displayed when stdin is a TTY.
When stdin is not a TTY, defaults are used silently.
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

_console = Console(stderr=True)


def is_tty() -> bool:
    """Check if stdin is connected to a terminal."""
    return sys.stdin.isatty()


def confirm(message: str, default: bool = False) -> bool:
    """Ask a yes/no confirmation question.

    Returns default when stdin is not a TTY.
    """
    if not is_tty():
        return default
    return Confirm.ask(message, default=default, console=_console)


def input_prompt(label: str, default: str = "", allow_empty: bool = False) -> str:
    """Ask for text input.

    Returns default when stdin is not a TTY.
    When allow_empty is True, pressing Enter without input returns "".
    """
    if not is_tty():
        return default
    if allow_empty and not default:
        # Rich Prompt.ask with default=None requires input — use a sentinel
        result = Prompt.ask(f"{label} (Enter to skip)", default="", console=_console)
        return result
    result = Prompt.ask(label, default=default if default else "", console=_console)
    return result or default


def select(
    title: str,
    items: list[str],
    default: int = 0,
    hints: list[str] | None = None,
) -> int:
    """Numeric picker — display items and let the user choose one.

    Returns the 0-based index of the selected item.
    Returns default when stdin is not a TTY.

    Args:
        title: The prompt title.
        items: List of item labels.
        default: Default 0-based index.
        hints: Optional right-aligned hints per item (e.g. command names).
    """
    if not is_tty():
        return default

    _console.print()
    _console.print(f"  [bold]{title}[/]")
    _console.print()

    table = Table(show_header=False, box=None, padding=(0, 1), expand=False)
    table.add_column("Num", style="bold", no_wrap=True, width=4, justify="right")
    table.add_column("Label")
    if hints:
        table.add_column("Hint", style="dim", no_wrap=True)

    for i, item in enumerate(items):
        num_style = "bold cyan" if i == default else "bold"
        label_style = "bold cyan" if i == default else ""
        num = f"[{num_style}]{i + 1}[/]"
        label = f"[{label_style}]{item}[/]" if label_style else item
        if hints:
            hint = hints[i] if i < len(hints) else ""
            table.add_row(num, label, hint)
        else:
            table.add_row(num, label)

    _console.print(table)
    _console.print()

    while True:
        choice = Prompt.ask(
            f"  Select [1-{len(items)}]",
            default=str(default + 1),
            console=_console,
        )
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return idx
        except ValueError:
            pass
        _console.print("[warning]  Invalid choice, try again.[/]")


def menu(
    title: str,
    items: list[str],
    default: int = 0,
    hints: list[str] | None = None,
    version: str | None = None,
) -> int:
    """Interactive menu with table-based layout.

    Args:
        title: Menu heading.
        items: List of menu item labels.
        default: Default 0-based index.
        hints: Optional command hints per item (shown dim, right-aligned).
        version: Optional version string to display above menu.
    """
    if not is_tty():
        return default

    _console.print()
    if version:
        _console.print(f"  [dim]{version}[/]")
        _console.print()

    _console.print(f"  [bold]{title}[/]")
    _console.print()

    table = Table(show_header=False, box=None, padding=(0, 1), expand=False)
    table.add_column("Num", style="bold", no_wrap=True, width=4, justify="right")
    table.add_column("Label")
    if hints:
        table.add_column("Hint", style="dim", no_wrap=True)

    for i, item in enumerate(items):
        num_style = "bold cyan" if i == default else "bold"
        label_style = "bold cyan" if i == default else ""
        num = f"[{num_style}]{i + 1}[/]"
        label = f"[{label_style}]{item}[/]" if label_style else item
        if hints:
            hint = hints[i] if i < len(hints) else ""
            table.add_row(num, label, hint)
        else:
            table.add_row(num, label)

    _console.print(table)
    _console.print()

    while True:
        choice = Prompt.ask(
            f"  Select [1-{len(items)}]",
            default=str(default + 1),
            console=_console,
        )
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return idx
        except ValueError:
            pass
        _console.print("[warning]  Invalid choice, try again.[/]")


def multi_select(
    title: str,
    items: list[str],
) -> list[int]:
    """Multi-select picker — enter space-separated numbers or 'all'.

    Returns a list of 0-based indices.
    Returns all items when stdin is not a TTY.
    """
    if not is_tty():
        return list(range(len(items)))

    _console.print()
    _console.print(f"  [bold]{title}[/]")
    _console.print()

    for i, item in enumerate(items):
        _console.print(f"    [bold]{i + 1:>3}[/]  {item}")
    _console.print()

    while True:
        raw = Prompt.ask(
            f"  Enter numbers [1-{len(items)}] separated by spaces, or 'all'",
            console=_console,
        )
        if raw.strip().lower() == "all":
            return list(range(len(items)))
        try:
            indices = [int(x) - 1 for x in raw.split()]
            if all(0 <= idx < len(items) for idx in indices) and indices:
                return indices
        except ValueError:
            pass
        _console.print("[warning]  Invalid selection, try again.[/]")
