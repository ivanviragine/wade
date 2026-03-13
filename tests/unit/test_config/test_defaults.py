"""Tests for config defaults — tier mapping correctness."""

from __future__ import annotations

from pathlib import Path

import yaml

from wade.config.defaults import TOOL_DEFAULTS, get_defaults
from wade.models.ai import AIToolID
from wade.services.init_service import _write_config


class TestMediumTierDefaults:
    """Medium tier must map to balanced-tier (not fast-tier) for all tools."""

    def test_medium_differs_from_easy_for_all_tools(self) -> None:
        for tool_id, mapping in TOOL_DEFAULTS.items():
            assert mapping.medium != mapping.easy, (
                f"{tool_id}: medium ({mapping.medium}) should differ from easy ({mapping.easy})"
            )

    def test_medium_equals_complex_for_all_tools(self) -> None:
        for tool_id, mapping in TOOL_DEFAULTS.items():
            assert mapping.medium == mapping.complex, (
                f"{tool_id}: medium ({mapping.medium}) should equal "
                f"complex ({mapping.complex}) — both balanced-tier"
            )

    def test_claude_medium_is_sonnet(self) -> None:
        mapping = get_defaults(AIToolID.CLAUDE)
        assert mapping.medium == "claude-sonnet-4.6"

    def test_cursor_medium_is_balanced(self) -> None:
        mapping = get_defaults(AIToolID.CURSOR)
        assert mapping.medium == "sonnet-4.6"


class TestWriteLoadRoundtrip:
    """Write config → load → verify models resolve correctly per tier."""

    def test_roundtrip_all_tiers(self, tmp_path: Path) -> None:
        defaults = get_defaults(AIToolID.CLAUDE)
        config_path = tmp_path / ".wade.yml"
        _write_config(config_path, "claude", defaults)

        config = yaml.safe_load(config_path.read_text())
        models = config["models"]["claude"]

        assert models["easy"] == defaults.easy
        assert models["medium"] == defaults.medium
        assert models["complex"] == defaults.complex
        assert models["very_complex"] == defaults.very_complex
        # medium must not equal easy
        assert models["medium"] != models["easy"]
