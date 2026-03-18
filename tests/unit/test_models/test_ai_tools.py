"""Tests for AI tool class hierarchy and self-registration."""

from __future__ import annotations

import pytest

from wade.ai_tools import AbstractAITool
from wade.ai_tools.base import pick_best_model
from wade.ai_tools.model_utils import (
    classify_tier_universal,
    has_date_suffix,
)
from wade.models.ai import AIModel, AIToolID, AIToolType, ModelTier


class TestSelfRegistration:
    def test_all_tools_registered(self) -> None:
        registered = AbstractAITool.available_tools()
        assert AIToolID.CLAUDE in registered
        assert AIToolID.COPILOT in registered
        assert AIToolID.GEMINI in registered
        assert AIToolID.CODEX in registered
        assert AIToolID.ANTIGRAVITY in registered
        assert AIToolID.OPENCODE in registered
        assert AIToolID.CURSOR in registered

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
        assert caps.supports_initial_message is True
        assert caps.blocks_until_exit is True

    def test_copilot_capabilities(self) -> None:
        caps = AbstractAITool.get("copilot").capabilities()
        assert caps.binary == "copilot"
        assert caps.headless_flag == "--prompt"

    def test_gemini_capabilities(self) -> None:
        caps = AbstractAITool.get("gemini").capabilities()
        assert caps.supports_headless is True
        assert caps.headless_flag == "-p"
        assert caps.supports_resume is True

    def test_antigravity_capabilities(self) -> None:
        caps = AbstractAITool.get("antigravity").capabilities()
        assert caps.supports_model_flag is False
        assert caps.supports_initial_message is False
        assert caps.blocks_until_exit is False

    def test_vscode_capabilities(self) -> None:
        caps = AbstractAITool.get("vscode").capabilities()
        assert caps.tool_type == AIToolType.GUI
        assert caps.supports_initial_message is False
        assert caps.blocks_until_exit is False

    def test_codex_capabilities(self) -> None:
        caps = AbstractAITool.get("codex").capabilities()
        assert caps.binary == "codex"
        assert caps.headless_flag == "exec"
        assert caps.supports_headless is True

    def test_opencode_capabilities(self) -> None:
        caps = AbstractAITool.get("opencode").capabilities()
        assert caps.binary == "opencode"
        assert caps.tool_type == AIToolType.TERMINAL
        assert caps.supports_model_flag is True
        assert caps.model_flag == "--model"
        assert caps.headless_flag == "run"
        assert caps.supports_headless is True

    def test_cursor_capabilities(self) -> None:
        caps = AbstractAITool.get("cursor").capabilities()
        assert caps.binary == "agent"
        assert caps.tool_type == AIToolType.TERMINAL
        assert caps.supports_model_flag is True
        assert caps.model_flag == "--model"
        assert caps.headless_flag == "--print"
        assert caps.supports_headless is True
        assert caps.supports_initial_message is True
        assert caps.blocks_until_exit is True


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

    def test_opencode_accepts_all(self) -> None:
        adapter = AbstractAITool.get("opencode")
        # opencode supports 75+ providers — accepts any model ID
        assert adapter.is_model_compatible("anthropic/claude-sonnet-4") is True
        assert adapter.is_model_compatible("openai/gpt-4o") is True
        assert adapter.is_model_compatible("google/gemini-2.0-flash") is True

    def test_cursor_accepts_all(self) -> None:
        adapter = AbstractAITool.get("cursor")
        assert adapter.is_model_compatible("opus-4.6") is True
        assert adapter.is_model_compatible("sonnet-4.6") is True
        assert adapter.is_model_compatible("gpt-5.3-codex") is True
        assert adapter.is_model_compatible("anything") is True


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

    def test_copilot_normalizes_model_in_launch_command(self) -> None:
        """Copilot must receive dotted model format (claude-sonnet-4.6 not claude-sonnet-4-6)."""
        adapter = AbstractAITool.get("copilot")
        cmd = adapter.build_launch_command(model="claude-sonnet-4-6")
        assert cmd == ["copilot", "--model", "claude-sonnet-4.6"]

    def test_opencode_with_provider_slash_model(self) -> None:
        """opencode accepts provider/model format as-is."""
        adapter = AbstractAITool.get("opencode")
        cmd = adapter.build_launch_command(model="anthropic/claude-sonnet-4")
        assert cmd == ["opencode", "--model", "anthropic/claude-sonnet-4"]

    def test_opencode_headless_with_prompt(self) -> None:
        """opencode headless uses run arg."""
        adapter = AbstractAITool.get("opencode")
        cmd = adapter.build_launch_command(model="anthropic/claude-haiku-4-5", prompt="Fix the bug")
        assert cmd == [
            "opencode",
            "--model",
            "anthropic/claude-haiku-4-5",
            "run",
            "Fix the bug",
        ]

    def test_codex_headless_with_prompt(self) -> None:
        """codex headless uses exec subcommand."""
        adapter = AbstractAITool.get("codex")
        cmd = adapter.build_launch_command(model="codex-mini-latest", prompt="Review code")
        assert cmd == [
            "codex",
            "--model",
            "codex-mini-latest",
            "exec",
            "Review code",
        ]

    def test_cursor_basic_launch(self) -> None:
        adapter = AbstractAITool.get("cursor")
        cmd = adapter.build_launch_command(model="opus-4.6")
        assert cmd == ["agent", "--model", "opus-4.6"]

    def test_cursor_with_initial_message(self) -> None:
        adapter = AbstractAITool.get("cursor")
        cmd = adapter.build_launch_command(initial_message="Implement feature X")
        assert cmd == ["agent", "Implement feature X"]

    def test_cursor_plan_mode_launch(self) -> None:
        """Cursor plan mode uses --mode plan."""
        adapter = AbstractAITool.get("cursor")
        cmd = adapter.build_launch_command(
            model="sonnet-4.6", initial_message="Do stuff", plan_mode=True
        )
        assert cmd == [
            "agent",
            "Do stuff",
            "--model",
            "sonnet-4.6",
            "--mode",
            "plan",
        ]

    def test_cursor_model_passthrough(self) -> None:
        """Cursor uses its own model namespace — IDs pass through unchanged."""
        adapter = AbstractAITool.get("cursor")
        cmd = adapter.build_launch_command(model="gpt-5.3-codex")
        assert cmd == ["agent", "--model", "gpt-5.3-codex"]


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


class TestClassifyTierUniversal:
    """Test the universal tier classifier used for scraped/probed models."""

    def test_fast_tier_keywords(self) -> None:
        assert classify_tier_universal("claude-haiku-4-5") == ModelTier.FAST
        assert classify_tier_universal("gemini-2.0-flash") == ModelTier.FAST
        assert classify_tier_universal("codex-mini-latest") == ModelTier.FAST

    def test_powerful_tier_keywords(self) -> None:
        assert classify_tier_universal("claude-opus-4-6") == ModelTier.POWERFUL
        assert classify_tier_universal("gemini-2.5-pro") == ModelTier.POWERFUL
        assert classify_tier_universal("gemini-ultra") == ModelTier.POWERFUL

    def test_balanced_tier_sonnet(self) -> None:
        assert classify_tier_universal("claude-sonnet-4-6") == ModelTier.BALANCED

    def test_unrecognized_defaults_to_balanced(self) -> None:
        assert classify_tier_universal("gpt-4o") == ModelTier.BALANCED
        assert classify_tier_universal("some-unknown-model") == ModelTier.BALANCED


class TestTrustedDirsArgs:
    """Test trusted directory CLI arguments per tool."""

    def test_claude_add_dir(self) -> None:
        adapter = AbstractAITool.get("claude")
        assert adapter.trusted_dirs_args(["/tmp"]) == ["--add-dir", "/tmp"]

    def test_cursor_no_per_dir_trust(self) -> None:
        """Cursor has no per-directory trust flag — returns empty."""
        adapter = AbstractAITool.get("cursor")
        assert adapter.trusted_dirs_args(["/tmp", "/work"]) == []


class TestPlanModeArgs:
    """Test native plan mode CLI arguments per tool."""

    def test_claude_plan_mode(self) -> None:
        adapter = AbstractAITool.get("claude")
        assert adapter.plan_mode_args() == ["--permission-mode", "plan"]

    def test_gemini_plan_mode(self) -> None:
        adapter = AbstractAITool.get("gemini")
        assert adapter.plan_mode_args() == ["--approval-mode", "plan"]

    def test_copilot_no_plan_mode(self) -> None:
        adapter = AbstractAITool.get("copilot")
        assert adapter.plan_mode_args() == []

    def test_codex_no_plan_mode(self) -> None:
        adapter = AbstractAITool.get("codex")
        assert adapter.plan_mode_args() == []

    def test_opencode_no_plan_mode(self) -> None:
        adapter = AbstractAITool.get("opencode")
        assert adapter.plan_mode_args() == []

    def test_cursor_plan_mode(self) -> None:
        adapter = AbstractAITool.get("cursor")
        assert adapter.plan_mode_args() == ["--mode", "plan"]

    def test_plan_mode_in_launch_command(self) -> None:
        adapter = AbstractAITool.get("claude")
        cmd = adapter.build_launch_command(plan_mode=True)
        assert "--permission-mode" in cmd
        assert "plan" in cmd

    def test_no_plan_mode_in_launch_command(self) -> None:
        adapter = AbstractAITool.get("claude")
        cmd = adapter.build_launch_command(plan_mode=False)
        assert "--approval-mode" not in cmd


class TestNormalizeModelFormat:
    """Test model ID format normalization per tool."""

    def test_claude_dotted_to_dashed(self) -> None:
        adapter = AbstractAITool.get("claude")
        assert adapter.normalize_model_format("claude-haiku-4.5") == "claude-haiku-4-5"
        assert adapter.normalize_model_format("claude-sonnet-4.6") == "claude-sonnet-4-6"

    def test_claude_already_dashed(self) -> None:
        adapter = AbstractAITool.get("claude")
        assert adapter.normalize_model_format("claude-haiku-4-5") == "claude-haiku-4-5"

    def test_claude_non_claude_passthrough(self) -> None:
        adapter = AbstractAITool.get("claude")
        assert adapter.normalize_model_format("gpt-4o") == "gpt-4o"

    def test_copilot_dashed_to_dotted(self) -> None:
        adapter = AbstractAITool.get("copilot")
        assert adapter.normalize_model_format("claude-haiku-4-5") == "claude-haiku-4.5"
        assert adapter.normalize_model_format("claude-sonnet-4-6") == "claude-sonnet-4.6"

    def test_copilot_already_dotted(self) -> None:
        adapter = AbstractAITool.get("copilot")
        assert adapter.normalize_model_format("claude-haiku-4.5") == "claude-haiku-4.5"

    def test_copilot_non_claude_passthrough(self) -> None:
        adapter = AbstractAITool.get("copilot")
        assert adapter.normalize_model_format("gpt-4o") == "gpt-4o"

    def test_gemini_passthrough(self) -> None:
        adapter = AbstractAITool.get("gemini")
        assert adapter.normalize_model_format("gemini-2.0-flash") == "gemini-2.0-flash"

    def test_opencode_passthrough(self) -> None:
        """opencode passes provider/model IDs through unchanged."""
        adapter = AbstractAITool.get("opencode")
        assert (
            adapter.normalize_model_format("anthropic/claude-sonnet-4")
            == "anthropic/claude-sonnet-4"
        )
        assert adapter.normalize_model_format("openai/gpt-4o") == "openai/gpt-4o"

    def test_cursor_passthrough(self) -> None:
        """Cursor uses its own model namespace — no normalization needed."""
        adapter = AbstractAITool.get("cursor")
        assert adapter.normalize_model_format("sonnet-4.6") == "sonnet-4.6"
        assert adapter.normalize_model_format("opus-4.6") == "opus-4.6"
        assert adapter.normalize_model_format("gpt-5.3-codex") == "gpt-5.3-codex"
