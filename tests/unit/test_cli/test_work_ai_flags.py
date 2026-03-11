"""CLI-level tests for the --ai flag in wade implement."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from wade.cli.main import app

runner = CliRunner()


class TestImplementAIFlagCLI:
    """Verify --ai flag is accepted by the implement CLI."""

    def test_implement_help_documents_ai_flag(self) -> None:
        """implement --help should document the --ai flag."""
        import re

        result = runner.invoke(app, ["implement", "--help"])
        assert result.exit_code == 0
        output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--ai" in output

    @patch("wade.services.implementation_service.start", return_value=True)
    def test_implement_ai_flag_accepted(self, _mock_start: object) -> None:
        """--ai with a value should be accepted by Typer (no option-parse error)."""
        result = runner.invoke(app, ["implement", "--ai", "claude", "1"])
        # May fail at service/git level, but must not be rejected as an unknown option
        assert "No such option: --ai" not in result.output
        assert "Error: No such option" not in result.output

    @patch("wade.services.implementation_service.start", return_value=True)
    def test_implement_multiple_ai_flags_accepted(self, _mock_start: object) -> None:
        """Multiple --ai flags should be accepted by Typer (no option-parse error)."""
        result = runner.invoke(app, ["implement", "--ai", "claude", "--ai", "copilot", "1"])
        assert "No such option: --ai" not in result.output
        assert "Error: No such option" not in result.output
