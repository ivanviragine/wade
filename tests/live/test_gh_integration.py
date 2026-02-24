"""Live GitHub integration tests.

These tests require:
  - gh CLI authenticated
  - RUN_LIVE_GH_TESTS=1 environment variable
  - Network access

They exercise real gh API calls against GitHub.
"""

from __future__ import annotations

import os
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_GH_TESTS") != "1",
    reason="Live gh tests disabled (set RUN_LIVE_GH_TESTS=1)",
)


def _gh_available() -> bool:
    """Check if gh CLI is available and authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


@pytest.fixture(autouse=True)
def require_gh() -> None:
    if not _gh_available():
        pytest.skip("gh CLI not authenticated")


class TestGhProvider:
    def test_gh_auth_status(self) -> None:
        """Verify gh is authenticated."""
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_gh_repo_view(self) -> None:
        """Verify gh can view the current repo."""
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "name"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
