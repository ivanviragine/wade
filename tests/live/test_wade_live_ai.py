"""Manual live smoke tests for real AI tool invocation.

Wave 1 policy:
  - Canonical tool: claude
  - Canonical model: claude-haiku-4.5
  - Validates API-key-backed Claude headless execution
  - Does not validate Claude Code's interactive `/login` session auth path

Required env:
  - RUN_LIVE_AI_TESTS=1
  - ANTHROPIC_API_KEY
Optional env:
  - WADE_LIVE_AI_TOOL (default: claude)
  - WADE_LIVE_AI_MODEL (default: claude-haiku-4.5)
  - WADE_LIVE_AI_TIMEOUT (default: 45 seconds)
"""

from __future__ import annotations

import os
import shutil

import pytest

from wade.models.delegation import DelegationMode, DelegationRequest
from wade.services.delegation_service import delegate
from wade.services.deps_service import parse_deps_output

AI_TOOL = os.environ.get("WADE_LIVE_AI_TOOL", "claude")
AI_MODEL = os.environ.get("WADE_LIVE_AI_MODEL", "claude-haiku-4.5")


def _parse_live_timeout(raw: str | None) -> int:
    """Parse timeout env safely and fall back to a sane default."""
    if raw is None:
        return 45
    try:
        parsed = int(raw)
    except ValueError:
        return 45
    return parsed if parsed > 0 else 45


AI_TIMEOUT = _parse_live_timeout(os.environ.get("WADE_LIVE_AI_TIMEOUT"))

pytestmark = [
    pytest.mark.live_ai,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_AI_TESTS") != "1",
        reason="Live AI tests disabled (set RUN_LIVE_AI_TESTS=1)",
    ),
]


@pytest.fixture(autouse=True)
def require_live_ai_prereqs() -> None:
    if AI_TOOL != "claude":
        pytest.skip(f"Wave 1 only supports claude live AI smoke (got {AI_TOOL!r})")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY is required for live AI smoke tests")
    if not shutil.which("claude"):
        pytest.skip("claude CLI binary not found in PATH")


class TestLiveAISmoke:
    def test_claude_headless_dependency_prompt(self) -> None:
        """Run a tiny deterministic headless prompt and verify parseable output."""
        prompt = "You are running a test. Return exactly one line:\n# No dependencies found"
        result = delegate(
            DelegationRequest(
                mode=DelegationMode.HEADLESS,
                prompt=prompt,
                ai_tool=AI_TOOL,
                model=AI_MODEL,
                timeout=AI_TIMEOUT,
            )
        )
        assert result.success, f"Live AI headless delegation failed: {result.feedback!r}"
        output = result.feedback
        assert output is not None, "Headless analysis returned no output"
        assert output.strip(), "Headless analysis returned empty output"
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        assert "# No dependencies found" in lines, (
            f"Live AI output is missing the required no-dependencies marker.\nGot lines: {lines!r}"
        )
        assert not any("->" in line for line in lines), (
            "Live AI output contains dependency-edge syntax despite claiming no dependencies.\n"
            f"Got lines: {lines!r}"
        )
        parsed = parse_deps_output(output, valid_numbers={"1", "2"})
        assert parsed == [], f"Expected no parsed dependency edges, got: {parsed!r}"
