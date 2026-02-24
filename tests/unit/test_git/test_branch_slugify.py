"""Tests for branch name slugification."""

from ghaiw.git.branch import _slugify


def test_slugify_ascii_unchanged() -> None:
    """ASCII text should be lowercased and spaces replaced with hyphens."""
    assert _slugify("hello world") == "hello-world"
    assert _slugify("Hello World") == "hello-world"
    assert _slugify("HELLO WORLD") == "hello-world"


def test_slugify_unicode_becomes_dash() -> None:
    """Non-ASCII characters should be replaced with dashes, not question marks."""
    result = _slugify("café feature")
    # The é should be replaced with -, resulting in "caf-feature"
    assert result == "caf-feature"
    assert "?" not in result
    assert "-" in result


def test_slugify_japanese_becomes_dashes() -> None:
    """Japanese characters should be replaced with dashes."""
    result = _slugify("日本語")
    # All non-ASCII chars should be replaced with -, but consecutive dashes collapse
    # and leading/trailing dashes are stripped, so result should be empty or minimal
    assert "?" not in result
    # Japanese text with no ASCII should result in empty or dash-only string
    # which gets stripped to empty
    assert result == ""


def test_slugify_mixed_unicode_and_ascii() -> None:
    """Mixed Unicode and ASCII should preserve ASCII and replace Unicode with dashes."""
    result = _slugify("café-feature")
    assert result == "caf-feature"
    assert "?" not in result


def test_slugify_consecutive_hyphens_collapsed() -> None:
    """Consecutive hyphens should be collapsed into one."""
    result = _slugify("hello   world")
    assert result == "hello-world"
    assert "--" not in result


def test_slugify_leading_trailing_hyphens_stripped() -> None:
    """Leading and trailing hyphens should be stripped."""
    result = _slugify("-hello-world-")
    assert result == "hello-world"
    assert not result.startswith("-")
    assert not result.endswith("-")


def test_slugify_max_length() -> None:
    """Text longer than max_length should be truncated."""
    long_text = "a" * 100
    result = _slugify(long_text, max_length=50)
    assert len(result) <= 50


def test_slugify_max_length_at_word_boundary() -> None:
    """Truncation should prefer word boundaries."""
    text = "hello-world-feature-implementation"
    result = _slugify(text, max_length=20)
    assert len(result) <= 20
    assert not result.endswith("-")


def test_slugify_special_characters() -> None:
    """Special characters should be replaced with dashes."""
    result = _slugify("hello@world#test")
    assert result == "hello-world-test"
    assert "?" not in result
    assert "@" not in result
    assert "#" not in result
