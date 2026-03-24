"""Deterministic E2E contracts for the mock gh harness itself."""

from __future__ import annotations

import json
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
        logged = [
            json.loads(line)
            for line in mock_gh_cli["log_file"].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert ["label", "bogus"] in logged

    def test_invalid_state_payload_fails_with_clear_error(self, mock_gh_cli: MockGhCli) -> None:
        """Non-object state JSON should fail with a clear shape error."""
        mock_gh_cli["state_file"].write_text("[]", encoding="utf-8")

        result = subprocess.run(
            ["gh", "label", "list"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1
        assert str(mock_gh_cli["state_file"]) in result.stderr
        assert "must be a JSON object" in result.stderr
