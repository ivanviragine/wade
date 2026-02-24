"""Interactive prompts — confirm, input, select, menu.

TTY-aware: prompts are only displayed when stdin is a TTY.
When stdin is not a TTY, defaults are used silently.
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.prompt import Confirm, Prompt

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
    result = Prompt.ask(label, default=default or None, console=_console)
    return result or default


def select(
    title: str,
    items: list[str],
    default: int = 0,
) -> int:
    """Numeric picker — display items and let the user choose one.

    Returns the 0-based index of the selected item.
    Returns default when stdin is not a TTY.
    """
    if not is_tty():
        return default

    _console.print(f"\n[bold]{title}[/]")
    for i, item in enumerate(items):
        marker = "\u203a" if i == default else " "
        _console.print(f"  {marker} {i + 1}) {item}")

    while True:
        choice = Prompt.ask(
            f"Choose [1-{len(items)}]",
            default=str(default + 1),
            console=_console,
        )
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return idx
        except ValueError:
            pass
        _console.print("[warning]Invalid choice, try again.[/]")


def menu(title: str, items: list[str], default: int = 0) -> int:
    """Interactive menu with header-style title.

    Equivalent to _ui_menu from Bash version.
    """
    prefix = f"\u2500\u2500\u2500 {title} "
    pad = max(0, 62 - len(prefix))
    separator = prefix + "\u2500" * pad
    _console.print(f"\n[bold]{separator}[/]")

    for i, item in enumerate(items):
        marker = "\u203a" if i == default else " "
        _console.print(f"  {marker} {i + 1}) {item}")

    while True:
        choice = Prompt.ask(
            f"Choose [1-{len(items)}]",
            default=str(default + 1),
            console=_console,
        )
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return idx
        except ValueError:
            pass
        _console.print("[warning]Invalid choice, try again.[/]")
