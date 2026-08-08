"""Tests for the #351 SessionStart context-injection branch of ``wade-hook``.

Strictly non-blocking: on every SessionStart source (startup/resume/compact/…)
the hook re-injects a compact, phase-gated task reminder as ``additionalContext``
and NEVER blocks a session from starting (always exit 0). The per-dialect subset
is driven through the lean ``wade.hooks.cli`` entry point as a real subprocess
(mirroring ``test_hook_cli.py`` / ``test_post_tool_use.py``); the builder itself
is exercised directly.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from wade.hooks.policies import _SESSION_CONTEXT_MAX_CHARS, session_start_context
from wade.models.hooks import SessionPhase

_ISSUE_TITLE = "E3: Session-start & resume context injection"
_PLAN_FIRST_LINE = f"# Issue #351: {_ISSUE_TITLE}"

# Repo root → the source-of-truth skill templates that get installed per session.
_TEMPLATES = Path(__file__).resolve().parents[3] / "templates" / "skills"
_PHASE_SKILL = {
    SessionPhase.IMPLEMENT: "implementation-session",
    SessionPhase.REVIEW: "review-pr-comments-session",
    SessionPhase.PLAN: "plan-session",
}


def _run_ss(
    tool: str,
    root: str | None,
    phase: str | None,
    stdin: str = "{}",
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "wade.hooks.cli"]
    cmd += ["session_start", "--guard", "context", "--tool", tool]
    if root is not None:
        cmd += ["--root", root]
    if phase is not None:
        cmd += ["--phase", phase]
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


def _run_ss_alias(
    tool: str,
    root: str | None,
    phase: str | None,
    stdin: str = "{}",
) -> subprocess.CompletedProcess[str]:
    """Drive the ``wade hook`` Typer alias (parity coverage)."""
    cmd = [sys.executable, "-m", "wade", "hook"]
    cmd += ["session_start", "--guard", "context", "--tool", tool]
    if root is not None:
        cmd += ["--root", root]
    if phase is not None:
        cmd += ["--phase", phase]
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


def _write_plan(root: Path, first_line: str = _PLAN_FIRST_LINE) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "PLAN.md").write_text(f"{first_line}\n\nbody\n", encoding="utf-8")
    return root


class TestPerDialect:
    """The four session-start dialects crossby's ``emit_decision`` serializes."""

    def test_claude_nested_hook_specific_output(self, tmp_path: Path) -> None:
        _write_plan(tmp_path)
        r = _run_ss("claude", str(tmp_path), "implement")
        assert r.returncode == 0
        payload = json.loads(r.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "#351" in payload["hookSpecificOutput"]["additionalContext"]

    def test_codex_nested_hook_specific_output(self, tmp_path: Path) -> None:
        _write_plan(tmp_path)
        r = _run_ss("codex", str(tmp_path), "implement")
        assert r.returncode == 0
        assert "additionalContext" in json.loads(r.stdout)["hookSpecificOutput"]

    def test_copilot_flat_additional_context(self, tmp_path: Path) -> None:
        _write_plan(tmp_path)
        r = _run_ss("copilot", str(tmp_path), "implement")
        assert r.returncode == 0
        payload = json.loads(r.stdout)
        assert "#351" in payload["additionalContext"]
        assert "hookSpecificOutput" not in payload

    def test_cursor_snake_case_additional_context(self, tmp_path: Path) -> None:
        _write_plan(tmp_path)
        r = _run_ss("cursor", str(tmp_path), "review")
        assert r.returncode == 0
        assert "#351" in json.loads(r.stdout)["additional_context"]

    def test_antigravity_cli_noops_empty_object(self, tmp_path: Path) -> None:
        # agy's DECISION dialect has no verified context channel — the runtime
        # double-guards (bootstrap already skips it) and emits a no-op proceed.
        _write_plan(tmp_path)
        r = _run_ss("antigravity-cli", str(tmp_path), "implement")
        assert r.returncode == 0
        assert json.loads(r.stdout) == {}


class TestPhaseContent:
    def _ctx(self, r: subprocess.CompletedProcess[str]) -> str:
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_implement_carries_issue_and_done_command(self, tmp_path: Path) -> None:
        _write_plan(tmp_path)
        ctx = self._ctx(_run_ss("claude", str(tmp_path), "implement"))
        assert "Issue #351" in ctx
        assert _ISSUE_TITLE in ctx
        assert "wade implementation-session done" in ctx

    def test_review_points_at_review_done_command(self, tmp_path: Path) -> None:
        _write_plan(tmp_path)
        ctx = self._ctx(_run_ss("claude", str(tmp_path), "review"))
        assert "wade review-pr-comments-session done" in ctx

    def test_plan_has_no_issue_line_and_points_at_plan_done(self, tmp_path: Path) -> None:
        # Plan is a detached worktree — no PLAN.md at the root, so no issue line.
        ctx = self._ctx(_run_ss("claude", str(tmp_path), "plan"))
        assert "Issue #" not in ctx
        assert "wade plan-session done" in ctx
        assert "## Complexity" in ctx


class TestFailOpen:
    """A SessionStart hook must never block a session from starting (exit 0)."""

    def test_missing_plan_md_omits_issue_line_but_still_emits(self, tmp_path: Path) -> None:
        # No PLAN.md: the issue line is omitted (do not fail) but the phase
        # reminder is still injected.
        r = _run_ss("claude", str(tmp_path), "implement")
        assert r.returncode == 0
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "Issue #" not in ctx
        assert "wade implementation-session done" in ctx

    def test_unparseable_plan_first_line_omits_issue_line(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, first_line="# Not an issue heading")
        r = _run_ss("claude", str(tmp_path), "implement")
        assert r.returncode == 0
        assert "Issue #" not in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_missing_root_is_noop_exit_zero(self) -> None:
        r = _run_ss("claude", None, "implement")
        assert r.returncode == 0
        assert r.stdout == ""  # allow no-op → empty stdout for Claude

    def test_missing_phase_is_noop_exit_zero(self, tmp_path: Path) -> None:
        _write_plan(tmp_path)
        r = _run_ss("claude", str(tmp_path), None)
        assert r.returncode == 0
        assert r.stdout == ""

    def test_unknown_phase_is_noop_exit_zero(self, tmp_path: Path) -> None:
        _write_plan(tmp_path)
        r = _run_ss("claude", str(tmp_path), "not-a-phase")
        assert r.returncode == 0
        assert r.stdout == ""

    def test_malformed_invocation_missing_tool_fails_open(self) -> None:
        # Omitting the required --tool makes argparse exit 2; the SystemExit branch
        # must recover the event and return 0 (never block startup), not raise.
        r = subprocess.run(
            [sys.executable, "-m", "wade.hooks.cli", "session_start", "--phase", "implement"],
            input="{}",
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0

    def test_exception_in_builder_fails_open(self, monkeypatch, tmp_path: Path) -> None:
        # Any exception building the payload must degrade to a no-op allow — the
        # builder is called in-process so we can force the failure.
        from wade.hooks import cli as hook_cli

        def _boom(*_a: object, **_k: object) -> str:
            raise RuntimeError("boom")

        monkeypatch.setattr(hook_cli, "session_start_context", _boom)
        em = hook_cli._run_session_start("claude", str(tmp_path), "implement")
        assert em.exit_code == 0
        assert em.stdout == ""  # Claude allow no-op

    def test_unreadable_stdin_still_emits(self, monkeypatch, tmp_path: Path) -> None:
        # stdin is read-and-discarded best-effort; an OSError there must not stop
        # the payload (which is built from --root/--phase), nor block startup.
        from wade.hooks import cli as hook_cli

        _write_plan(tmp_path)

        class _BadStdin:
            def read(self) -> str:
                raise OSError("stdin unreadable")

        monkeypatch.setattr(hook_cli.sys, "stdin", _BadStdin())
        em = hook_cli._run_session_start("claude", str(tmp_path), "implement")
        assert em.exit_code == 0
        assert "additionalContext" in em.stdout


class TestLeanEntryParity:
    """``wade hook session_start`` and lean ``wade-hook session_start`` must match."""

    def test_claude_payload_identical(self, tmp_path: Path) -> None:
        _write_plan(tmp_path)
        lean = _run_ss("claude", str(tmp_path), "implement")
        alias = _run_ss_alias("claude", str(tmp_path), "implement")
        assert lean.returncode == alias.returncode == 0
        assert json.loads(lean.stdout) == json.loads(alias.stdout)

    def test_agy_noop_identical(self, tmp_path: Path) -> None:
        _write_plan(tmp_path)
        lean = _run_ss("antigravity-cli", str(tmp_path), "implement")
        alias = _run_ss_alias("antigravity-cli", str(tmp_path), "implement")
        assert lean.stdout == alias.stdout == "{}"

    def test_missing_root_noop_identical(self) -> None:
        lean = _run_ss("claude", None, "implement")
        alias = _run_ss_alias("claude", None, "implement")
        assert lean.returncode == alias.returncode == 0
        assert lean.stdout == alias.stdout == ""

    def test_missing_tool_both_fail_open(self) -> None:
        # The lean parser rejects a missing --tool (argparse required) and recovers
        # to exit 0 in its usage-error branch. The Typer alias must reach the same
        # fail-open outcome — a required --tool there would exit 2 *before* the
        # session_start dispatcher, contradicting the fail-open contract.
        args = ["session_start", "--phase", "implement"]
        lean = subprocess.run(
            [sys.executable, "-m", "wade.hooks.cli", *args],
            input="{}",
            capture_output=True,
            text=True,
        )
        alias = subprocess.run(
            [sys.executable, "-m", "wade", "hook", *args],
            input="{}",
            capture_output=True,
            text=True,
        )
        assert lean.returncode == alias.returncode == 0
        assert lean.stdout == alias.stdout == ""


class TestBuilderParsing:
    def test_bom_prefixed_plan_still_parses_issue(self, tmp_path: Path) -> None:
        # A UTF-8 BOM before the "# Issue #..." heading must not suppress the ref
        # (read with utf-8-sig). write_plan_md never emits a BOM, but a hand-edited
        # or tool-converted PLAN.md might.
        bom = "﻿"
        (tmp_path / "PLAN.md").write_text(f"{bom}{_PLAN_FIRST_LINE}\n\nbody\n", encoding="utf-8")
        payload = session_start_context(tmp_path, SessionPhase.IMPLEMENT)
        assert payload is not None
        assert "Issue #351" in payload


class TestBuilderBudget:
    def test_payload_under_cap_even_with_a_huge_title(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, first_line="# Issue #999: " + "x" * 5000)
        for phase in SessionPhase:
            payload = session_start_context(tmp_path, phase)
            assert payload is not None
            assert len(payload) <= _SESSION_CONTEXT_MAX_CHARS

    def test_hard_cap_truncates_defensively(self, monkeypatch, tmp_path: Path) -> None:
        # Shrink the cap below the natural payload length to exercise the
        # defensive truncation branch directly.
        import wade.hooks.policies as policies

        _write_plan(tmp_path)
        monkeypatch.setattr(policies, "_SESSION_CONTEXT_MAX_CHARS", 50)
        payload = session_start_context(tmp_path, SessionPhase.IMPLEMENT)
        assert payload is not None
        assert len(payload) <= 50
        assert payload.endswith("…")


class TestPayloadSkillNoOverlap:
    """The injected payload must not restate a prose line from the always-loaded skill.

    The skill is loaded every session; the payload's value is being *distinct*
    (compact reminders/pointers, not a second copy). Prose-scoped: short lines and
    bare single-backtick-span lines are excluded, so the one string both legitimately
    share (the exact closing command) never fails the test by construction.
    """

    _PROSE_MIN_LEN = 40

    def _is_prose(self, line: str) -> bool:
        if len(line) < self._PROSE_MIN_LEN:
            return False
        # A line that is entirely one backtick-code span (e.g. a bare command).
        return not (line.startswith("`") and line.endswith("`") and line.count("`") == 2)

    def _skill_lines(self, phase: SessionPhase) -> set[str]:
        skill = _TEMPLATES / _PHASE_SKILL[phase] / "SKILL.md"
        return {ln.strip() for ln in skill.read_text(encoding="utf-8").splitlines() if ln.strip()}

    def _payload_lines(self, phase: SessionPhase, tmp_path: Path) -> list[str]:
        _write_plan(tmp_path)
        payload = session_start_context(tmp_path, phase)
        assert payload is not None
        return [ln.strip() for ln in payload.splitlines() if ln.strip()]

    def test_no_prose_line_appears_verbatim_in_skill(self, tmp_path: Path) -> None:
        for phase in SessionPhase:
            skill_lines = self._skill_lines(phase)
            for line in self._payload_lines(phase, tmp_path):
                if not self._is_prose(line):
                    continue
                assert line not in skill_lines, (
                    f"{phase}: payload line duplicates SKILL.md: {line!r}"
                )

    def test_payload_within_budget_per_phase(self, tmp_path: Path) -> None:
        for phase in SessionPhase:
            _write_plan(tmp_path)
            payload = session_start_context(tmp_path, phase)
            assert payload is not None
            assert len(payload) <= _SESSION_CONTEXT_MAX_CHARS
