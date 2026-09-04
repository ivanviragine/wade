"""Tests for the shared parent-runtime probe (#480).

The contract under test is conservatism: wade may say ``sandboxed`` or
``unrestricted`` only where a runtime published a real signal, and must say
``unknown`` everywhere else. A confident wrong cause is worse than no cause,
because the whole value of the diagnosis is that a user can act on it without
checking it first.
"""

from __future__ import annotations

import pytest

from wade.utils.runtime_env import (
    CODEX_NETWORK_DISABLED_ENV,
    CODEX_SANDBOX_ENV,
    UNNAMED_RUNTIME_LABEL,
    ParentRuntime,
    SandboxAssessment,
    assess_parent_sandbox,
    detect_ai_cli_env,
    detect_parent_runtime,
    has_explicit_sandbox_denial,
    inherited_sandbox_finding,
    looks_like_sandbox_denial,
    possible_inherited_sandbox_cause,
    requires_unsandboxed_relaunch,
)


class TestIdentityProbe:
    """``detect_ai_cli_env`` keeps its historical contract verbatim."""

    @pytest.mark.parametrize(
        ("variable", "expected"),
        [
            ("CLAUDE_CODE", "CLAUDE_CODE"),
            ("CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE"),
            ("COPILOT_CLI", "COPILOT_CLI"),
            ("CODEX_CLI", "CODEX_CLI"),
            ("CODEX_SESSION_ID", "CODEX_CLI"),
            ("CODEX_THREAD_ID", "CODEX_CLI"),
            ("CURSOR_CLI", "CURSOR_CLI"),
            ("ANTIGRAVITY_AGENT", "ANTIGRAVITY_AGENT"),
        ],
    )
    def test_each_marker_maps_to_its_key(self, variable: str, expected: str) -> None:
        assert detect_ai_cli_env({variable: "1"}) == expected

    def test_no_marker_is_no_parent(self) -> None:
        assert detect_ai_cli_env({"PATH": "/usr/bin"}) is None

    def test_empty_value_does_not_count_as_present(self) -> None:
        assert detect_ai_cli_env({"CODEX_CLI": ""}) is None


class TestSandboxAssessment:
    """Only a published signal moves the verdict off ``UNKNOWN``."""

    @pytest.mark.parametrize(
        "value",
        ["seatbelt", "linux-seccomp", "workspace-write", "read-only", "some-future-policy"],
    )
    def test_a_named_policy_reads_as_sandboxed(self, value: str) -> None:
        # Codex exports the variable only when a policy is in force, so an
        # unrecognised name is still a policy — fail toward "sandboxed", where a
        # wrong guess costs a redundant hint rather than a restored opaque error.
        assessment, signal = assess_parent_sandbox({CODEX_SANDBOX_ENV: value})
        assert assessment is SandboxAssessment.SANDBOXED
        assert signal == f"{CODEX_SANDBOX_ENV}={value}"

    @pytest.mark.parametrize("value", ["danger-full-access", "none", "off", "DISABLED"])
    def test_an_explicitly_unconfined_mode_reads_as_unrestricted(self, value: str) -> None:
        assessment, _ = assess_parent_sandbox({CODEX_SANDBOX_ENV: value})
        assert assessment is SandboxAssessment.UNRESTRICTED

    def test_network_confinement_is_a_secondary_sandbox_signal(self) -> None:
        assessment, signal = assess_parent_sandbox({CODEX_NETWORK_DISABLED_ENV: "1"})
        assert assessment is SandboxAssessment.SANDBOXED
        assert signal == CODEX_NETWORK_DISABLED_ENV

    def test_a_falsey_network_flag_is_not_a_signal(self) -> None:
        assessment, _ = assess_parent_sandbox({CODEX_NETWORK_DISABLED_ENV: "0"})
        assert assessment is SandboxAssessment.UNKNOWN

    def test_absent_signals_are_unknown_not_unrestricted(self) -> None:
        # An older runtime that never exports the variable must not read as
        # "definitely unconfined" — that would suppress a correct diagnosis.
        assessment, signal = assess_parent_sandbox({})
        assert assessment is SandboxAssessment.UNKNOWN
        assert signal is None

    @pytest.mark.parametrize(
        "variable",
        ["CLAUDE_CODE", "COPILOT_CLI", "CODEX_CLI", "CURSOR_CLI", "ANTIGRAVITY_AGENT"],
    )
    def test_tool_identity_alone_never_produces_a_verdict(self, variable: str) -> None:
        """The core invariant: being *able* to sandbox is not being sandboxed.

        Codex and Cursor both expose a sandbox toggle, so inferring confinement
        from either marker would be wrong exactly half the time — including for
        the unrestricted default this project ships.
        """
        runtime = detect_parent_runtime({variable: "1"})
        assert runtime.detected is True
        assert runtime.sandbox is SandboxAssessment.UNKNOWN
        assert runtime.is_sandboxed is False


class TestParentRuntime:
    def test_a_sandbox_signal_without_identity_still_counts(self) -> None:
        """Confinement is actionable even when the tool cannot be named."""
        runtime = detect_parent_runtime({CODEX_SANDBOX_ENV: "seatbelt"})
        assert runtime.detected is False
        assert runtime.is_sandboxed is True
        assert runtime.label == UNNAMED_RUNTIME_LABEL

    def test_label_names_the_detected_runtime(self) -> None:
        assert detect_parent_runtime({"CODEX_CLI": "1"}).label == "Codex CLI"
        assert detect_parent_runtime({"CLAUDE_CODE": "1"}).label == "Claude Code"


class TestProfileMismatchPredicate:
    """True for exactly one combination, and never on a guess."""

    @pytest.mark.parametrize(
        ("resolved_sandbox", "assessment", "expected"),
        [
            (False, SandboxAssessment.SANDBOXED, True),
            (False, SandboxAssessment.UNRESTRICTED, False),
            (False, SandboxAssessment.UNKNOWN, False),
            (True, SandboxAssessment.SANDBOXED, False),
            (True, SandboxAssessment.UNRESTRICTED, False),
            (True, SandboxAssessment.UNKNOWN, False),
        ],
    )
    def test_truth_table(
        self,
        resolved_sandbox: bool,
        assessment: SandboxAssessment,
        expected: bool,
    ) -> None:
        parent = ParentRuntime(env_var="CODEX_CLI", sandbox=assessment)
        assert (
            requires_unsandboxed_relaunch(resolved_sandbox=resolved_sandbox, parent=parent)
            is expected
        )


class TestRemediationWording:
    def test_the_finding_names_the_runtime_and_the_limitation(self) -> None:
        parent = ParentRuntime(env_var="CODEX_CLI", sandbox=SandboxAssessment.SANDBOXED)
        message = inherited_sandbox_finding(parent, operation="the implementation review")

        assert "Codex CLI" in message
        assert "cannot escape" in message
        assert "the implementation review" in message

    def test_no_wording_claims_the_child_elevated_itself(self) -> None:
        """wade must never imply an inner process widened its own boundary."""
        parent = ParentRuntime(env_var="CODEX_CLI", sandbox=SandboxAssessment.SANDBOXED)
        unknown = ParentRuntime(env_var="CLAUDE_CODE")
        texts = [
            inherited_sandbox_finding(parent, operation="the review session"),
            possible_inherited_sandbox_cause(unknown),
        ]

        for text in texts:
            lowered = text.casefold()
            assert "escape" not in lowered or "cannot escape" in lowered
            for claim in ("now unrestricted", "elevated", "granted host access"):
                assert claim not in lowered

    def test_the_hedged_cause_is_offered_as_a_possibility(self) -> None:
        text = possible_inherited_sandbox_cause(ParentRuntime(env_var="CLAUDE_CODE"))

        assert "cannot confirm" in text
        assert "if it is" in text

    def test_an_explicitly_unrestricted_parent_does_not_get_sandbox_remediation(self) -> None:
        text = possible_inherited_sandbox_cause(
            ParentRuntime(env_var="CODEX_CLI", sandbox=SandboxAssessment.UNRESTRICTED)
        )

        assert "explicitly reports an unrestricted runtime" in text
        assert "executable permissions or network configuration" in text
        assert "no sandbox signal" not in text
        assert "relaunch" not in text


class TestSandboxDenialShapes:
    @pytest.mark.parametrize(
        "text",
        [
            "open /Users/me/.codex/auth.json: permission denied",
            "connect: operation not permitted",
            "EACCES: permission denied",
            "getaddrinfo: could not resolve host api.anthropic.com",
            "network is unreachable",
            "denied by sandbox policy",
            "seatbelt: deny file-read",
            "bash: /usr/local/bin/claude: cannot execute: required file not found",
        ],
    )
    def test_denial_shapes_match(self, text: str) -> None:
        assert looks_like_sandbox_denial(text) is True

    @pytest.mark.parametrize(
        "text",
        ["denied by sandbox policy", "seatbelt: deny file-read", "landlock blocked launch"],
    )
    def test_explicit_policy_markers_support_a_confident_cause(self, text: str) -> None:
        assert has_explicit_sandbox_denial(text) is True

    @pytest.mark.parametrize(
        "text",
        ["permission denied", "connection refused", "exec format error"],
    )
    def test_generic_os_denials_remain_ambiguous(self, text: str) -> None:
        assert looks_like_sandbox_denial(text) is True
        assert has_explicit_sandbox_denial(text) is False

    @pytest.mark.parametrize(
        "text",
        [
            "claude: command not found",
            "no such file or directory",
            "Review found 3 issues in src/wade/services/core.py",
            "",
        ],
    )
    def test_ambiguous_or_unrelated_text_does_not_match(self, text: str) -> None:
        """A missing binary is not evidence of a sandbox.

        From inside a sandbox the host filesystem is exactly what is *not*
        observable, so "a binary that exists on the host but is missing here" is
        unverifiable at the point of failure — and is equally the signature of a
        tool that was simply never installed.
        """
        assert looks_like_sandbox_denial(text) is False
