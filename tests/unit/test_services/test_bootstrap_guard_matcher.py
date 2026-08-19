"""Tests for the tool scope the PreToolUse write guard compiles to.

The guard is only as good as its matcher: a write tool missing from
``_GUARD_WRITE_TOOLS`` means no hook fires for that tool at all, so containment
is not enforced — a silent bypass rather than a visible failure. These tests read
the config crossby's writers actually emit, because the canonical-name list and
the per-tool matcher are two different things and only the latter is what a tool
matches against.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wade.services.implementation_service.bootstrap import (
    _GUARD_WRITE_TOOLS,
    _install_guard_hooks,
)


def _agy_matcher(worktree_path: Path) -> str:
    """Return the PreToolUse matcher crossby wrote into agy's hooks.json."""
    config = json.loads((worktree_path / ".agents" / "hooks.json").read_text())
    return config["crossby-pretooluse"]["PreToolUse"][0]["matcher"]


class TestGuardWriteToolScope:
    """The canonical tool list feeding the guard matcher."""

    def test_multiedit_is_scoped(self) -> None:
        """Batched edits are a write channel and must be guarded like ``Edit``."""
        assert "MultiEdit" in _GUARD_WRITE_TOOLS

    @pytest.mark.parametrize("guard_type", ["worktree", "plan"])
    def test_agy_matcher_covers_batched_edits(self, tmp_path: Path, guard_type: str) -> None:
        """agy's ``multi_replace_file_content`` must be a matcher alternative.

        Regression: matchers are matched **whole**, so the ``replace_file_content``
        alternative (from ``Edit``) does not cover ``multi_replace_file_content``
        despite being a substring of it. Asserting membership in the split
        alternatives — not ``in matcher`` — is the point of this test.
        """
        _install_guard_hooks(tmp_path, guard_type=guard_type)

        alternatives = _agy_matcher(tmp_path).split("|")

        assert "multi_replace_file_content" in alternatives
        # The single-edit and shell channels stay covered alongside it.
        assert "replace_file_content" in alternatives
        assert "write_to_file" in alternatives
        assert "run_command" in alternatives

    def test_matcher_alternatives_are_not_duplicated(self, tmp_path: Path) -> None:
        """Adding ``MultiEdit`` must not double up a tool that collapses onto another.

        crossby dedupes when several canonical names map to one native name, so
        this pins that ``Edit``/``MultiEdit``/``Write`` collapsing (as they do on
        Cursor) stays a single alternative rather than repeating one.
        """
        _install_guard_hooks(tmp_path, guard_type="worktree")

        alternatives = _agy_matcher(tmp_path).split("|")

        assert len(alternatives) == len(set(alternatives))
