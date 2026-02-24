"""Tests for model probing functions."""

from unittest.mock import MagicMock, patch

from ghaiw.ai_tools.model_utils import probe_copilot_models


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

        # Should NOT return bare prefixes
        assert "claude" not in result or "claude-sonnet-4.6" in result
        assert "gpt" not in result or "gpt-4o" in result

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
