"""Tests for slug utility."""

from __future__ import annotations

from ghaiw.utils.slug import slugify


class TestSlugify:
    def test_basic(self) -> None:
        assert slugify("Add User Authentication") == "add-user-authentication"

    def test_special_chars(self) -> None:
        assert slugify("Fix: bug #42 in API!") == "fix-bug-42-in-api"

    def test_consecutive_hyphens(self) -> None:
        assert slugify("foo---bar") == "foo-bar"

    def test_leading_trailing_hyphens(self) -> None:
        assert slugify("--hello--world--") == "hello-world"

    def test_max_length(self) -> None:
        result = slugify("a" * 100, max_length=40)
        assert len(result) <= 40

    def test_max_length_no_trailing_hyphen(self) -> None:
        result = slugify("a-b " * 20, max_length=10)
        assert not result.endswith("-")

    def test_multiline_takes_first(self) -> None:
        assert slugify("First Line\nSecond Line") == "first-line"

    def test_empty_string(self) -> None:
        assert slugify("") == ""

    def test_only_special_chars(self) -> None:
        assert slugify("!@#$%") == ""

    def test_unicode(self) -> None:
        # Unicode chars become hyphens, then collapse
        result = slugify("héllo wörld")
        assert "h" in result
        assert "llo" in result
