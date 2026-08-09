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


def test_escape_markup_escapes_open_bracket() -> None:
    console = Console()

    # rich.markup.escape backslash-escapes the opening bracket so it is not parsed.
    assert console.escape_markup("[/]") == "\\[/]"


def test_issue_ref_title_with_bracket_renders_without_crash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # issue_ref's output is embedded into markup-rendered strings (kv/panel/…). A
    # provider-derived title with a stray `[/]` would raise MarkupError there
    # unless issue_ref escapes it. The escaped bracket renders back literally.
    console = Console()

    console.kv("Issue", console.issue_ref("42", "fix: handle a/b [/] separator"))

    captured = capsys.readouterr()
    assert "fix: handle a/b [/] separator" in captured.out


def test_dep_tree_bracket_title_renders_without_crash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    console = Console()

    console.dep_tree(
        [("1", "2", "blocks")],
        {"1": "feat: node with [/] token", "2": "fix: child"},
    )

    captured = capsys.readouterr()
    # No MarkupError, and the bracketed title text survives to output.
    assert "feat:" in captured.out
    assert "fix: child" in captured.out
