"""CLI-level tests for the --ai flag in ghaiw implement-task."""

from __future__ import annotations

from typer.testing import CliRunner

from ghaiw.cli.main import app

runner = CliRunner()


class TestImplementTaskAIFlagCLI:
    """Verify --ai flag is accepted by the implement-task CLI."""

    def test_implement_task_help_documents_ai_flag(self) -> None:
        """implement-task --help should document the --ai flag."""
        import re

        result = runner.invoke(app, ["implement-task", "--help"])
        assert result.exit_code == 0
        output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--ai" in output

    def test_implement_task_ai_flag_accepted(self) -> None:
        """--ai with a value should be accepted by Typer (no option-parse error)."""
        result = runner.invoke(app, ["implement-task", "--ai", "claude", "1"])
        # May fail at service/git level, but must not be rejected as an unknown option
        assert "No such option: --ai" not in result.output
        assert "Error: No such option" not in result.output

    def test_implement_task_multiple_ai_flags_accepted(self) -> None:
        """Multiple --ai flags should be accepted by Typer (no option-parse error)."""
        result = runner.invoke(app, ["implement-task", "--ai", "claude", "--ai", "copilot", "1"])
        assert "No such option: --ai" not in result.output
        assert "Error: No such option" not in result.output
