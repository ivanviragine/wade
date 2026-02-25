"""CLI-level tests for the --ai flag in ghaiwpy work start."""

from __future__ import annotations

from typer.testing import CliRunner

from ghaiw.cli.main import app

runner = CliRunner()


class TestWorkAIFlagCLI:
    """Verify --ai flag is accepted by the work start CLI."""

    def test_work_start_help_documents_ai_flag(self) -> None:
        """work start --help should document the --ai flag."""
        import re

        result = runner.invoke(app, ["work", "start", "--help"])
        assert result.exit_code == 0
        output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--ai" in output

    def test_work_start_ai_flag_accepted(self) -> None:
        """--ai with a value should be accepted by Typer (no option-parse error)."""
        result = runner.invoke(app, ["work", "start", "--ai", "claude", "1"])
        # May fail at service/git level, but must not be rejected as an unknown option
        assert "No such option: --ai" not in result.output
        assert "Error: No such option" not in result.output

    def test_work_start_multiple_ai_flags_accepted(self) -> None:
        """Multiple --ai flags should be accepted by Typer (no option-parse error)."""
        result = runner.invoke(app, ["work", "start", "--ai", "claude", "--ai", "copilot", "1"])
        assert "No such option: --ai" not in result.output
        assert "Error: No such option" not in result.output
