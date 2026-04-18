"""Tests for the static model registry and adapter get_models() behavior."""

from wade.ai_tools import AbstractAITool
from wade.data import MODELS, get_models_for_tool
from wade.models.ai import AIToolID


class TestModelRegistry:
    def test_get_models_for_tool_returns_list(self) -> None:
        """get_models_for_tool should return lists of strings for known tools."""
        claude_models = get_models_for_tool("claude")
        assert isinstance(claude_models, list)
        assert len(claude_models) > 0
        assert "claude-haiku-4.5" in claude_models

    def test_get_models_for_tool_unknown_returns_empty(self) -> None:
        """get_models_for_tool should return empty list for unknown tool."""
        assert get_models_for_tool("unknown-tool") == []

    def test_registry_contains_no_meta_keys(self) -> None:
        """The loaded MODELS dict should not contain _note or other _ keys."""
        for key in MODELS:
            assert not key.startswith("_")

    def test_claude_opus_47_in_claude_registry(self) -> None:
        assert "claude-opus-4.7" in get_models_for_tool("claude")

    def test_claude_opus_47_xhigh_in_cursor_registry(self) -> None:
        assert "claude-opus-4-7-xhigh" in get_models_for_tool("cursor")

    def test_gemini_31_pro_preview_removed(self) -> None:
        assert "gemini-3.1-pro-preview" not in get_models_for_tool("gemini")

    def test_gpt5_removed_from_codex(self) -> None:
        assert "gpt-5" not in get_models_for_tool("codex")

    def test_gemini_defaults_use_known_models(self) -> None:
        from wade.config.defaults import TOOL_DEFAULTS

        gemini_defaults = TOOL_DEFAULTS[AIToolID.GEMINI]
        known = set(get_models_for_tool("gemini"))
        for model in [
            gemini_defaults.easy,
            gemini_defaults.medium,
            gemini_defaults.complex,
            gemini_defaults.very_complex,
        ]:
            if model:
                assert model in known, f"Gemini default '{model}' not in registry"


class TestRegistryGetModels:
    """Verify that adapters read correctly from the static registry."""

    def test_claude_adapter_reads_registry(self) -> None:
        adapter = AbstractAITool.get(AIToolID.CLAUDE)
        models = adapter.get_models()
        assert len(models) == len(get_models_for_tool("claude"))
        assert "claude-haiku-4.5" in [m.id for m in models]

    def test_copilot_adapter_reads_registry(self) -> None:
        adapter = AbstractAITool.get(AIToolID.COPILOT)
        models = adapter.get_models()
        assert len(models) == len(get_models_for_tool("copilot"))
        assert "gpt-4.1" in [m.id for m in models]

    def test_gemini_adapter_reads_registry(self) -> None:
        adapter = AbstractAITool.get(AIToolID.GEMINI)
        models = adapter.get_models()
        assert len(models) == len(get_models_for_tool("gemini"))
        assert "gemini-2.5-pro" in [m.id for m in models]

    def test_codex_adapter_reads_registry(self) -> None:
        adapter = AbstractAITool.get(AIToolID.CODEX)
        models = adapter.get_models()
        assert len(models) == len(get_models_for_tool("codex"))
        assert any("codex" in m.id for m in models)

    def test_opencode_adapter_reads_registry(self) -> None:
        adapter = AbstractAITool.get(AIToolID.OPENCODE)
        models = adapter.get_models()
        assert len(models) == len(get_models_for_tool("opencode"))
        # Using suffix for classification, we expect the original string in id
        assert "anthropic/claude-sonnet-4.6" in [m.id for m in models]
