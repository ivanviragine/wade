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
