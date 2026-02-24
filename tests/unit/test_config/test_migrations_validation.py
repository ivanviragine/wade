"""Tests for config migration validation — unknown AI tool detection."""

from __future__ import annotations

import pytest

from ghaiw.config.migrations import _get_ai_tool


class TestGetAIToolValidation:
    """Test validation of AI tool values in _get_ai_tool()."""

    def test_get_ai_tool_valid_value(self) -> None:
        """Valid AI tool value should be returned without error."""
        raw = {"ai": {"default_tool": "claude"}}
        assert _get_ai_tool(raw) == "claude"

    def test_get_ai_tool_valid_copilot(self) -> None:
        """Valid copilot tool should be returned."""
        raw = {"ai": {"default_tool": "copilot"}}
        assert _get_ai_tool(raw) == "copilot"

    def test_get_ai_tool_valid_gemini(self) -> None:
        """Valid gemini tool should be returned."""
        raw = {"ai": {"default_tool": "gemini"}}
        assert _get_ai_tool(raw) == "gemini"

    def test_get_ai_tool_valid_codex(self) -> None:
        """Valid codex tool should be returned."""
        raw = {"ai": {"default_tool": "codex"}}
        assert _get_ai_tool(raw) == "codex"

    def test_get_ai_tool_valid_antigravity(self) -> None:
        """Valid antigravity tool should be returned."""
        raw = {"ai": {"default_tool": "antigravity"}}
        assert _get_ai_tool(raw) == "antigravity"

    def test_get_ai_tool_unknown_value_raises(self) -> None:
        """Unknown AI tool value should raise ValueError."""
        raw = {"ai": {"default_tool": "unknown_tool"}}
        with pytest.raises(ValueError) as exc_info:
            _get_ai_tool(raw)
        assert "Unknown AI tool 'unknown_tool'" in str(exc_info.value)
        assert "Valid values:" in str(exc_info.value)

    def test_get_ai_tool_unknown_value_lists_valid_tools(self) -> None:
        """ValueError should list all valid tool values."""
        raw = {"ai": {"default_tool": "invalid"}}
        with pytest.raises(ValueError) as exc_info:
            _get_ai_tool(raw)
        error_msg = str(exc_info.value)
        assert "claude" in error_msg
        assert "Valid values:" in error_msg

    def test_get_ai_tool_missing_key_returns_default(self) -> None:
        """Missing AI tool key should return None without error."""
        raw: dict[str, object] = {}
        assert _get_ai_tool(raw) is None

    def test_get_ai_tool_empty_ai_dict_returns_none(self) -> None:
        """Empty ai dict should return None without error."""
        raw: dict[str, object] = {"ai": {}}
        assert _get_ai_tool(raw) is None

    def test_get_ai_tool_empty_string_returns_none(self) -> None:
        """Empty string default_tool should return None without error."""
        raw = {"ai": {"default_tool": ""}}
        assert _get_ai_tool(raw) is None

    def test_get_ai_tool_legacy_v1_valid(self) -> None:
        """Valid legacy v1 ai_tool should be returned."""
        raw = {"ai_tool": "claude"}
        assert _get_ai_tool(raw) == "claude"

    def test_get_ai_tool_legacy_v1_unknown_raises(self) -> None:
        """Unknown legacy v1 ai_tool should raise ValueError."""
        raw = {"ai_tool": "unknown_legacy"}
        with pytest.raises(ValueError) as exc_info:
            _get_ai_tool(raw)
        assert "Unknown AI tool 'unknown_legacy'" in str(exc_info.value)

    def test_get_ai_tool_v2_takes_precedence_over_v1(self) -> None:
        """v2 default_tool should take precedence over v1 ai_tool."""
        raw = {"ai": {"default_tool": "gemini"}, "ai_tool": "claude"}
        assert _get_ai_tool(raw) == "gemini"

    def test_get_ai_tool_v2_precedence_validates_v2_only(self) -> None:
        """When v2 is present, only v2 is validated (v1 is ignored)."""
        raw = {"ai": {"default_tool": "claude"}, "ai_tool": "unknown_legacy"}
        # Should not raise because v2 is valid and takes precedence
        assert _get_ai_tool(raw) == "claude"

    def test_get_ai_tool_numeric_value_converted_to_string(self) -> None:
        """Numeric values should be converted to string without validation."""
        raw = {"ai": {"default_tool": 42}}
        # Numeric values are not validated, only strings are
        assert _get_ai_tool(raw) == "42"

    def test_get_ai_tool_case_sensitive(self) -> None:
        """Tool validation should be case-sensitive."""
        raw = {"ai": {"default_tool": "Claude"}}  # Capital C
        with pytest.raises(ValueError) as exc_info:
            _get_ai_tool(raw)
        assert "Unknown AI tool 'Claude'" in str(exc_info.value)

    def test_get_ai_tool_whitespace_not_trimmed(self) -> None:
        """Whitespace should not be trimmed from tool values."""
        raw = {"ai": {"default_tool": " claude"}}
        with pytest.raises(ValueError) as exc_info:
            _get_ai_tool(raw)
        assert "Unknown AI tool ' claude'" in str(exc_info.value)
