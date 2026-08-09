"""Unit tests for console raw output behavior."""

from __future__ import annotations

import pytest

from wade.ui.console import Console


def test_raw_preserves_exact_text_without_adding_newline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    console = Console()

    console.raw('{"ok": true}')

    captured = capsys.readouterr()
    assert captured.out == '{"ok": true}'
    assert captured.err == ""


def test_raw_preserves_existing_trailing_newline(capsys: pytest.CaptureFixture[str]) -> None:
    console = Console()

    console.raw("# Project Knowledge\n")

    captured = capsys.readouterr()
    assert captured.out == "# Project Knowledge\n"
    assert captured.err == ""


def test_success_markup_false_does_not_crash_on_stray_bracket(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A stray `[/]` is Rich markup that "has nothing to close" and raises
    # MarkupError when parsed. With markup=False the message (e.g. a
    # provider-derived title echoed back) is rendered literally instead of
    # crashing after the work succeeded. Mirrors Console.error/detail.
    console = Console()

    console.success("PR title synced to issue title: feat: handle [/] tokens", markup=False)

    captured = capsys.readouterr()
    # Rendered literally — the raw bracket text survives to stdout.
    assert "feat: handle [/] tokens" in captured.out


def test_success_markup_true_still_renders_message(capsys: pytest.CaptureFixture[str]) -> None:
    console = Console()

    console.success("Branch pushed.")

    captured = capsys.readouterr()
    assert "Branch pushed." in captured.out
