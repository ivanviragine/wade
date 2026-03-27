"""Tests for delegation domain models."""

from __future__ import annotations

from pathlib import Path

from wade.models.delegation import DelegationMode, DelegationRequest, DelegationResult


class TestDelegationMode:
    def test_enum_values(self) -> None:
        assert DelegationMode.PROMPT == "prompt"
        assert DelegationMode.INTERACTIVE == "interactive"
        assert DelegationMode.HEADLESS == "headless"

    def test_from_string(self) -> None:
        assert DelegationMode("prompt") == DelegationMode.PROMPT
        assert DelegationMode("headless") == DelegationMode.HEADLESS
        assert DelegationMode("interactive") == DelegationMode.INTERACTIVE


class TestDelegationRequest:
    def test_minimal_request(self) -> None:
        req = DelegationRequest(mode=DelegationMode.PROMPT, prompt="Review this")
        assert req.mode == DelegationMode.PROMPT
        assert req.prompt == "Review this"
        assert req.ai_tool is None
        assert req.model is None
        assert req.timeout == 120
        assert req.trusted_dirs == []
        assert req.allowed_commands == []
        assert req.yolo is False

    def test_full_request(self) -> None:
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Analyze code",
            ai_tool="claude",
            model="claude-sonnet-4-6",
            effort="high",
            cwd=Path("/tmp/test"),
            timeout=300,
            output_file=Path("/tmp/out.txt"),
            trusted_dirs=["/tmp"],
            allowed_commands=["wade *"],
            yolo=True,
        )
        assert req.ai_tool == "claude"
        assert req.model == "claude-sonnet-4-6"
        assert req.effort == "high"
        assert req.cwd == Path("/tmp/test")
        assert req.timeout == 300
        assert req.output_file == Path("/tmp/out.txt")
        assert req.trusted_dirs == ["/tmp"]
        assert req.allowed_commands == ["wade *"]
        assert req.yolo is True


class TestDelegationResult:
    def test_success_result(self) -> None:
        result = DelegationResult(
            success=True,
            feedback="Looks good!",
            mode=DelegationMode.PROMPT,
        )
        assert result.success is True
        assert result.feedback == "Looks good!"
        assert result.exit_code == 0
        assert result.skipped is False

    def test_failure_result(self) -> None:
        result = DelegationResult(
            success=False,
            feedback="Tool failed",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
        )
        assert result.success is False
        assert result.exit_code == 1
        assert result.skipped is False

    def test_skipped_result(self) -> None:
        result = DelegationResult(
            success=True,
            feedback="Review skipped",
            mode=DelegationMode.PROMPT,
            skipped=True,
        )
        assert result.success is True
        assert result.skipped is True
