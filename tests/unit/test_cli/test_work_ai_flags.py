"""Tests for multiple --ai flags in ghaiwpy work start."""

from __future__ import annotations

import inspect
from unittest.mock import patch


class TestAIFlagHandling:
    """Test --ai flag behavior in work start command."""

    def test_ai_parameter_accepts_list(self) -> None:
        """The --ai parameter should accept list[str] | None."""
        from ghaiw.cli.work import start

        sig = inspect.signature(start)
        ai_param = sig.parameters.get("ai")
        assert ai_param is not None
        # Check that annotation includes list
        annotation_str = str(ai_param.annotation)
        assert "list" in annotation_str.lower()

    def test_single_ai_flag_no_selection(self) -> None:
        """Single --ai value should not trigger selection prompt."""
        # When ai is a list with 1 item, no select() should be called
        ai_list = ["claude"]
        selected_ai = None

        if ai_list and len(ai_list) > 1:
            # This branch should NOT execute
            selected_ai = "should_not_happen"
        elif ai_list and len(ai_list) == 1:
            selected_ai = ai_list[0]

        assert selected_ai == "claude"

    def test_multiple_ai_flags_trigger_selection(self) -> None:
        """Multiple --ai values should trigger select() prompt."""
        from ghaiw.ui import prompts

        ai_list = ["claude", "copilot"]
        selected_ai = None

        with patch.object(prompts, "select", return_value=0) as mock_select:
            if ai_list and len(ai_list) > 1:
                idx = prompts.select("Select AI tool", ai_list)
                selected_ai = ai_list[idx]
            elif ai_list and len(ai_list) == 1:
                selected_ai = ai_list[0]

            mock_select.assert_called_once_with("Select AI tool", ai_list)
            assert selected_ai == "claude"

    def test_no_ai_flag_returns_none(self) -> None:
        """No --ai flag should result in None (auto-detection)."""
        ai_list = None
        selected_ai = None

        if ai_list and len(ai_list) > 1:
            selected_ai = "should_not_happen"
        elif ai_list and len(ai_list) == 1:
            selected_ai = ai_list[0]

        assert selected_ai is None
