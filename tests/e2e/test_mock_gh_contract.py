"""Deterministic E2E contracts for the mock gh harness itself."""

from __future__ import annotations

import subprocess

import pytest

from tests.e2e._support import MockGhCli

pytestmark = [
    pytest.mark.e2e_docker,
    pytest.mark.contract,
]


class TestMockGhHarness:
    """Protect harness behaviors that workflow contracts rely on."""

    def test_unknown_label_action_fails(self, mock_gh_cli: MockGhCli) -> None:
        """Unsupported gh label subcommands should fail loudly."""
        result = subprocess.run(
            ["gh", "label", "bogus"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1
        assert "unsupported gh label action: bogus" in result.stderr
