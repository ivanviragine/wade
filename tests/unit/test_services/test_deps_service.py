"""Tests for dependency analysis service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.models.config import ProjectConfig, ProjectSettings
from wade.models.delegation import DelegationMode, DelegationResult
from wade.models.deps import DependencyEdge, DependencyGraph
from wade.models.permission import PermissionMode
from wade.models.task import Task, TaskState
from wade.services.deps_service import (
    _find_existing_tracking_issue,
    _run_delegation,
    analyze_deps,
    apply_deps_to_issues,
    build_context,
    build_deps_prompt,
    build_deps_section,
    create_tracking_issue,
    get_deps_prompt_template,
    parse_deps_output,
    strip_deps_section,
)

# ---------------------------------------------------------------------------
# Prompt template tests
# ---------------------------------------------------------------------------


class TestPromptTemplate:
    def test_template_exists(self) -> None:
        template = get_deps_prompt_template()
        assert len(template) > 50
        assert "Dependency-analysis operation contract" in template
        assert "bounded" in template

    def test_build_prompt(self) -> None:
        prompt = build_deps_prompt(
            "## Issue #1: Add auth\n\nAdd login page.",
            '<method position="0" ref="builtin:dependency-analysis">\nMethod\n</method>',
        )
        assert "## Issue #1: Add auth" in prompt
        assert "Add login page" in prompt
        assert "builtin:dependency-analysis" in prompt
        assert "<operation-input>" in prompt
        assert "Required result contract" in prompt


# ---------------------------------------------------------------------------
# Context building tests
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_builds_from_issues(self) -> None:
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Add auth", body="Login page needed"),
            Task(id="2", title="Add DB", body="SQLite schema"),
        ]
        context = build_context(provider, ["1", "2"])
        assert "## Issue #1: Add auth" in context
        assert "## Issue #2: Add DB" in context
        assert "Login page needed" in context

    def test_handles_read_failure(self) -> None:
        provider = MagicMock()
        provider.read_task.side_effect = Exception("not found")
        context = build_context(provider, ["99"])
        assert "## Issue #99: (could not read)" in context


# ---------------------------------------------------------------------------
# Edge parsing tests
# ---------------------------------------------------------------------------


class TestParseEdges:
    def test_basic_edges(self) -> None:
        text = "1 -> 2 # auth before UI\n3 -> 2 # DB before UI\n"
        edges = parse_deps_output(text, {"1", "2", "3"})
        assert len(edges) == 2
        assert edges[0].from_task == "1"
        assert edges[0].to_task == "2"
        assert edges[0].reason == "auth before UI"

    def test_strips_markdown(self) -> None:
        text = "- `1 -> 2` # reason\n1. 3 -> 4 # another\n"
        edges = parse_deps_output(text, {"1", "2", "3", "4"})
        assert len(edges) == 2

    def test_skips_invalid_numbers(self) -> None:
        text = "1 -> 99 # invalid\n"
        edges = parse_deps_output(text, {"1", "2"})
        assert len(edges) == 0

    def test_skips_comments(self) -> None:
        text = "# This is a comment\n1 -> 2 # valid edge\n"
        edges = parse_deps_output(text, {"1", "2"})
        assert len(edges) == 1

    def test_skips_empty_lines(self) -> None:
        text = "\n\n1 -> 2 # edge\n\n\n"
        edges = parse_deps_output(text, {"1", "2"})
        assert len(edges) == 1

    def test_no_deps_found(self) -> None:
        text = "# No dependencies found\n"
        edges = parse_deps_output(text, {"1", "2"})
        assert len(edges) == 0

    def test_edge_without_reason(self) -> None:
        text = "1 -> 2\n"
        edges = parse_deps_output(text, {"1", "2"})
        assert len(edges) == 1
        assert edges[0].reason == ""

    def test_spaces_around_arrow(self) -> None:
        text = "  1   ->   2   # spaced\n"
        edges = parse_deps_output(text, {"1", "2"})
        assert len(edges) == 1

    def test_mixed_valid_invalid(self) -> None:
        text = "1 -> 2 # valid\n1 -> 99 # invalid\n3 -> 2 # valid\n"
        edges = parse_deps_output(text, {"1", "2", "3"})
        assert len(edges) == 2


# ---------------------------------------------------------------------------
# Cross-reference tests
# ---------------------------------------------------------------------------


class TestStripDepsSection:
    def test_removes_deps_section_in_middle(self) -> None:
        body = "## Tasks\n- Do A\n\n## Dependencies\n**Depends on:** #1\n\n## Notes\nSome notes\n"
        stripped = strip_deps_section(body)
        assert "## Dependencies" not in stripped
        assert "## Tasks" in stripped
        assert "## Notes" in stripped

    def test_removes_deps_section_at_end(self) -> None:
        body = "## Tasks\n- Do A\n\n## Dependencies\n**Blocks:** #2\n"
        stripped = strip_deps_section(body)
        assert "## Dependencies" not in stripped
        assert "## Tasks" in stripped

    def test_no_deps_section(self) -> None:
        body = "## Tasks\n- Do A\n"
        stripped = strip_deps_section(body)
        assert "## Tasks" in stripped


class TestBuildDepsSection:
    def test_depends_on(self) -> None:
        edges = [DependencyEdge(from_task="1", to_task="2", reason="")]
        section = build_deps_section("2", edges)
        assert "**Depends on:** #1" in section

    def test_blocks(self) -> None:
        edges = [DependencyEdge(from_task="1", to_task="2", reason="")]
        section = build_deps_section("1", edges)
        assert "**Blocks:** #2" in section

    def test_both(self) -> None:
        edges = [
            DependencyEdge(from_task="1", to_task="2"),
            DependencyEdge(from_task="2", to_task="3"),
        ]
        section = build_deps_section("2", edges)
        assert "**Depends on:** #1" in section
        assert "**Blocks:** #3" in section

    def test_no_deps(self) -> None:
        edges = [DependencyEdge(from_task="1", to_task="2")]
        section = build_deps_section("3", edges)
        assert section == ""


class TestApplyDeps:
    def test_updates_issues(self) -> None:
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Auth", body="## Tasks\n- Login\n"),
            Task(id="2", title="UI", body="## Tasks\n- Dashboard\n"),
        ]
        provider.update_task.return_value = Task(id="1", title="Auth")

        edges = [DependencyEdge(from_task="1", to_task="2")]
        updated = apply_deps_to_issues(provider, ["1", "2"], edges)
        assert updated == 2
        assert provider.update_task.call_count == 2

    def test_skips_unrelated_issues(self) -> None:
        provider = MagicMock()
        provider.read_task.return_value = Task(id="3", title="Other", body="No deps")

        edges = [DependencyEdge(from_task="1", to_task="2")]
        updated = apply_deps_to_issues(provider, ["3"], edges)
        assert updated == 0

    def test_existing_marked_block_not_corrupted(self) -> None:
        # A body that already carries a marked wade:deps block (with the
        # ## Dependencies heading *inside* it). The legacy heading stripper must
        # not orphan the markers — after the update there must be exactly one
        # balanced pair, with surrounding content preserved.
        from wade.services.deps_service import DEPS_MARKER_END, DEPS_MARKER_START

        body = (
            "Intro paragraph.\n\n"
            f"{DEPS_MARKER_START}\n"
            "## Dependencies\n\n"
            "**Depends on:** #9\n"
            f"{DEPS_MARKER_END}\n\n"
            "Trailing content.\n"
        )
        provider = MagicMock()
        provider.read_task.return_value = Task(id="2", title="UI", body=body)
        provider.update_task.return_value = Task(id="2", title="UI")

        edges = [DependencyEdge(from_task="1", to_task="2")]
        updated = apply_deps_to_issues(provider, ["2"], edges)

        assert updated == 1
        new_body = provider.update_task.call_args.kwargs["body"]
        # Exactly one balanced marker pair — no orphaned start marker.
        assert new_body.count(DEPS_MARKER_START) == 1
        assert new_body.count(DEPS_MARKER_END) == 1
        # Surrounding content is preserved and the block is refreshed.
        assert "Intro paragraph." in new_body
        assert "Trailing content." in new_body
        assert "**Depends on:** #1" in new_body


# ---------------------------------------------------------------------------
# Tracking issue tests
# ---------------------------------------------------------------------------


class TestCreateTrackingIssue:
    def test_creates_tracking_issue(self) -> None:
        provider = MagicMock()
        provider.list_tasks.return_value = []
        provider.create_task.return_value = Task(id="10", title="Tracking: #1, #2, #3")

        config = ProjectConfig(project=ProjectSettings(issue_label="feature-plan"))
        graph = DependencyGraph(
            edges=[
                DependencyEdge(from_task="1", to_task="2"),
                DependencyEdge(from_task="2", to_task="3"),
            ]
        )
        titles = {"1": "Auth", "2": "DB", "3": "UI"}

        tracking_id = create_tracking_issue(provider, config, ["1", "2", "3"], graph, titles)
        assert tracking_id == "10"
        provider.create_task.assert_called_once()

        # Verify body content
        call_kwargs = provider.create_task.call_args[1]
        assert "## Execution Plan" in call_kwargs["body"]
        assert "- [ ] #1" in call_kwargs["body"]
        assert "```mermaid" in call_kwargs["body"]

    def test_title_for_many_issues(self) -> None:
        provider = MagicMock()
        provider.list_tasks.return_value = []
        provider.create_task.return_value = Task(id="10", title="Tracking")

        config = ProjectConfig()
        graph = DependencyGraph(edges=[])

        create_tracking_issue(provider, config, ["1", "2", "3", "4"], graph, {})
        call_kwargs = provider.create_task.call_args[1]
        assert "4 issues" in call_kwargs["title"]

    def test_title_for_few_issues(self) -> None:
        provider = MagicMock()
        provider.list_tasks.return_value = []
        provider.create_task.return_value = Task(id="10", title="Tracking")

        config = ProjectConfig()
        graph = DependencyGraph(edges=[])

        create_tracking_issue(provider, config, ["1", "2"], graph, {})
        call_kwargs = provider.create_task.call_args[1]
        assert "#1" in call_kwargs["title"]
        assert "#2" in call_kwargs["title"]

    def test_handles_creation_failure(self) -> None:
        provider = MagicMock()
        provider.list_tasks.return_value = []
        provider.create_task.side_effect = Exception("API error")

        config = ProjectConfig()
        graph = DependencyGraph(edges=[])

        result = create_tracking_issue(provider, config, ["1", "2", "3"], graph, {})
        assert result is None

    def test_skips_creation_when_duplicate_exists(self) -> None:
        """Should return existing tracking issue ID instead of creating a duplicate."""
        provider = MagicMock()
        provider.list_tasks.return_value = [
            Task(id="50", title="Tracking: #1, #2", state=TaskState.OPEN),
        ]

        config = ProjectConfig(project=ProjectSettings(issue_label="feature-plan"))
        graph = DependencyGraph(edges=[])

        result = create_tracking_issue(provider, config, ["1", "2"], graph, {})
        assert result == "50"
        provider.create_task.assert_not_called()

    def test_creates_when_no_duplicate(self) -> None:
        """Should create tracking issue when no existing duplicate is found."""
        provider = MagicMock()
        provider.list_tasks.return_value = [
            Task(id="50", title="Tracking: #3, #4", state=TaskState.OPEN),
        ]
        provider.create_task.return_value = Task(id="60", title="Tracking: #1, #2")

        config = ProjectConfig(project=ProjectSettings(issue_label="feature-plan"))
        graph = DependencyGraph(edges=[])

        result = create_tracking_issue(provider, config, ["1", "2"], graph, {})
        assert result == "60"
        provider.create_task.assert_called_once()


class TestFindExistingTrackingIssue:
    def test_finds_matching_issue(self) -> None:
        provider = MagicMock()
        provider.list_tasks.return_value = [
            Task(id="10", title="Tracking: #1, #2", state=TaskState.OPEN),
        ]
        result = _find_existing_tracking_issue(provider, "feature-plan", "Tracking: #1, #2")
        assert result == "10"
        provider.list_tasks.assert_called_once_with(label="feature-plan", state=None)

    def test_finds_closed_tracking_issue(self) -> None:
        """Should find tracking issues even if they are closed."""
        provider = MagicMock()
        provider.list_tasks.return_value = [
            Task(id="42", title="Tracking: #1, #2", state=TaskState.CLOSED),
        ]
        result = _find_existing_tracking_issue(provider, "feature-plan", "Tracking: #1, #2")
        assert result == "42"
        provider.list_tasks.assert_called_once_with(label="feature-plan", state=None)

    def test_returns_none_when_no_match(self) -> None:
        provider = MagicMock()
        provider.list_tasks.return_value = [
            Task(id="10", title="Tracking: #3, #4", state=TaskState.OPEN),
        ]
        result = _find_existing_tracking_issue(provider, "feature-plan", "Tracking: #1, #2")
        assert result is None

    def test_returns_none_on_error(self) -> None:
        provider = MagicMock()
        provider.list_tasks.side_effect = Exception("API error")
        result = _find_existing_tracking_issue(provider, "feature-plan", "Tracking: #1, #2")
        assert result is None


# ---------------------------------------------------------------------------
# Delegation helper tests
# ---------------------------------------------------------------------------


class TestRunDelegation:
    """_run_delegation wraps delegate() and returns the full DelegationResult (#366)."""

    @patch("wade.services.deps_service.delegate")
    def test_successful_delegation(self, mock_delegate: MagicMock) -> None:
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="1 -> 2 # auth before UI",
            mode=DelegationMode.HEADLESS,
        )
        result = _run_delegation("claude", "Analyze deps", DelegationMode.HEADLESS)
        assert result.success is True
        assert result.feedback == "1 -> 2 # auth before UI"

        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.HEADLESS
        assert call_args.ai_tool == "claude"
        assert call_args.prompt == "Analyze deps"

    @patch("wade.services.deps_service.delegate")
    def test_failed_delegation_returns_result(self, mock_delegate: MagicMock) -> None:
        mock_delegate.return_value = DelegationResult(
            success=False,
            feedback="AI tool does not support headless mode",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
        )
        result = _run_delegation("antigravity-cli", "Analyze deps", DelegationMode.HEADLESS)
        assert result.success is False
        assert result.timed_out is False

    @patch("wade.services.deps_service.delegate")
    def test_empty_feedback_returned_verbatim(self, mock_delegate: MagicMock) -> None:
        """The None-collapsing on empty/failure output now lives in analyze_deps."""
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="",
            mode=DelegationMode.HEADLESS,
        )
        result = _run_delegation("claude", "Analyze deps", DelegationMode.HEADLESS)
        assert result.success is True
        assert result.feedback == ""

    @patch("wade.services.deps_service.delegate")
    def test_timed_out_delegation_flagged(self, mock_delegate: MagicMock) -> None:
        mock_delegate.return_value = DelegationResult(
            success=False,
            feedback="1 -> 2 # partial",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
            timed_out=True,
        )
        result = _run_delegation("claude", "Analyze deps", DelegationMode.HEADLESS)
        assert result.timed_out is True
        assert result.feedback == "1 -> 2 # partial"

    @patch("wade.services.deps_service.delegate")
    def test_passes_model_and_effort(self, mock_delegate: MagicMock) -> None:
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="1 -> 2",
            mode=DelegationMode.HEADLESS,
        )
        _run_delegation(
            "claude",
            "Analyze",
            DelegationMode.HEADLESS,
            model="claude-haiku-4-5",
            effort="low",
        )
        call_args = mock_delegate.call_args[0][0]
        assert call_args.model == "claude-haiku-4-5"
        assert call_args.effort == "low"

    @patch("wade.services.deps_service.delegate")
    def test_interactive_mode(self, mock_delegate: MagicMock) -> None:
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="1 -> 2 # reason",
            mode=DelegationMode.INTERACTIVE,
        )
        result = _run_delegation("claude", "Analyze", DelegationMode.INTERACTIVE)
        assert result.feedback == "1 -> 2 # reason"
        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.INTERACTIVE


# ---------------------------------------------------------------------------
# analyze_deps mode / prompt tests
# ---------------------------------------------------------------------------


def _stub_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a ready runtime so mode/timeout tests exercise delegation, not readiness.

    These tests hand ``analyze_deps`` a bare ``tmp_path`` as the reused planning
    worktree; the real check would correctly reject it as not-a-worktree.
    """
    from wade.services.check_service import CheckExitCode, CheckResult, CheckStatus

    monkeypatch.setattr(
        "wade.services.check_service.check_session_readiness",
        lambda *_args, **_kwargs: CheckResult(
            status=CheckStatus.IN_WORKTREE,
            exit_code=CheckExitCode.IN_WORKTREE,
        ),
    )


class TestAnalyzeDepsMode:
    """Tests for analyze_deps with mode parameter and prompt-mode early return."""

    @pytest.fixture(autouse=True)
    def _ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ready(monkeypatch)

    @patch("wade.services.deps_service.console.out.print")
    @patch("wade.services.deps_service.create_tracking_issue")
    @patch("wade.services.deps_service.apply_deps_to_issues")
    @patch("wade.services.deps_service.delegate")
    @patch("wade.services.deps_service.confirm_ai_selection")
    @patch("wade.services.deps_service.resolve_effort")
    @patch("wade.services.deps_service.resolve_model")
    @patch("wade.services.deps_service.resolve_ai_tool")
    @patch("wade.services.deps_service.get_provider")
    @patch("wade.services.deps_service.load_config")
    def test_prompt_mode_returns_empty_graph(
        self,
        mock_config: MagicMock,
        mock_provider: MagicMock,
        mock_resolve_tool: MagicMock,
        mock_resolve_model: MagicMock,
        mock_resolve_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
        mock_apply: MagicMock,
        mock_tracking: MagicMock,
        mock_print: MagicMock,
    ) -> None:
        """Prompt mode should return empty graph and skip edge parsing."""
        from wade.models.config import AICommandConfig, AIConfig

        mock_config.return_value = ProjectConfig(ai=AIConfig(deps=AICommandConfig(tool="claude")))
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
        ]
        mock_provider.return_value = provider
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="raw prompt text",
            mode=DelegationMode.PROMPT,
        )

        result = analyze_deps(["1", "2"], mode="prompt")
        assert result is not None
        assert result.edges == []
        # markup=False keeps untrusted AI output from being parsed as Rich
        # markup (bracketed tokens would otherwise crash — #394).
        mock_print.assert_called_once_with("raw prompt text", markup=False)
        mock_resolve_tool.assert_not_called()
        mock_resolve_model.assert_not_called()
        mock_resolve_effort.assert_not_called()
        mock_confirm.assert_not_called()
        mock_apply.assert_not_called()
        mock_tracking.assert_not_called()

    @patch("wade.services.deps_service.create_tracking_issue")
    @patch("wade.services.deps_service.apply_deps_to_issues")
    @patch("wade.services.deps_service.delegate")
    @patch("wade.services.deps_service.confirm_ai_selection")
    @patch("wade.services.deps_service.resolve_effort")
    @patch("wade.services.deps_service.resolve_model")
    @patch("wade.services.deps_service.resolve_ai_tool")
    @patch("wade.services.deps_service.get_provider")
    @patch("wade.services.deps_service.load_config")
    def test_prompt_mode_output_with_bracket_markup_does_not_raise(
        self,
        mock_config: MagicMock,
        mock_provider: MagicMock,
        mock_resolve_tool: MagicMock,
        mock_resolve_model: MagicMock,
        mock_resolve_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
        mock_apply: MagicMock,
        mock_tracking: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Bracketed markup in PROMPT-mode output prints literally, not crash (#394).

        Uses the real console (no ``console.out.print`` mock) so Rich actually
        renders the untrusted AI output. The ``[/]`` is *unbalanced* (nothing to
        open) — with ``markup=True`` this raised ``rich.errors.MarkupError``.
        """
        from wade.models.config import AICommandConfig, AIConfig

        mock_config.return_value = ProjectConfig(ai=AIConfig(deps=AICommandConfig(tool="claude")))
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
        ]
        mock_provider.return_value = provider
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="analysis: the token result[/] must stay literal",
            mode=DelegationMode.PROMPT,
        )

        result = analyze_deps(["1", "2"], mode="prompt")

        assert result is not None
        assert result.edges == []
        assert "[/]" in capsys.readouterr().out

    @patch("wade.services.deps_service.create_tracking_issue")
    @patch("wade.services.deps_service.apply_deps_to_issues")
    @patch("wade.services.deps_service.delegate")
    @patch("wade.services.deps_service.confirm_ai_selection")
    @patch("wade.services.deps_service.resolve_effort")
    @patch("wade.services.deps_service.resolve_model")
    @patch("wade.services.deps_service.resolve_ai_tool")
    @patch("wade.services.deps_service.get_provider")
    @patch("wade.services.deps_service.load_config")
    def test_explicit_mode_override(
        self,
        mock_config: MagicMock,
        mock_provider: MagicMock,
        mock_resolve_tool: MagicMock,
        mock_resolve_model: MagicMock,
        mock_resolve_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
        mock_apply: MagicMock,
        mock_tracking: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Explicit mode='headless' should override config mode."""
        from wade.models.config import AICommandConfig, AIConfig

        mock_config.return_value = ProjectConfig(
            ai=AIConfig(deps=AICommandConfig(tool="claude", mode="prompt"))
        )
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
        ]
        mock_provider.return_value = provider
        mock_resolve_tool.return_value = "claude"
        mock_resolve_model.return_value = None
        mock_resolve_effort.return_value = None
        mock_confirm.return_value = ("claude", None, None, False)
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="1 -> 2 # auth before UI",
            mode=DelegationMode.HEADLESS,
        )
        mock_apply.return_value = 2
        mock_tracking.return_value = "10"

        result = analyze_deps(["1", "2"], mode="headless", planning_worktree=tmp_path)
        assert result is not None
        # Should have parsed the edge since not in prompt mode
        assert len(result.edges) == 1
        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.HEADLESS

    @patch("wade.services.deps_service.create_tracking_issue")
    @patch("wade.services.deps_service.apply_deps_to_issues")
    @patch("wade.services.deps_service.delegate")
    @patch("wade.services.deps_service.confirm_ai_selection")
    @patch("wade.services.deps_service.resolve_effort")
    @patch("wade.services.deps_service.resolve_model")
    @patch("wade.services.deps_service.resolve_ai_tool")
    @patch("wade.services.deps_service.get_provider")
    @patch("wade.services.deps_service.load_config")
    def test_headless_timeout_is_forwarded(
        self,
        mock_config: MagicMock,
        mock_provider: MagicMock,
        mock_resolve_tool: MagicMock,
        mock_resolve_model: MagicMock,
        mock_resolve_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
        mock_apply: MagicMock,
        mock_tracking: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ai.deps.timeout should be forwarded into the DelegationRequest."""
        from wade.models.config import AICommandConfig, AIConfig

        mock_config.return_value = ProjectConfig(
            ai=AIConfig(deps=AICommandConfig(tool="claude", mode="headless", timeout=300))
        )
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
        ]
        mock_provider.return_value = provider
        mock_resolve_tool.return_value = "claude"
        mock_resolve_model.return_value = None
        mock_resolve_effort.return_value = None
        mock_confirm.return_value = ("claude", None, None, False)
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="1 -> 2 # auth before UI",
            mode=DelegationMode.HEADLESS,
        )
        mock_apply.return_value = 2
        mock_tracking.return_value = "10"

        result = analyze_deps(["1", "2"], planning_worktree=tmp_path)
        assert result is not None
        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.HEADLESS
        assert call_args.timeout == 300

    @patch("wade.services.deps_service.create_tracking_issue")
    @patch("wade.services.deps_service.apply_deps_to_issues")
    @patch("wade.services.deps_service.delegate")
    @patch("wade.services.deps_service.confirm_ai_selection")
    @patch("wade.services.deps_service.resolve_effort")
    @patch("wade.services.deps_service.resolve_model")
    @patch("wade.services.deps_service.resolve_ai_tool")
    @patch("wade.services.deps_service.get_provider")
    @patch("wade.services.deps_service.load_config")
    def test_scaled_timeout_when_unset(
        self,
        mock_config: MagicMock,
        mock_provider: MagicMock,
        mock_resolve_tool: MagicMock,
        mock_resolve_model: MagicMock,
        mock_resolve_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
        mock_apply: MagicMock,
        mock_tracking: MagicMock,
        tmp_path: Path,
    ) -> None:
        """With ai.deps.timeout unset, analyze_deps scales the budget from the prompt."""
        from wade.models.config import AICommandConfig, AIConfig
        from wade.services.delegation_service import TIMEOUT_FLOOR, effective_timeout

        mock_config.return_value = ProjectConfig(
            ai=AIConfig(deps=AICommandConfig(tool="claude", mode="headless"))
        )
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
        ]
        mock_provider.return_value = provider
        mock_resolve_tool.return_value = "claude"
        mock_resolve_model.return_value = None
        mock_resolve_effort.return_value = None
        mock_confirm.return_value = ("claude", None, None, False)
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="1 -> 2 # auth before UI",
            mode=DelegationMode.HEADLESS,
        )
        mock_apply.return_value = 2
        mock_tracking.return_value = "10"

        result = analyze_deps(["1", "2"], planning_worktree=tmp_path)
        assert result is not None
        call_args = mock_delegate.call_args[0][0]
        # Timeout is the scaled value for this exact prompt (not a flat default).
        assert call_args.timeout == effective_timeout(call_args.prompt, None, None)
        assert call_args.timeout >= TIMEOUT_FLOOR

    @patch("wade.services.deps_service.console")
    @patch("wade.services.deps_service.create_tracking_issue")
    @patch("wade.services.deps_service.apply_deps_to_issues")
    @patch("wade.services.deps_service.delegate")
    @patch("wade.services.deps_service.confirm_ai_selection")
    @patch("wade.services.deps_service.resolve_effort")
    @patch("wade.services.deps_service.resolve_model")
    @patch("wade.services.deps_service.resolve_ai_tool")
    @patch("wade.services.deps_service.get_provider")
    @patch("wade.services.deps_service.load_config")
    def test_headless_prints_budget_advisory(
        self,
        mock_config: MagicMock,
        mock_provider: MagicMock,
        mock_resolve_tool: MagicMock,
        mock_resolve_model: MagicMock,
        mock_resolve_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
        mock_apply: MagicMock,
        mock_tracking: MagicMock,
        mock_console: MagicMock,
        tmp_path: Path,
    ) -> None:
        """#366 review: headless deps must announce the worst-case budget pre-launch,

        the same way _run_review_delegation does, so an agent-driven shell reserves
        enough time instead of killing the call at its own shorter timeout.
        """
        from wade.models.config import AICommandConfig, AIConfig
        from wade.services.delegation_service import effective_timeout, extended_timeout

        mock_config.return_value = ProjectConfig(
            ai=AIConfig(deps=AICommandConfig(tool="claude", mode="headless"))
        )
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
        ]
        mock_provider.return_value = provider
        mock_resolve_tool.return_value = "claude"
        mock_resolve_model.return_value = None
        mock_resolve_effort.return_value = None
        mock_confirm.return_value = ("claude", None, None, False)
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="1 -> 2 # auth before UI",
            mode=DelegationMode.HEADLESS,
        )
        mock_apply.return_value = 2
        mock_tracking.return_value = "10"

        result = analyze_deps(["1", "2"], planning_worktree=tmp_path)
        assert result is not None
        call_args = mock_delegate.call_args[0][0]
        budget = effective_timeout(call_args.prompt, None, None)
        worst_case = budget + extended_timeout(budget)

        info_calls = [c.args[0] for c in mock_console.info.call_args_list]
        assert any(str(worst_case) in msg and str(budget) in msg for msg in info_calls), (
            f"expected an advisory mentioning budget {budget}s / worst-case "
            f"{worst_case}s, got: {info_calls}"
        )

    @patch("wade.services.deps_service.console")
    @patch("wade.services.deps_service.create_tracking_issue")
    @patch("wade.services.deps_service.apply_deps_to_issues")
    @patch("wade.services.deps_service.delegate")
    @patch("wade.services.deps_service.confirm_ai_selection")
    @patch("wade.services.deps_service.resolve_effort")
    @patch("wade.services.deps_service.resolve_model")
    @patch("wade.services.deps_service.resolve_ai_tool")
    @patch("wade.services.deps_service.get_provider")
    @patch("wade.services.deps_service.load_config")
    def test_timed_out_returns_none_without_applying(
        self,
        mock_config: MagicMock,
        mock_provider: MagicMock,
        mock_resolve_tool: MagicMock,
        mock_resolve_model: MagicMock,
        mock_resolve_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
        mock_apply: MagicMock,
        mock_tracking: MagicMock,
        mock_console: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A timed-out deps run returns None, warns (timeout, not crash), applies nothing."""
        from wade.models.config import AICommandConfig, AIConfig

        mock_config.return_value = ProjectConfig(
            ai=AIConfig(deps=AICommandConfig(tool="claude", mode="headless"))
        )
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
        ]
        mock_provider.return_value = provider
        mock_resolve_tool.return_value = "claude"
        mock_resolve_model.return_value = None
        mock_resolve_effort.return_value = None
        mock_confirm.return_value = ("claude", None, None, False)
        mock_delegate.return_value = DelegationResult(
            success=False,
            feedback="1 -> 2 # partial",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
            timed_out=True,
        )

        result = analyze_deps(["1", "2"], mode="headless", planning_worktree=tmp_path)

        assert result is None
        mock_apply.assert_not_called()
        mock_tracking.assert_not_called()
        warn_text = " ".join(str(c.args[0]) for c in mock_console.warn.call_args_list if c.args)
        assert "timed out" in warn_text.lower()


# ---------------------------------------------------------------------------
# analyze_deps permission-mode resolution + forwarding
# ---------------------------------------------------------------------------


class TestAnalyzeDepsIsolationFailure:
    """A failed detached-worktree setup must never launch in the caller's cwd."""

    @staticmethod
    def _configure_headless(
        monkeypatch: pytest.MonkeyPatch,
        delegate: MagicMock,
        *,
        knowledge_enabled: bool = False,
    ) -> None:
        from wade.models.config import AICommandConfig, AIConfig, KnowledgeConfig

        config = ProjectConfig(
            ai=AIConfig(deps=AICommandConfig(tool="claude", mode="headless")),
            knowledge=KnowledgeConfig(enabled=knowledge_enabled),
        )
        monkeypatch.setattr("wade.services.deps_service.load_config", lambda *_: config)
        monkeypatch.setattr("wade.services.deps_service.get_provider", lambda _: MagicMock())
        monkeypatch.setattr(
            "wade.services.deps_service.resolve_ai_tool", lambda *_args, **_kwargs: "claude"
        )
        monkeypatch.setattr(
            "wade.services.deps_service.resolve_model", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(
            "wade.services.deps_service.resolve_effort", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(
            "wade.services.deps_service.confirm_ai_selection",
            lambda *_args, **_kwargs: ("claude", None, None, PermissionMode.DEFAULT),
        )
        monkeypatch.setattr("wade.services.deps_service.delegate", delegate)

    def test_creation_failure_does_not_fall_back_to_caller_checkout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        delegate = MagicMock()
        self._configure_headless(monkeypatch, delegate)
        monkeypatch.setattr("wade.git.repo.get_repo_root", lambda _: tmp_path)
        monkeypatch.setattr(
            "wade.git.worktree.create_detached_worktree",
            lambda **_kwargs: (_ for _ in ()).throw(OSError("sandbox denied worktree setup")),
        )

        result = analyze_deps(["1", "2"], mode="headless", project_root=tmp_path)

        assert result is None
        delegate.assert_not_called()

    def test_readiness_failure_removes_empty_worktree_before_agent_launch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from wade.services.check_service import CheckExitCode, CheckResult, CheckStatus

        delegate = MagicMock()
        self._configure_headless(monkeypatch, delegate)
        worktree = tmp_path / "deps-worktree"
        remove = MagicMock()
        monkeypatch.setattr("wade.git.repo.get_repo_root", lambda _: tmp_path)
        monkeypatch.setattr(
            "wade.git.worktree.create_detached_worktree",
            lambda **_kwargs: worktree,
        )
        monkeypatch.setattr(
            "wade.services.implementation_service.bootstrap_worktree",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            "wade.services.check_service.check_session_readiness",
            lambda *_args, **_kwargs: CheckResult(
                status=CheckStatus.KNOWLEDGE_STAGING_BLOCKED,
                exit_code=CheckExitCode.KNOWLEDGE_STAGING_BLOCKED,
            ),
        )
        monkeypatch.setattr("wade.git.worktree.remove_worktree", remove)

        result = analyze_deps(["1", "2"], mode="headless", project_root=tmp_path)

        assert result is None
        delegate.assert_not_called()
        remove.assert_called_once_with(tmp_path, worktree, force=True)

    def test_blocked_planning_worktree_stops_delegation_without_removing_it(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A reused planning worktree is checked too — and never deleted from here."""
        from wade.services.check_service import CheckExitCode, CheckResult, CheckStatus

        delegate = MagicMock()
        self._configure_headless(monkeypatch, delegate)
        planning_worktree = tmp_path / "plan-worktree"
        planning_worktree.mkdir()
        remove = MagicMock()
        monkeypatch.setattr(
            "wade.services.check_service.check_session_readiness",
            lambda *_args, **_kwargs: CheckResult(
                status=CheckStatus.KNOWLEDGE_STAGING_BLOCKED,
                exit_code=CheckExitCode.KNOWLEDGE_STAGING_BLOCKED,
            ),
        )
        monkeypatch.setattr("wade.git.worktree.remove_worktree", remove)

        result = analyze_deps(
            ["1", "2"],
            mode="headless",
            project_root=tmp_path,
            planning_worktree=planning_worktree,
        )

        assert result is None
        delegate.assert_not_called()
        # Owned by the parent `wade plan` lifecycle, which flushes its votes.
        remove.assert_not_called()
        assert planning_worktree.is_dir()

    def test_handoff_failure_preserves_durable_dependency_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from wade.services.check_service import CheckExitCode, CheckResult, CheckStatus
        from wade.services.knowledge_service import StagedRatingsFlushResult

        delegate = MagicMock(
            return_value=DelegationResult(
                success=True,
                feedback="1 -> 2 # schema must precede API",
                mode=DelegationMode.HEADLESS,
            )
        )
        self._configure_headless(monkeypatch, delegate, knowledge_enabled=True)
        worktree = tmp_path / "deps-worktree"
        worktree.mkdir()
        provider = MagicMock()
        provider.read_task.return_value = Task(id="1", title="Schema", body="Create schema")
        remove = MagicMock()
        monkeypatch.setattr("wade.services.deps_service.get_provider", lambda _: provider)
        monkeypatch.setattr("wade.git.repo.get_repo_root", lambda _: tmp_path)
        monkeypatch.setattr(
            "wade.git.worktree.create_detached_worktree",
            lambda **_kwargs: worktree,
        )
        monkeypatch.setattr(
            "wade.services.implementation_service.bootstrap_worktree",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            "wade.services.check_service.check_session_readiness",
            lambda *_args, **_kwargs: CheckResult(
                status=CheckStatus.IN_WORKTREE,
                exit_code=CheckExitCode.IN_WORKTREE,
            ),
        )
        monkeypatch.setattr(
            "wade.services.knowledge_service.flush_staged_ratings",
            lambda *_args, **_kwargs: StagedRatingsFlushResult(
                success=False,
                message="main checkout is read-only",
            ),
        )
        monkeypatch.setattr("wade.git.worktree.remove_worktree", remove)

        result = analyze_deps(["1", "2"], mode="headless", project_root=tmp_path)

        assert result is None
        snapshot = worktree / ".wade" / "deps-analysis-output.txt"
        assert snapshot.read_text(encoding="utf-8") == "1 -> 2 # schema must precede API\n"
        remove.assert_not_called()

    def test_retained_votes_are_swept_before_a_new_worktree_is_created(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Otherwise a preserved worktree's votes have nothing that ever retries them."""
        delegate = MagicMock()
        self._configure_headless(monkeypatch, delegate, knowledge_enabled=True)
        sweep = MagicMock()
        monkeypatch.setattr("wade.services.deps_service.report_retained_vote_recovery", sweep)
        monkeypatch.setattr("wade.git.repo.get_repo_root", lambda _: tmp_path)
        monkeypatch.setattr(
            "wade.git.worktree.create_detached_worktree",
            lambda **_kwargs: (_ for _ in ()).throw(OSError("sandbox denied worktree setup")),
        )

        result = analyze_deps(["1", "2"], mode="headless", project_root=tmp_path)

        # Ran even though this session then failed to start: recovery is about
        # the *previous* session's votes, not this one's.
        assert result is None
        sweep.assert_called_once()
        assert sweep.call_args.args[0] == tmp_path


def _echo_confirm(
    resolved_tool: str | None,
    resolved_model: str | None,
    *,
    resolved_effort: object = None,
    resolved_permission_mode: PermissionMode = PermissionMode.DEFAULT,
    **_kwargs: object,
) -> tuple[str | None, str | None, object, PermissionMode]:
    """confirm_ai_selection stand-in that echoes the display mode it was handed."""
    return resolved_tool, resolved_model, resolved_effort, resolved_permission_mode


class TestAnalyzeDepsPermissionMode:
    @pytest.fixture(autouse=True)
    def _ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ready(monkeypatch)

    @patch("wade.services.deps_service.create_tracking_issue")
    @patch("wade.services.deps_service.apply_deps_to_issues")
    @patch("wade.services.deps_service.delegate")
    @patch("wade.services.deps_service.confirm_ai_selection")
    @patch("wade.services.deps_service.resolve_effort")
    @patch("wade.services.deps_service.resolve_model")
    @patch("wade.services.deps_service.resolve_ai_tool")
    @patch("wade.services.deps_service.get_provider")
    @patch("wade.services.deps_service.load_config")
    def test_headless_deps_forces_default_even_with_config_yolo(
        self,
        mock_config: MagicMock,
        mock_provider: MagicMock,
        mock_resolve_tool: MagicMock,
        mock_resolve_model: MagicMock,
        mock_resolve_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
        mock_apply: MagicMock,
        mock_tracking: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Headless deps stays read-only: no yolo reaches the request."""
        from wade.models.config import AICommandConfig, AIConfig

        mock_config.return_value = ProjectConfig(
            ai=AIConfig(deps=AICommandConfig(tool="claude", mode="headless", yolo=True))
        )
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
        ]
        mock_provider.return_value = provider
        mock_resolve_tool.return_value = "claude"
        mock_resolve_model.return_value = None
        mock_resolve_effort.return_value = None
        mock_confirm.side_effect = _echo_confirm
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="1 -> 2 # auth before UI", mode=DelegationMode.HEADLESS
        )
        mock_apply.return_value = 2
        mock_tracking.return_value = "10"

        result = analyze_deps(["1", "2"], planning_worktree=tmp_path)
        assert result is not None
        request = mock_delegate.call_args[0][0]
        assert request.permission_mode == PermissionMode.DEFAULT

    @patch("wade.services.deps_service.create_tracking_issue")
    @patch("wade.services.deps_service.apply_deps_to_issues")
    @patch("wade.services.deps_service.delegate")
    @patch("wade.services.deps_service.confirm_ai_selection")
    @patch("wade.services.deps_service.resolve_effort")
    @patch("wade.services.deps_service.resolve_model")
    @patch("wade.services.deps_service.resolve_ai_tool")
    @patch("wade.services.deps_service.get_provider")
    @patch("wade.services.deps_service.load_config")
    def test_interactive_deps_forwards_config_yolo(
        self,
        mock_config: MagicMock,
        mock_provider: MagicMock,
        mock_resolve_tool: MagicMock,
        mock_resolve_model: MagicMock,
        mock_resolve_effort: MagicMock,
        mock_confirm: MagicMock,
        mock_delegate: MagicMock,
        mock_apply: MagicMock,
        mock_tracking: MagicMock,
        tmp_path: Path,
    ) -> None:
        """`wade task deps --mode interactive` with ai.deps.yolo: true launches yolo.

        Intentional, documented behavior change — deps interactive now forwards
        the resolved autonomy tier to the request (was silently discarded)."""
        from wade.models.config import AICommandConfig, AIConfig

        mock_config.return_value = ProjectConfig(
            ai=AIConfig(deps=AICommandConfig(tool="claude", mode="interactive", yolo=True))
        )
        provider = MagicMock()
        provider.read_task.side_effect = [
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
            Task(id="1", title="Auth", body="Login"),
            Task(id="2", title="DB", body="Schema"),
        ]
        mock_provider.return_value = provider
        mock_resolve_tool.return_value = "claude"
        mock_resolve_model.return_value = None
        mock_resolve_effort.return_value = None
        mock_confirm.side_effect = _echo_confirm
        mock_delegate.return_value = DelegationResult(
            success=True, feedback="1 -> 2 # auth before UI", mode=DelegationMode.INTERACTIVE
        )
        mock_apply.return_value = 2
        mock_tracking.return_value = "10"

        result = analyze_deps(["1", "2"], mode="interactive", planning_worktree=tmp_path)
        assert result is not None
        request = mock_delegate.call_args[0][0]
        assert request.permission_mode == PermissionMode.YOLO
