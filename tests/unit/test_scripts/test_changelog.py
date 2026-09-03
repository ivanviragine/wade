"""Unit tests for `scripts/changelog.py` breaking-change extraction.

`scripts/` is on pytest's `pythonpath` (see `pyproject.toml`), so the generator
imports as a plain module.
"""

from __future__ import annotations

from changelog import breaking_note


class TestBreakingNote:
    """`BREAKING CHANGE:` footer extraction from a raw commit body."""

    def test_no_footer_returns_empty(self) -> None:
        assert breaking_note("feat: add a thing\n\nJust a body.\n") == ""

    def test_single_line_footer(self) -> None:
        body = "feat!: drop the loader\n\nBREAKING CHANGE: removed API\n"
        assert breaking_note(body) == "removed API"

    def test_hyphenated_footer_token(self) -> None:
        body = "feat!: drop the loader\n\nBREAKING-CHANGE: removed API\n"
        assert breaking_note(body) == "removed API"

    def test_multi_line_footer_is_joined(self) -> None:
        body = (
            "feat!: drop the loader\n\n"
            "BREAKING CHANGE: removed the API.\nUse the new one instead.\n"
        )
        assert breaking_note(body) == "removed the API. Use the new one instead."

    def test_stops_at_blank_line(self) -> None:
        body = "feat!: drop the loader\n\nBREAKING CHANGE: removed API\n\nRationale paragraph.\n"
        assert breaking_note(body) == "removed API"

    def test_stops_at_next_footer_token(self) -> None:
        """A trailer right after the footer, with no blank line, is not absorbed."""
        body = "feat!: drop the loader\n\nBREAKING CHANGE: removed API\nRefs: #123\n"
        assert breaking_note(body) == "removed API"

    def test_stops_at_signed_off_by(self) -> None:
        body = (
            "feat!: drop the loader\n\n"
            "BREAKING CHANGE: removed API\n"
            "Signed-off-by: A Dev <dev@example.com>\n"
        )
        assert breaking_note(body) == "removed API"

    def test_stops_at_hash_shorthand_footer(self) -> None:
        body = "feat!: drop the loader\n\nBREAKING CHANGE: removed API\nCloses #478\n"
        assert breaking_note(body) == "removed API"

    def test_continuation_prose_is_not_mistaken_for_a_footer(self) -> None:
        """Wrapped prose containing a colon mid-line keeps flowing into the note."""
        body = (
            "feat!: retire the pin\n\n"
            "BREAKING CHANGE: `ai.network_access` is retired and the runtime now\n"
            "launches unsandboxed. Set `ai.sandbox: true` to restore it.\n"
            "Closes #478\n"
        )
        assert breaking_note(body) == (
            "`ai.network_access` is retired and the runtime now launches "
            "unsandboxed. Set `ai.sandbox: true` to restore it."
        )
