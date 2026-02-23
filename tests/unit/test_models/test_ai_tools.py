"""Tests for AI tool class hierarchy and self-registration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ghaiw.ai_tools import AbstractAITool
from ghaiw.ai_tools.base import pick_best_model
from ghaiw.ai_tools.model_utils import (
    classify_tier_claude,
    classify_tier_codex,
    classify_tier_gemini,
    classify_tier_universal,
    has_date_suffix,
    probe_copilot_models,
    raw_ids_to_models,
    scrape_models_from_docs,
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


class TestRawIdsToModels:
    """Test converting raw model ID strings to AIModel instances."""

    def test_basic_conversion(self) -> None:
        models = raw_ids_to_models(["claude-haiku-4-5", "claude-opus-4-6"])
        assert len(models) == 2
        assert models[0].id == "claude-haiku-4-5"
        assert models[0].tier == ModelTier.FAST
        assert models[0].is_alias is True
        assert models[1].tier == ModelTier.POWERFUL

    def test_with_date_suffix(self) -> None:
        models = raw_ids_to_models(["claude-haiku-4-5-20251001"])
        assert models[0].is_alias is False

    def test_empty_strings_filtered(self) -> None:
        models = raw_ids_to_models(["", "claude-haiku-4-5", "  "])
        assert len(models) == 1

    def test_explicit_tier_classifier(self) -> None:
        models = raw_ids_to_models(["anything"], classifier=ModelTier.POWERFUL)
        assert models[0].tier == ModelTier.POWERFUL


class TestScrapeModelsFromDocs:
    """Test web scraping model discovery (mocked curl)."""

    @patch("ghaiw.ai_tools.model_utils.shutil.which", return_value=None)
    def test_returns_empty_without_curl(self, mock_which: object) -> None:
        assert scrape_models_from_docs("claude") == []

    @patch("ghaiw.ai_tools.model_utils.shutil.which", return_value="/usr/bin/curl")
    @patch("ghaiw.ai_tools.model_utils.subprocess.run")
    def test_claude_scraping(self, mock_run: object, mock_which: object) -> None:
        import subprocess
        from unittest.mock import MagicMock

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        mock_result.stdout = (
            "<code>claude-haiku-4-5</code> and "
            "<code>claude-sonnet-4-6</code> and "
            "<code>claude-opus-4-6</code> and "
            "<code>claude-haiku-4-5-20251001</code>"
        )
        assert isinstance(mock_run, MagicMock)
        mock_run.return_value = mock_result

        result = scrape_models_from_docs("claude")
        assert len(result) >= 3
        assert any("haiku" in r for r in result)
        assert any("sonnet" in r for r in result)
        assert any("opus" in r for r in result)

    @patch("ghaiw.ai_tools.model_utils.shutil.which", return_value="/usr/bin/curl")
    @patch("ghaiw.ai_tools.model_utils.subprocess.run")
    def test_gemini_scraping(self, mock_run: object, mock_which: object) -> None:
        import subprocess
        from unittest.mock import MagicMock

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        mock_result.stdout = (
            "Use gemini-2.0-flash or gemini-2.5-pro for best results. gemini-3.0-ultra coming soon."
        )
        assert isinstance(mock_run, MagicMock)
        mock_run.return_value = mock_result

        result = scrape_models_from_docs("gemini")
        assert len(result) >= 2

    @patch("ghaiw.ai_tools.model_utils.shutil.which", return_value="/usr/bin/curl")
    @patch("ghaiw.ai_tools.model_utils.subprocess.run")
    def test_codex_scraping(self, mock_run: object, mock_which: object) -> None:
        import subprocess
        from unittest.mock import MagicMock

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 0
        mock_result.stdout = (
            "Run <code>codex -m gpt-5.3-codex</code> or <code>codex -m gpt-4o</code>"
        )
        assert isinstance(mock_run, MagicMock)
        mock_run.return_value = mock_result

        result = scrape_models_from_docs("codex")
        assert len(result) >= 1
        assert any("gpt-" in r for r in result)

    def test_unknown_tool_returns_empty(self) -> None:
        assert scrape_models_from_docs("nonexistent") == []


class TestProbeCopilotModels:
    """Test Copilot --model x validation error probing."""

    @patch("ghaiw.ai_tools.model_utils.shutil.which", return_value=None)
    def test_returns_empty_without_copilot(self, mock_which: object) -> None:
        assert probe_copilot_models() == []

    @patch("ghaiw.ai_tools.model_utils.shutil.which", return_value="/usr/bin/copilot")
    @patch("ghaiw.ai_tools.model_utils.subprocess.run")
    def test_parses_validation_error(self, mock_run: object, mock_which: object) -> None:
        import subprocess
        from unittest.mock import MagicMock

        mock_result = MagicMock(spec=subprocess.CompletedProcess)
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = (
            "Error: Invalid value for --model: 'x'. "
            "Allowed choices are claude-sonnet-4.6, gpt-5.3-codex, "
            "claude-haiku-4.5, o3."
        )
        assert isinstance(mock_run, MagicMock)
        mock_run.return_value = mock_result

        result = probe_copilot_models()
        assert len(result) >= 3
        assert any("claude" in r for r in result)
        assert any("gpt" in r for r in result)
        assert any(r.startswith("o3") for r in result)


class TestPlanModeArgs:
    """Test native plan mode CLI arguments per tool."""

    def test_claude_plan_mode(self) -> None:
        adapter = AbstractAITool.get("claude")
        assert adapter.plan_mode_args() == ["--approval-mode", "plan"]

    def test_gemini_plan_mode(self) -> None:
        adapter = AbstractAITool.get("gemini")
        assert adapter.plan_mode_args() == ["--approval-mode", "plan"]

    def test_copilot_no_plan_mode(self) -> None:
        adapter = AbstractAITool.get("copilot")
        assert adapter.plan_mode_args() == []

    def test_codex_no_plan_mode(self) -> None:
        adapter = AbstractAITool.get("codex")
        assert adapter.plan_mode_args() == []

    def test_plan_mode_in_launch_command(self) -> None:
        adapter = AbstractAITool.get("claude")
        cmd = adapter.build_launch_command(plan_mode=True)
        assert "--approval-mode" in cmd
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
