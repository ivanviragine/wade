"""Tests for dependency analysis service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from wade.models.config import ProjectConfig, ProjectSettings
from wade.models.delegation import DelegationMode, DelegationResult
from wade.models.deps import DependencyEdge, DependencyGraph
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
        assert "{context}" in template

    def test_build_prompt(self) -> None:
        prompt = build_deps_prompt("## Issue #1: Add auth\n\nAdd login page.")
        assert "## Issue #1: Add auth" in prompt
        assert "Add login page" in prompt
        assert "{context}" not in prompt


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
    """Tests for _run_delegation which wraps the generic delegate() infrastructure."""

    @patch("wade.services.deps_service.delegate")
    def test_successful_delegation(self, mock_delegate: MagicMock) -> None:
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="1 -> 2 # auth before UI",
            mode=DelegationMode.HEADLESS,
        )
        result = _run_delegation("claude", "Analyze deps", DelegationMode.HEADLESS)
        assert result == "1 -> 2 # auth before UI"

        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.HEADLESS
        assert call_args.ai_tool == "claude"
        assert call_args.prompt == "Analyze deps"

    @patch("wade.services.deps_service.delegate")
    def test_failed_delegation_returns_none(self, mock_delegate: MagicMock) -> None:
        mock_delegate.return_value = DelegationResult(
            success=False,
            feedback="AI tool does not support headless mode",
            mode=DelegationMode.HEADLESS,
            exit_code=1,
        )
        result = _run_delegation("gemini", "Analyze deps", DelegationMode.HEADLESS)
        assert result is None

    @patch("wade.services.deps_service.delegate")
    def test_empty_feedback_returns_none(self, mock_delegate: MagicMock) -> None:
        mock_delegate.return_value = DelegationResult(
            success=True,
            feedback="",
            mode=DelegationMode.HEADLESS,
        )
        result = _run_delegation("claude", "Analyze deps", DelegationMode.HEADLESS)
        assert result is None

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
        assert result == "1 -> 2 # reason"
        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.INTERACTIVE


# ---------------------------------------------------------------------------
# analyze_deps mode / prompt tests
# ---------------------------------------------------------------------------


class TestAnalyzeDepsMode:
    """Tests for analyze_deps with mode parameter and prompt-mode early return."""

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
    ) -> None:
        """Prompt mode should return empty graph and skip edge parsing."""
        from wade.models.config import AICommandConfig, AIConfig

        mock_config.return_value = ProjectConfig(ai=AIConfig(deps=AICommandConfig(tool="claude")))
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
            feedback="raw prompt text",
            mode=DelegationMode.PROMPT,
        )

        result = analyze_deps(["1", "2"], mode="prompt")
        assert result is not None
        assert result.edges == []
        # Edge parsing and apply should be skipped
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

        result = analyze_deps(["1", "2"], mode="headless")
        assert result is not None
        # Should have parsed the edge since not in prompt mode
        assert len(result.edges) == 1
        call_args = mock_delegate.call_args[0][0]
        assert call_args.mode == DelegationMode.HEADLESS
