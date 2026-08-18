"""Tests for wade.skills.pointer — marker-based vs legacy removal."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wade.skills.pointer import MARKER_END, MARKER_START, remove_pointer

_POINTER = "wade.skills.pointer"


class TestRemovePointerLogEvents:
    def test_marker_based_removal_logs_removed(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text(f"# Notes\n\n{MARKER_START}\n## Git Workflow\n{MARKER_END}\n")

        with patch(f"{_POINTER}.logger") as mock_logger:
            result = remove_pointer(target)

        assert result is True
        events = [call.args[0] for call in mock_logger.info.call_args_list]
        assert "pointer.removed" in events
        assert "pointer.removed_legacy" not in events

    def test_legacy_format_removal_logs_removed_legacy(self, tmp_path: Path) -> None:
        """A pre-marker pointer (bare '## Git Workflow') logs a distinct event.

        Regression test for the PR #454 review finding: sharing removal logic
        between the marker and legacy paths dropped the ``pointer.removed_legacy``
        event, always logging ``pointer.removed`` regardless of which format fired.
        """
        target = tmp_path / "AGENTS.md"
        target.write_text("# Notes\n\n## Git Workflow\nOld pointer content\n")

        with patch(f"{_POINTER}.logger") as mock_logger:
            result = remove_pointer(target)

        assert result is True
        events = [call.args[0] for call in mock_logger.info.call_args_list]
        assert "pointer.removed_legacy" in events
        assert "pointer.removed" not in events
