"""Tests for model probing functions."""

import subprocess
from unittest.mock import MagicMock, patch

from ghaiw.ai_tools import AbstractAITool
from ghaiw.ai_tools.model_utils import probe_copilot_models
from ghaiw.models.ai import AIToolID, ModelTier


class TestProbeCopilotModels:
    """Tests for probe_copilot_models() function."""

    def test_probe_copilot_models_returns_full_names(self) -> None:
        """probe_copilot_models should return full model names, not just prefixes.

        When the copilot CLI returns validation error output with model names like
        "claude-sonnet-4.6", the function should extract and return the full names,
        not just the prefix "claude".
        """
        # Simulate copilot --model x error output listing available models
        mock_output = (
            "Error: Invalid model 'x'. Allowed choices are "
            "claude-sonnet-4.6, gpt-4o, gemini-1.5-pro, codex-2024, o1-preview"
        )

        with (
            patch("shutil.which", return_value="/usr/bin/copilot"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                stdout="",
                stderr=mock_output,
                returncode=1,
            )
            result = probe_copilot_models()

        # Should return full model names, not just prefixes
        assert "claude-sonnet-4.6" in result
        assert "gpt-4o" in result
        assert "gemini-1.5-pro" in result
        assert "codex-2024" in result
        assert "o1-preview" in result

        # Should NOT return bare prefixes (e.g. "claude" or "gpt" as standalone entries)
        assert "claude" not in result
        assert "gpt" not in result

    def test_probe_copilot_models_cleans_trailing_punctuation(self) -> None:
        """probe_copilot_models should clean trailing punctuation from model names."""
        mock_output = (
            "Error: Invalid model 'x'. Allowed choices are "
            "claude-sonnet-4.6, gpt-4o, gemini-1.5-pro."
        )

        with (
            patch("shutil.which", return_value="/usr/bin/copilot"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                stdout="",
                stderr=mock_output,
                returncode=1,
            )
            result = probe_copilot_models()

        # Should have cleaned the trailing period from gemini-1.5-pro
        assert "gemini-1.5-pro" in result
        assert "gemini-1.5-pro." not in result


class TestOpenCodeGetModels:
    """Tests for OpenCodeAdapter.get_models() using ``opencode models``."""

    def _make_run_result(self, stdout: str, returncode: int = 0) -> MagicMock:
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = ""
        return result

    @patch("ghaiw.ai_tools.opencode.subprocess.run")
    def test_parses_provider_slash_model_format(self, mock_run: MagicMock) -> None:
        """get_models parses 'provider/model' lines from ``opencode models`` output."""
        mock_run.return_value = self._make_run_result(
            "anthropic/claude-haiku-4-5\nanthropicle/claude-sonnet-4\nanthropic/claude-opus-4\n"
        )
        adapter = AbstractAITool.get(AIToolID.OPENCODE)
        models = adapter.get_models()

        ids = [m.id for m in models]
        assert "anthropic/claude-haiku-4-5" in ids
        assert "anthropicle/claude-sonnet-4" in ids
        assert "anthropic/claude-opus-4" in ids

    @patch("ghaiw.ai_tools.opencode.subprocess.run")
    def test_tier_classification_uses_model_part(self, mock_run: MagicMock) -> None:
        """Tier classification is based on the model component after '/', not the provider."""
        mock_run.return_value = self._make_run_result(
            "anthropic/claude-haiku-4-5\nanthropic/claude-sonnet-4\nanthropic/claude-opus-4\n"
        )
        adapter = AbstractAITool.get(AIToolID.OPENCODE)
        models = {m.id: m for m in adapter.get_models()}

        assert models["anthropic/claude-haiku-4-5"].tier == ModelTier.FAST
        assert models["anthropic/claude-sonnet-4"].tier == ModelTier.BALANCED
        assert models["anthropic/claude-opus-4"].tier == ModelTier.POWERFUL

    @patch("ghaiw.ai_tools.opencode.subprocess.run")
    def test_skips_header_lines(self, mock_run: MagicMock) -> None:
        """Lines whose first token is a known header keyword are skipped."""
        mock_run.return_value = self._make_run_result(
            "model  provider\nname   description\nanthropic/claude-haiku-4-5\n"
        )
        adapter = AbstractAITool.get(AIToolID.OPENCODE)
        models = adapter.get_models()

        ids = [m.id for m in models]
        assert "model" not in ids
        assert "name" not in ids
        assert "anthropic/claude-haiku-4-5" in ids

    @patch("ghaiw.ai_tools.opencode.subprocess.run")
    def test_returns_empty_on_nonzero_exit(self, mock_run: MagicMock) -> None:
        """get_models returns [] when ``opencode models`` exits non-zero."""
        mock_run.return_value = self._make_run_result("", returncode=1)
        adapter = AbstractAITool.get(AIToolID.OPENCODE)
        assert adapter.get_models() == []

    @patch("ghaiw.ai_tools.opencode.subprocess.run")
    def test_returns_empty_on_blank_output(self, mock_run: MagicMock) -> None:
        """get_models returns [] when ``opencode models`` produces no output."""
        mock_run.return_value = self._make_run_result("   \n  \n")
        adapter = AbstractAITool.get(AIToolID.OPENCODE)
        assert adapter.get_models() == []

    @patch(
        "ghaiw.ai_tools.opencode.subprocess.run",
        side_effect=FileNotFoundError("opencode not found"),
    )
    def test_returns_empty_when_binary_missing(self, mock_run: MagicMock) -> None:
        """get_models returns [] gracefully when the opencode binary is not installed."""
        adapter = AbstractAITool.get(AIToolID.OPENCODE)
        assert adapter.get_models() == []

    @patch(
        "ghaiw.ai_tools.opencode.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="opencode", timeout=15),
    )
    def test_returns_empty_on_timeout(self, mock_run: MagicMock) -> None:
        """get_models returns [] gracefully when ``opencode models`` times out."""
        adapter = AbstractAITool.get(AIToolID.OPENCODE)
        assert adapter.get_models() == []
