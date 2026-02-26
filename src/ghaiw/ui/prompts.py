"""Interactive prompts — confirm, input, select, menu.

TTY-aware: prompts are only displayed when stdin is a TTY.
When stdin is not a TTY, defaults are used silently.

Uses questionary for arrow-key navigation menus.
"""

from __future__ import annotations

import sys

import questionary
import typer
from prompt_toolkit.styles import Style
from rich.console import Console

_console = Console(stderr=True)

# Custom prompt_toolkit style matching the color palette
_style = Style(
    [
        ("qmark", "fg:#7c8aff bold"),  # ? marker
        ("question", "bold"),  # question text
        ("answer", "fg:#7c8aff bold"),  # submitted answer
        ("pointer", "fg:#7c8aff bold"),  # pointer character
        ("highlighted", "fg:#7c8aff bold"),  # currently highlighted choice
        ("selected", "fg:#7c8aff bold"),  # selected checkbox item
        ("instruction", "fg:#888888"),  # (Use arrow keys) hint
    ]
)


def is_tty() -> bool:
    """Check if stdin is connected to a terminal."""
    return sys.stdin.isatty()


def _handle_none(result: object) -> None:
    """Raise typer.Exit if questionary returns None (Ctrl+C)."""
    if result is None:
        raise typer.Exit(1)


def confirm(message: str, default: bool = False) -> bool:
    """Ask a yes/no confirmation question.

    Returns default when stdin is not a TTY.
    """
    if not is_tty():
        return default
    choices = ["Yes", "No"]
    default_choice = "Yes" if default else "No"

    result: str | None = questionary.select(
        message,
        choices=choices,
        default=default_choice,
        pointer="\u203a",
        style=_style,
        instruction="",
    ).ask()

    _handle_none(result)
    return result == "Yes"


def input_prompt(label: str, default: str = "", allow_empty: bool = False) -> str:
    """Ask for text input.

    Returns default when stdin is not a TTY.
    When allow_empty is True, pressing Enter without input returns "".
    """
    if not is_tty():
        return default
    instruction = "(Enter to skip)" if allow_empty and not default else None
    result: str | None = questionary.text(
        label,
        default=default,
        instruction=instruction,
        style=_style,
    ).ask()
    _handle_none(result)
    result_str = result or ""
    return result_str or default


def select(
    title: str,
    items: list[str],
    default: int = 0,
    hints: list[str] | None = None,
    allow_back: bool = False,
) -> int:
    """Arrow-key select picker — display items and let the user choose one.

    Returns the 0-based index of the selected item.
    Returns default when stdin is not a TTY.
    Returns -1 if allow_back is True and the user selects "← Back".

    Args:
        title: The prompt title.
        items: List of item labels.
        default: Default 0-based index.
        hints: Optional right-aligned hints per item (e.g. command names).
        allow_back: If True, prepend a "← Back" option; returns -1 if chosen.
    """
    if not is_tty():
        return default

    # Build choice labels — append hints if provided
    choices: list[str] = []
    for i, item in enumerate(items):
        if hints and i < len(hints) and hints[i]:
            choices.append(f"{item}  ({hints[i]})")
        else:
            choices.append(item)

    back_label = "\u2190 Back"
    if allow_back:
        choices = [back_label, *choices]
        adjusted_default = default + 1
        if adjusted_default >= len(choices):
            adjusted_default = 1
    else:
        adjusted_default = default

    default_choice = (
        choices[adjusted_default] if 0 <= adjusted_default < len(choices) else choices[0]
    )
    result: str | None = questionary.select(
        title,
        choices=choices,
        default=default_choice,
        pointer="\u203a",
        style=_style,
        instruction="",
    ).ask()
    _handle_none(result)

    if allow_back and result == back_label:
        return -1

    # Map back to original index (accounting for prepended "← Back" item)
    try:
        idx = choices.index(result)  # type: ignore[arg-type]
        return idx - 1 if allow_back else idx
    except ValueError:
        return default


def menu(
    title: str,
    items: list[str],
    default: int = 0,
    hints: list[str] | None = None,
    version: str | None = None,
) -> int:
    """Interactive menu with arrow-key navigation.

    Args:
        title: Menu heading.
        items: List of menu item labels.
        default: Default 0-based index.
        hints: Optional command hints per item.
        version: Optional version string to display above menu.
    """
    if not is_tty():
        return default

    # Show version header via Rich before the questionary prompt
    if version:
        _console.print()
        _console.print(f"  [dim]{version}[/]")

    return select(title, items, default=default, hints=hints)


def multi_select(
    title: str,
    items: list[str],
) -> list[int]:
    """Checkbox multi-select — arrow keys + Space to toggle, Enter to confirm.

    Returns a list of 0-based indices.
    Returns all items when stdin is not a TTY.
    """
    if not is_tty():
        return list(range(len(items)))

    result: list[str] | None = questionary.checkbox(
        title,
        choices=items,
        pointer="\u203a",
        style=_style,
        instruction="(Space to toggle, Enter to confirm)",
    ).ask()
    _handle_none(result)

    # Map selected labels back to indices
    selected = result or []
    return [items.index(s) for s in selected if s in items]
