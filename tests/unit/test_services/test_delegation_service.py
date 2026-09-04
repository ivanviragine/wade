"""Tests for the generic delegation service."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import crossby.ai_tools  # noqa: F401 — triggers adapter auto-registration
import pytest

from wade.models.config import AICommandConfig
from wade.models.delegation import DelegationMode, DelegationRequest
from wade.models.permission import PermissionMode
from wade.services.delegation_service import (
    TIMEOUT_CEILING,
    TIMEOUT_FLOOR,
    TOTAL_TIMEOUT_CAP,
    _delegate_headless,
    _delegate_interactive,
    _delegate_prompt,
    delegate,
    effective_timeout,
    extended_timeout,
    resolve_mode,
    scaled_timeout,
)
from wade.utils.process import CommandError
from wade.utils.runtime_env import CODEX_SANDBOX_ENV

# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


class TestResolveMode:
    def test_defaults_to_prompt(self) -> None:
        cfg = AICommandConfig()
        assert resolve_mode(cfg) == DelegationMode.PROMPT

    def test_reads_mode_from_config(self) -> None:
        cfg = AICommandConfig(mode="headless")
        assert resolve_mode(cfg) == DelegationMode.HEADLESS

    def test_interactive_mode(self) -> None:
        cfg = AICommandConfig(mode="interactive")
        assert resolve_mode(cfg) == DelegationMode.INTERACTIVE

    def test_invalid_mode_defaults_to_prompt(self) -> None:
        cfg = AICommandConfig(mode="bad_value")
        assert resolve_mode(cfg) == DelegationMode.PROMPT

    def test_custom_default_no_config(self) -> None:
        cfg = AICommandConfig()
        assert resolve_mode(cfg, default=DelegationMode.INTERACTIVE) == DelegationMode.INTERACTIVE

    def test_config_mode_overrides_custom_default(self) -> None:
        cfg = AICommandConfig(mode="headless")
        assert resolve_mode(cfg, default=DelegationMode.INTERACTIVE) == DelegationMode.HEADLESS

    def test_invalid_mode_falls_back_to_custom_default(self) -> None:
        cfg = AICommandConfig(mode="bad_value")
        assert resolve_mode(cfg, default=DelegationMode.INTERACTIVE) == DelegationMode.INTERACTIVE


# ---------------------------------------------------------------------------
# Prompt mode
# ---------------------------------------------------------------------------


class TestDelegatePrompt:
    def test_returns_prompt_as_feedback(self) -> None:
        req = DelegationRequest(mode=DelegationMode.PROMPT, prompt="Review this plan.")
        result = delegate(req)
        assert result.success is True
        assert "Review this plan." in result.feedback
        assert result.mode == DelegationMode.PROMPT

    def test_prompt_mode_directly(self) -> None:
        req = DelegationRequest(mode=DelegationMode.PROMPT, prompt="Some prompt text")
        result = _delegate_prompt(req)
        assert result.success is True
        assert "Some prompt text" in result.feedback

    def test_prompt_mode_returns_raw_prompt(self) -> None:
        req = DelegationRequest(mode=DelegationMode.PROMPT, prompt="My prompt")
        result = _delegate_prompt(req)
        assert result.feedback == "My prompt"


# ---------------------------------------------------------------------------
# Headless mode
# ---------------------------------------------------------------------------


class TestDelegateHeadless:
    def test_unknown_tool_returns_failure(self) -> None:
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review code",
            ai_tool="nonexistent_tool",
        )
        result = _delegate_headless(req)
        assert result.success is False
        assert "Unknown AI tool" in result.feedback

    def test_tool_without_headless_returns_failure(self) -> None:
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review code",
            ai_tool="vscode",
        )
        result = _delegate_headless(req)
        assert result.success is False
        assert "does not support headless" in result.feedback

    @patch("wade.services.delegation_service.run")
    def test_successful_headless_run(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="All looks good!\n",
            stderr="A warning that is not feedback\n",
        )
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review the code",
            ai_tool="claude",
            model="claude-haiku-4-5",
        )
        result = _delegate_headless(req)
        assert result.success is True
        assert result.feedback == "All looks good!"
        assert result.mode == DelegationMode.HEADLESS

        # Verify command was built correctly
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "--print" in cmd

    @patch("wade.services.delegation_service.run")
    def test_headless_empty_output_still_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
        )
        result = _delegate_headless(req)
        assert result.success is True
        assert result.feedback == ""
        assert result.exit_code == 0

    @patch("wade.services.delegation_service.run")
    def test_headless_nonzero_exit(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
        )
        result = _delegate_headless(req)
        assert result.success is False
        assert result.exit_code == 1
        assert result.feedback == "Headless session failed with no output"

    @patch("wade.services.delegation_service.run")
    def test_headless_stderr_only_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="fatal: app-server permission denied\n",
        )
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
        )

        result = _delegate_headless(req)

        assert result.success is False
        assert result.feedback == ("Headless session stderr:\nfatal: app-server permission denied")

    @patch("wade.services.delegation_service.run")
    def test_headless_failure_combines_stdout_and_stderr(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="The reviewer started, then failed.\n",
            stderr="fatal: app-server permission denied\n",
        )
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
        )

        result = _delegate_headless(req)

        assert result.feedback == (
            "The reviewer started, then failed.\n\n"
            "Headless session stderr:\n"
            "fatal: app-server permission denied"
        )

    @patch("wade.services.delegation_service.run")
    def test_headless_failure_truncates_stderr_lines(self, mock_run: MagicMock) -> None:
        stderr = "\n".join(f"diagnostic-{index}" for index in range(1, 23))
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr=stderr)
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
        )

        result = _delegate_headless(req)

        assert result.feedback.startswith("Headless session stderr (truncated):\n")
        diagnostic_lines = result.feedback.splitlines()[1:]
        assert "diagnostic-1" not in diagnostic_lines
        assert "diagnostic-2" not in diagnostic_lines
        assert "diagnostic-3" in diagnostic_lines
        assert "diagnostic-22" in diagnostic_lines

    @patch("wade.services.delegation_service.run")
    def test_headless_failure_truncates_stderr_characters(self, mock_run: MagicMock) -> None:
        stderr = "diagnostic-start " + ("x" * 5_000)
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr=stderr)
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
        )

        result = _delegate_headless(req)

        diagnostic = result.feedback.split("\n", 1)[1]
        assert result.feedback.startswith("Headless session stderr (truncated):\n")
        assert len(diagnostic) == 4_000
        assert diagnostic == "x" * 4_000

    @patch("wade.services.delegation_service.console")
    @patch("wade.services.delegation_service.run")
    def test_headless_timeout_both_attempts(
        self, mock_run: MagicMock, mock_console: MagicMock
    ) -> None:
        """Both attempts time out → timed_out=True with decoded partial; run twice (#366)."""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["claude"], timeout=600, output=b"partial review"
        )
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
            timeout=600,
        )
        result = _delegate_headless(req)
        assert result.success is False
        assert result.timed_out is True
        assert result.feedback == "partial review"
        # Retried once with an escalating budget: second == extended_timeout(first).
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0].kwargs["timeout"] == 600
        assert mock_run.call_args_list[1].kwargs["timeout"] == extended_timeout(600)

    @patch("wade.services.delegation_service.console")
    @patch("wade.services.delegation_service.run")
    def test_headless_timeout_then_success(
        self, mock_run: MagicMock, mock_console: MagicMock
    ) -> None:
        """First attempt times out, retry succeeds → success with the retry's output."""
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd=["claude"], timeout=600, output=b"partial"),
            MagicMock(returncode=0, stdout="Full review done\n"),
        ]
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
            timeout=600,
        )
        result = _delegate_headless(req)
        assert result.success is True
        assert result.timed_out is False
        assert result.feedback == "Full review done"
        assert mock_run.call_count == 2

    @patch("wade.services.delegation_service.run")
    def test_headless_crash_is_not_retried(self, mock_run: MagicMock) -> None:
        """A crash (CommandError) returns immediately, never retried, timed_out stays False."""
        mock_run.side_effect = CommandError(["claude"], 127, "Command not found: claude")
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
            timeout=600,
        )
        result = _delegate_headless(req)
        assert result.success is False
        assert result.timed_out is False
        assert mock_run.call_count == 1

    @patch("wade.services.delegation_service.run")
    def test_headless_timeout_over_cap_skips_retry(self, mock_run: MagicMock) -> None:
        """An explicit budget already at/over the cap gets no retry — partial returned once."""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["claude"], timeout=TOTAL_TIMEOUT_CAP, output=b"partial"
        )
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
            timeout=TOTAL_TIMEOUT_CAP,
        )
        result = _delegate_headless(req)
        assert result.timed_out is True
        assert result.feedback == "partial"
        assert mock_run.call_count == 1

    @patch("wade.services.delegation_service.console")
    @patch("wade.services.delegation_service.run")
    def test_headless_explicit_timeout_not_retried(
        self, mock_run: MagicMock, mock_console: MagicMock
    ) -> None:
        """An explicit ai.<cmd>.timeout is honored verbatim — no retry even below the cap."""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["claude"], timeout=900, output=b"partial"
        )
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
            timeout=900,
            explicit_timeout=True,
        )
        result = _delegate_headless(req)
        assert result.timed_out is True
        assert result.feedback == "partial"
        # extended_timeout(900) > 0, but an explicit budget must not retry.
        assert extended_timeout(900) > 0
        assert mock_run.call_count == 1

    @patch("wade.services.delegation_service.console")
    @patch("wade.services.delegation_service.run")
    def test_headless_timeout_empty_partial_placeholder(
        self, mock_run: MagicMock, mock_console: MagicMock
    ) -> None:
        """No partial output → a clear placeholder, still flagged timed_out."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["claude"], timeout=600)
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review",
            ai_tool="claude",
            timeout=600,
        )
        result = _delegate_headless(req)
        assert result.timed_out is True
        assert result.feedback == "<no output before the budget elapsed>"

    @patch("wade.services.delegation_service.run")
    def test_codex_headless_delegation(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="LGTM\n")
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review code",
            ai_tool="codex",
            model="codex-mini-latest",
        )
        result = _delegate_headless(req)
        assert result.success is True
        assert result.feedback == "LGTM"

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "codex"
        assert "exec" in cmd
        assert "Review code" in cmd

    @patch("wade.services.delegation_service.run")
    def test_antigravity_cli_headless_delegation(self, mock_run: MagicMock) -> None:
        """antigravity-cli (`agy`) must run headless via `--print` and capture stdout.

        Regression guard: this is the review/deps delegation path
        (review_plan / review_implementation / review_batch / deps) that
        `antigravity-cli` inherited when it replaced Gemini CLI.
        """
        mock_run.return_value = MagicMock(returncode=0, stdout="1 -> 2 # auth first\n")
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Analyze deps",
            ai_tool="antigravity-cli",
            model="gemini-3-pro",
        )
        result = _delegate_headless(req)
        assert result.success is True
        assert result.feedback == "1 -> 2 # auth first"
        assert result.mode == DelegationMode.HEADLESS

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "agy"
        assert "--print" in cmd
        assert "--model" in cmd
        assert "gemini-3-pro" in cmd
        assert "Analyze deps" in cmd

    def test_antigravity_ide_headless_returns_failure(self) -> None:
        """Antigravity IDE (GUI, launch-only) has no headless surface.

        crossby 0.10.2 reclassifies it as GUI (supports_headless=False); the
        delegation service must reject it with a clear error rather than trying
        to launch the desktop app non-interactively.
        """
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review code",
            ai_tool="antigravity",
        )
        result = _delegate_headless(req)
        assert result.success is False
        assert "antigravity" in result.feedback
        assert "does not support headless" in result.feedback

    @patch("wade.services.delegation_service.run")
    def test_headless_autonomy_is_not_forwarded(self, mock_run: MagicMock) -> None:
        """An autonomy tier is silently ignored for headless delegation."""
        mock_run.return_value = MagicMock(returncode=0, stdout="done\n")
        req = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="Review code",
            ai_tool="claude",
            permission_mode=PermissionMode.YOLO,
        )
        result = _delegate_headless(req)
        assert result.success is True

        cmd = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" not in cmd


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------


class TestDelegateInteractive:
    def test_unknown_tool_returns_failure(self) -> None:
        req = DelegationRequest(
            mode=DelegationMode.INTERACTIVE,
            prompt="Review code",
            ai_tool="nonexistent_tool",
        )
        result = _delegate_interactive(req)
        assert result.success is False
        assert "Unknown AI tool" in result.feedback

    @patch("wade.services.delegation_service.deliver_prompt_if_needed")
    @patch("wade.services.delegation_service.AbstractAITool.get")
    def test_successful_interactive_with_output_file(
        self,
        mock_get: MagicMock,
        mock_deliver: MagicMock,
        tmp_path: Path,
    ) -> None:
        # Set up mock adapter
        mock_adapter = MagicMock()
        mock_adapter.capabilities.return_value = MagicMock(
            blocks_until_exit=True,
            supports_headless=False,
        )
        mock_adapter.launch.return_value = 0
        mock_get.return_value = mock_adapter

        # Write output file as if the AI produced it
        output_file = tmp_path / "output.txt"
        mock_adapter.launch.side_effect = lambda **_kw: output_file.write_text("Review feedback")

        req = DelegationRequest(
            mode=DelegationMode.INTERACTIVE,
            prompt="Review",
            ai_tool="claude",
            output_file=output_file,
        )
        result = _delegate_interactive(req)
        assert result.success is True
        assert result.feedback == "Review feedback"

    @patch("wade.services.delegation_service.deliver_prompt_if_needed")
    @patch("wade.services.delegation_service.AbstractAITool.get")
    def test_interactive_no_output_file(
        self,
        mock_get: MagicMock,
        mock_deliver: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_adapter = MagicMock()
        mock_adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)
        mock_adapter.launch.return_value = 0
        mock_get.return_value = mock_adapter

        output_file = tmp_path / "nonexistent.txt"
        req = DelegationRequest(
            mode=DelegationMode.INTERACTIVE,
            prompt="Review",
            ai_tool="claude",
            output_file=output_file,
        )
        result = _delegate_interactive(req)
        assert result.success is False
        assert "No output file" in result.feedback

    @patch("wade.services.delegation_service.deliver_prompt_if_needed")
    @patch("wade.services.delegation_service.AbstractAITool.get")
    def test_interactive_permission_mode_is_forwarded(
        self,
        mock_get: MagicMock,
        mock_deliver: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_adapter = MagicMock()
        mock_adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)
        mock_adapter.launch.side_effect = lambda **_kw: (tmp_path / "out.txt").write_text("ok")
        mock_get.return_value = mock_adapter

        output_file = tmp_path / "out.txt"
        req = DelegationRequest(
            mode=DelegationMode.INTERACTIVE,
            prompt="Review",
            ai_tool="claude",
            output_file=output_file,
            permission_mode=PermissionMode.YOLO,
        )
        result = _delegate_interactive(req)
        assert result.success is True
        # permission_mode=yolo maps to the crossby autonomy triplet.
        kwargs = mock_adapter.launch.call_args.kwargs
        assert kwargs["yolo"] is True
        assert kwargs["auto"] is False
        assert kwargs["accept_edits"] is False


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class TestDelegateDispatch:
    def test_dispatches_prompt(self) -> None:
        req = DelegationRequest(mode=DelegationMode.PROMPT, prompt="text")
        result = delegate(req)
        assert result.mode == DelegationMode.PROMPT
        assert result.success is True

    def test_dispatches_headless_unknown_tool(self) -> None:
        req = DelegationRequest(mode=DelegationMode.HEADLESS, prompt="text", ai_tool="bad")
        result = delegate(req)
        assert result.mode == DelegationMode.HEADLESS
        assert result.success is False


# ---------------------------------------------------------------------------
# Timeout scaling + retry helpers (#366)
# ---------------------------------------------------------------------------


class TestScaledTimeout:
    def test_floor_for_tiny_payload(self) -> None:
        assert scaled_timeout(0) == TIMEOUT_FLOOR
        assert scaled_timeout(50) == TIMEOUT_FLOOR

    def test_ceiling_for_huge_payload(self) -> None:
        assert scaled_timeout(10_000_000) == TIMEOUT_CEILING

    def test_mid_value_for_large_diff(self) -> None:
        # ~40 KB (~800-line) prompt adds ~300s over the floor.
        assert scaled_timeout(40_000) == 900

    def test_effort_increases_budget(self) -> None:
        """The same payload gets a larger budget at high effort than low (#363 repro)."""
        low = scaled_timeout(40_000, "low")
        high = scaled_timeout(40_000, "high")
        assert high > low
        assert low == 900  # unknown/low effort → multiplier 1.0

    def test_effort_still_bounded_by_ceiling(self) -> None:
        assert scaled_timeout(10_000_000, "high") == TIMEOUT_CEILING


class TestEffectiveTimeout:
    def test_configured_value_bypasses_scaling(self) -> None:
        """An explicit ai.<cmd>.timeout is honored verbatim regardless of size/effort."""
        assert effective_timeout("x" * 100_000, 300, "high") == 300

    def test_scales_from_prompt_when_unset(self) -> None:
        prompt = "x" * 40_000
        assert effective_timeout(prompt, None, None) == scaled_timeout(40_000, None)

    def test_scales_with_effort_when_unset(self) -> None:
        prompt = "x" * 40_000
        assert effective_timeout(prompt, None, "high") > effective_timeout(prompt, None, "low")


class TestExtendedTimeout:
    def test_sum_never_exceeds_total_cap(self) -> None:
        for t in [600, 900, 960, 1200, 1500, 2000]:
            assert t + extended_timeout(t) <= TOTAL_TIMEOUT_CAP

    def test_meaningful_extension_below_cap(self) -> None:
        # 600 * 1.5 = 900, sum 1500 <= TOTAL_TIMEOUT_CAP.
        assert extended_timeout(600) == 900

    def test_zero_when_at_or_over_cap(self) -> None:
        assert extended_timeout(TOTAL_TIMEOUT_CAP) == 0
        assert extended_timeout(TOTAL_TIMEOUT_CAP + 500) == 0

    def test_retry_always_longer_than_first_attempt(self) -> None:
        """#366 review: a scaled first attempt must never get a same-or-shorter retry.

        Every real first attempt is a scaled/floor/ceiling-bounded value in
        [TIMEOUT_FLOOR, TIMEOUT_CEILING] — the cap must accommodate the full
        multiplier across that whole range, not just below its midpoint.
        """
        for t in range(TIMEOUT_FLOOR, TIMEOUT_CEILING + 1, 50):
            retry = extended_timeout(t)
            assert retry > t, f"extended_timeout({t}) == {retry}, not longer than {t}"
            assert retry == round(t * 1.5)


# ---------------------------------------------------------------------------
# Never-launched classification + the shared parent-sandbox check (#480)
# ---------------------------------------------------------------------------


class TestNeverLaunchedClassification:
    """Distinguish "never started" from "started and failed".

    The headless path is covered first because it is the default for reviews —
    this repo configures ``mode: headless`` for ``deps``, ``review_plan`` and
    ``review_implementation``, and the receipt gate excludes ``PROMPT`` outright.
    Fixing only the interactive path would land the change on code the reviews
    never execute.
    """

    def test_missing_tool_never_launched(self) -> None:
        result = _delegate_headless(
            DelegationRequest(mode=DelegationMode.HEADLESS, prompt="p", ai_tool=None)
        )
        assert result.never_launched is True

    def test_unknown_tool_never_launched(self) -> None:
        result = _delegate_headless(
            DelegationRequest(mode=DelegationMode.HEADLESS, prompt="p", ai_tool="nonexistent_tool")
        )
        assert result.never_launched is True

    def test_capability_rejection_never_launched(self) -> None:
        result = _delegate_headless(
            DelegationRequest(mode=DelegationMode.HEADLESS, prompt="p", ai_tool="vscode")
        )
        assert result.never_launched is True

    @patch("wade.services.delegation_service.run")
    def test_spawn_failure_never_launched(self, mock_run: MagicMock) -> None:
        """A ``CommandError`` is the spawn itself failing — nothing ran."""
        mock_run.side_effect = CommandError(["claude"], 126, "permission denied")
        result = _delegate_headless(
            DelegationRequest(mode=DelegationMode.HEADLESS, prompt="p", ai_tool="claude")
        )
        assert result.success is False
        assert result.never_launched is True

    @patch("wade.services.delegation_service.run")
    def test_exec_denial_never_launched(self, mock_run: MagicMock) -> None:
        """A binary that resolves but cannot be executed must not crash the command.

        ``utils.process.run`` maps only ``FileNotFoundError`` to ``CommandError``,
        so a ``PermissionError`` on exec used to escape ``_delegate_headless``
        entirely — aborting `wade review implementation` with a traceback on one
        of the exact shapes an inherited sandbox produces.
        """
        mock_run.side_effect = PermissionError(13, "Permission denied")
        result = _delegate_headless(
            DelegationRequest(mode=DelegationMode.HEADLESS, prompt="p", ai_tool="claude")
        )
        assert result.success is False
        assert result.never_launched is True
        assert result.timed_out is False

    @patch("wade.services.delegation_service.run")
    def test_nonzero_exit_did_launch(self, mock_run: MagicMock) -> None:
        """The distinction that makes the diagnosis trustworthy.

        This reviewer started, ran, and exited non-zero. Telling its user to
        relaunch the outer session would be confidently wrong advice.
        """
        mock_run.return_value = MagicMock(returncode=3, stdout="partial", stderr="boom")
        result = _delegate_headless(
            DelegationRequest(mode=DelegationMode.HEADLESS, prompt="p", ai_tool="claude")
        )
        assert result.success is False
        assert result.never_launched is False

    @patch("wade.services.delegation_service.run")
    def test_timeout_did_launch_and_still_consumes_a_pass(self, mock_run: MagicMock) -> None:
        """A real timeout is a reviewer that ran out of budget, not one that never ran."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["claude"], timeout=1)
        result = _delegate_headless(
            DelegationRequest(
                mode=DelegationMode.HEADLESS,
                prompt="p",
                ai_tool="claude",
                timeout=1,
                explicit_timeout=True,
            )
        )
        assert result.timed_out is True
        assert result.never_launched is False

    def test_interactive_launch_failure_never_launched(self) -> None:
        adapter = MagicMock()
        adapter.launch.side_effect = OSError("Operation not permitted")
        with patch("crossby.ai_tools.AbstractAITool.get", return_value=adapter):
            result = _delegate_interactive(
                DelegationRequest(mode=DelegationMode.INTERACTIVE, prompt="p", ai_tool="claude")
            )
        assert result.never_launched is True

    def test_interactive_prompt_delivery_failure_never_launched(self) -> None:
        """Clipboard fallback runs before the spawn, so its failure started nothing."""
        adapter = MagicMock()
        with (
            patch("crossby.ai_tools.AbstractAITool.get", return_value=adapter),
            patch(
                "wade.services.delegation_service.deliver_prompt_if_needed",
                side_effect=subprocess.CalledProcessError(1, ["pbcopy"]),
            ),
        ):
            result = _delegate_interactive(
                DelegationRequest(mode=DelegationMode.INTERACTIVE, prompt="p", ai_tool="claude")
            )
        assert result.never_launched is True
        adapter.launch.assert_not_called()

    def test_interactive_nonzero_exit_from_a_blocking_adapter_did_launch(self) -> None:
        """A blocking adapter raises only *after* the session ran and failed.

        ``adapter.launch`` blocks until the child exits, so an adapter that
        checks the exit status raises ``CalledProcessError`` from a reviewer that
        very much started. Reporting that as never-launched would record an
        ``UNATTEMPTED`` outcome and offer sandbox-relaunch advice for a session
        the user watched run.
        """
        adapter = MagicMock()
        adapter.launch.side_effect = subprocess.CalledProcessError(3, ["claude"])
        with patch("crossby.ai_tools.AbstractAITool.get", return_value=adapter):
            result = _delegate_interactive(
                DelegationRequest(mode=DelegationMode.INTERACTIVE, prompt="p", ai_tool="claude")
            )
        assert result.success is False
        assert result.never_launched is False
        assert "after launch" in result.feedback

    def test_interactive_launch_timeout_did_launch(self) -> None:
        """``TimeoutExpired`` likewise carries a child that was created."""
        adapter = MagicMock()
        adapter.launch.side_effect = subprocess.TimeoutExpired(cmd=["claude"], timeout=1)
        with patch("crossby.ai_tools.AbstractAITool.get", return_value=adapter):
            result = _delegate_interactive(
                DelegationRequest(mode=DelegationMode.INTERACTIVE, prompt="p", ai_tool="claude")
            )
        assert result.never_launched is False

    def test_interactive_failure_after_launch_did_launch(self, tmp_path: Path) -> None:
        """An output file that cannot be read is not a launch failure.

        The launch and the post-session read share one exception handler, so
        without the explicit launch-boundary split a session that ran to
        completion and then lost its output would be reported as never started.
        """
        adapter = MagicMock()
        adapter.capabilities.return_value = MagicMock(blocks_until_exit=True)
        output_file = tmp_path / "out.txt"
        output_file.write_text("findings")
        with (
            patch("crossby.ai_tools.AbstractAITool.get", return_value=adapter),
            patch.object(Path, "read_text", side_effect=OSError("Permission denied")),
        ):
            result = _delegate_interactive(
                DelegationRequest(
                    mode=DelegationMode.INTERACTIVE,
                    prompt="p",
                    ai_tool="claude",
                    output_file=output_file,
                )
            )
        assert result.success is False
        assert result.never_launched is False


class TestSharedParentSandboxCheck:
    """One check in ``delegate()`` covers every operation that funnels through it."""

    @staticmethod
    def _sandboxed_request(operation: str, relaunch_command: str) -> DelegationRequest:
        # An unknown tool short-circuits before any process starts, so these
        # exercise the pre-launch check without spawning anything.
        return DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="p",
            ai_tool="nonexistent_tool",
            sandbox=False,
            operation=operation,
            relaunch_command=relaunch_command,
        )

    @pytest.mark.parametrize(
        ("operation", "relaunch"),
        [
            ("the implementation review", "wade review implementation"),
            # The two commands with a required positional operand carry it —
            # see review_delegation_service._relaunch_command.
            ("the plan review", "wade review plan docs/plan.md"),
            ("the batch review", "wade review batch 42"),
            ("the dependency analysis", "wade task deps 12 13"),
        ],
    )
    def test_each_operation_gets_its_own_relaunch_command(
        self,
        monkeypatch: pytest.MonkeyPatch,
        operation: str,
        relaunch: str,
    ) -> None:
        """Centralising the check must not flatten the remediation into generic advice."""
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        with patch("wade.ui.console.console") as mock_console:
            result = delegate(self._sandboxed_request(operation, relaunch))

        assert operation in str(mock_console.warn.call_args_list)
        mock_console.detail.assert_any_call(relaunch)
        assert result.inherited_sandbox_profile_mismatch is True

    def test_an_unknown_parent_assessment_stays_silent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(CODEX_SANDBOX_ENV, raising=False)
        with patch("wade.ui.console.console") as mock_console:
            delegate(
                self._sandboxed_request("the implementation review", "wade review implementation")
            )

        assert mock_console.warn.call_count == 0

    def test_a_sandboxed_request_inside_a_sandbox_is_no_mismatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing was promised that the boundary takes away."""
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        request = DelegationRequest(
            mode=DelegationMode.HEADLESS,
            prompt="p",
            ai_tool="nonexistent_tool",
            sandbox=True,
            operation="the implementation review",
            relaunch_command="wade review implementation",
        )
        with patch("wade.ui.console.console") as mock_console:
            result = delegate(request)

        assert mock_console.warn.call_count == 0
        assert result.inherited_sandbox_profile_mismatch is False

    def test_prompt_mode_never_reaches_the_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Prompt mode starts no runtime, so no boundary applies to it."""
        monkeypatch.setenv(CODEX_SANDBOX_ENV, "seatbelt")
        with patch("wade.ui.console.console") as mock_console:
            result = delegate(
                DelegationRequest(mode=DelegationMode.PROMPT, prompt="p", sandbox=False)
            )

        assert result.success is True
        assert mock_console.warn.call_count == 0
