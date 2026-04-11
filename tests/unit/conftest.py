"""Shared fixtures for unit tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _narrow_console_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force Rich console to 80-char width to match CI non-TTY behaviour.

    Locally, Rich auto-detects the real terminal width (often 200+ chars),
    which means output that wraps in CI at 80 chars passes locally. Pinning
    to 80 here makes the unit-test environment match CI so wrapping regressions
    are caught before they hit the pipeline.
    """
    from wade.ui.console import console

    monkeypatch.setattr(console.out, "_width", 80)
    monkeypatch.setattr(console.err, "_width", 80)
