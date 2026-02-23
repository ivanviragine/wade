"""Tests for AI tool class hierarchy and self-registration."""

from __future__ import annotations

import pytest

from ghaiw.ai_tools import AbstractAITool
from ghaiw.ai_tools.base import pick_best_model
from ghaiw.ai_tools.model_utils import (
    classify_tier_claude,
    classify_tier_codex,
    classify_tier_gemini,
    has_date_suffix,
)
from ghaiw.models.ai import AIModel, AIToolID, AIToolType, ModelTier


class TestSelfRegistration:
    def test_all_tools_registered(self) -> None:
        registered = AbstractAITool.available_tools()
        assert AIToolID.CLAUDE in registered
        assert AIToolID.COPILOT in registered
        assert AIToolID.GEMINI in registered
        assert AIToolID.CODEX in registered
        assert AIToolID.ANTIGRAVITY in registered

    def test_get_adapter(self) -> None:
        adapter = AbstractAITool.get("claude")
        assert adapter.capabilities().tool_id == AIToolID.CLAUDE
        assert adapter.capabilities().binary == "claude"

    def test_get_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            AbstractAITool.get("nonexistent")


class TestCapabilities:
    def test_claude_capabilities(self) -> None:
        caps = AbstractAITool.get("claude").capabilities()
        assert caps.tool_type == AIToolType.TERMINAL
        assert caps.supports_model_flag is True
        assert caps.model_flag == "--model"
        assert caps.headless_flag == "--print"
        assert caps.supports_headless is True

    def test_copilot_capabilities(self) -> None:
        caps = AbstractAITool.get("copilot").capabilities()
        assert caps.binary == "copilot"
        assert caps.headless_flag == "--prompt"

    def test_gemini_capabilities(self) -> None:
        caps = AbstractAITool.get("gemini").capabilities()
        assert caps.supports_headless is False

    def test_antigravity_capabilities(self) -> None:
        caps = AbstractAITool.get("antigravity").capabilities()
        assert caps.supports_model_flag is False


class TestModelCompatibility:
    def test_claude_accepts_claude_models(self) -> None:
        adapter = AbstractAITool.get("claude")
        assert adapter.is_model_compatible("claude-haiku-4-5") is True
        assert adapter.is_model_compatible("claude-opus-4-6") is True

    def test_claude_rejects_non_claude(self) -> None:
        adapter = AbstractAITool.get("claude")
        assert adapter.is_model_compatible("gpt-4") is False
        assert adapter.is_model_compatible("gemini-pro") is False

    def test_copilot_accepts_all(self) -> None:
        adapter = AbstractAITool.get("copilot")
        assert adapter.is_model_compatible("anything") is True

    def test_gemini_accepts_gemini_models(self) -> None:
        adapter = AbstractAITool.get("gemini")
        assert adapter.is_model_compatible("gemini-2.0-flash") is True
        assert adapter.is_model_compatible("claude-haiku") is False

    def test_codex_accepts_codex_gpt_o(self) -> None:
        adapter = AbstractAITool.get("codex")
        assert adapter.is_model_compatible("codex-mini-latest") is True
        assert adapter.is_model_compatible("gpt-4o") is True
        assert adapter.is_model_compatible("o3") is True
        assert adapter.is_model_compatible("claude-opus") is False


class TestBuildLaunchCommand:
    def test_basic(self) -> None:
        adapter = AbstractAITool.get("claude")
        cmd = adapter.build_launch_command()
        assert cmd == ["claude"]

    def test_with_model(self) -> None:
        adapter = AbstractAITool.get("claude")
        cmd = adapter.build_launch_command(model="claude-opus-4-6")
        assert cmd == ["claude", "--model", "claude-opus-4-6"]

    def test_with_prompt(self) -> None:
        adapter = AbstractAITool.get("claude")
        cmd = adapter.build_launch_command(prompt="Do something")
        assert "--print" in cmd
        assert "Do something" in cmd

    def test_no_model_flag_tool(self) -> None:
        adapter = AbstractAITool.get("antigravity")
        cmd = adapter.build_launch_command(model="some-model")
        # Antigravity doesn't support --model
        assert "--model" not in cmd


class TestTierClassification:
    def test_claude_tiers(self) -> None:
        assert classify_tier_claude("claude-haiku-4-5") == ModelTier.FAST
        assert classify_tier_claude("claude-sonnet-4-6") == ModelTier.BALANCED
        assert classify_tier_claude("claude-opus-4-6") == ModelTier.POWERFUL
        assert classify_tier_claude("unknown-model") is None

    def test_gemini_tiers(self) -> None:
        assert classify_tier_gemini("gemini-2.0-flash") == ModelTier.FAST
        assert classify_tier_gemini("gemini-2.5-pro") == ModelTier.BALANCED
        assert classify_tier_gemini("gemini-ultra") == ModelTier.POWERFUL

    def test_codex_tiers(self) -> None:
        assert classify_tier_codex("codex-mini-latest") == ModelTier.FAST
        assert classify_tier_codex("gpt-4o") == ModelTier.POWERFUL


class TestDateSuffix:
    def test_with_date(self) -> None:
        assert has_date_suffix("claude-haiku-4-5-20251001") is True

    def test_without_date(self) -> None:
        assert has_date_suffix("claude-haiku-4-5") is False
        assert has_date_suffix("gemini-2.0-flash") is False


class TestPickBestModel:
    def test_prefer_alias(self) -> None:
        models = [
            AIModel(id="claude-haiku-4-5-20251001", is_alias=False),
            AIModel(id="claude-haiku-4-5", is_alias=True),
        ]
        best = pick_best_model(models)
        assert best is not None
        assert best.id == "claude-haiku-4-5"

    def test_fallback_to_sorted(self) -> None:
        models = [
            AIModel(id="claude-haiku-4-5-20240101", is_alias=False),
            AIModel(id="claude-haiku-4-5-20251001", is_alias=False),
        ]
        best = pick_best_model(models)
        assert best is not None
        assert best.id == "claude-haiku-4-5-20251001"

    def test_empty_list(self) -> None:
        assert pick_best_model([]) is None
