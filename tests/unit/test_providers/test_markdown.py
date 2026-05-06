"""Tests for the Markdown provider — file-backed task storage."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wade.models.config import ProjectConfig, ProviderConfig, ProviderID
from wade.models.review import PRReviewStatus
from wade.models.task import Complexity, TaskState
from wade.providers.markdown import (
    DEFAULT_FILE_HEADER,
    MarkdownIssueProvider,
    TaskNotFoundError,
    _format_meta_block,
    _parse_meta_block,
    _parse_sections,
)
from wade.providers.registry import get_provider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_FILE = """# Wade Issues

<!-- Managed by the Wade markdown issue provider. -->

## #1 Add login feature

<!-- wade
state: open
labels: feature, complexity:medium
-->

Body for issue 1.

Multiple paragraphs ok.

## #2 Fix parser bug

<!-- wade
state: closed
labels: bug
-->

Body for issue 2.

## #3 Refactor

Body without metadata block.
"""


@pytest.fixture
def config_factory(tmp_path: Path):
    """Build a MarkdownIssueProvider with a temp file."""

    def _make(content: str | None = None, filename: str = "ISSUES.md") -> MarkdownIssueProvider:
        path = tmp_path / filename
        if content is not None:
            path.write_text(content, encoding="utf-8")
        provider_config = ProviderConfig(
            name=ProviderID.MARKDOWN,
            settings={"path": filename},
        )
        return MarkdownIssueProvider(provider_config, project_root=tmp_path)

    return _make


# ---------------------------------------------------------------------------
# Pure parser tests
# ---------------------------------------------------------------------------


class TestParseMetaBlock:
    def test_simple_pairs(self) -> None:
        meta = _parse_meta_block("state: open\nlabels: feature, bug")
        assert meta == {"state": "open", "labels": "feature, bug"}

    def test_skips_blank_and_invalid(self) -> None:
        meta = _parse_meta_block("\nstate: closed\nno-colon\n")
        assert meta == {"state": "closed"}

    def test_keys_normalized_to_lowercase(self) -> None:
        meta = _parse_meta_block("STATE: open\nLabels: x")
        assert meta == {"state": "open", "labels": "x"}


class TestFormatMetaBlock:
    def test_state_always_emitted(self) -> None:
        block = _format_meta_block({})
        assert "state: open" in block
        assert block.startswith("<!-- wade")
        assert block.endswith("-->")

    def test_labels_omitted_when_empty(self) -> None:
        block = _format_meta_block({"state": "closed", "labels": ""})
        assert "labels" not in block

    def test_labels_emitted_when_set(self) -> None:
        block = _format_meta_block({"state": "open", "labels": "a, b"})
        assert "labels: a, b" in block


class TestParseSections:
    def test_basic_sections(self) -> None:
        sections = _parse_sections(SAMPLE_FILE)
        assert len(sections) == 3
        assert sections[0].id == "1"
        assert sections[0].title == "Add login feature"
        assert sections[0].meta["state"] == "open"
        assert "Body for issue 1" in sections[0].body

    def test_section_without_meta_block(self) -> None:
        sections = _parse_sections(SAMPLE_FILE)
        section = next(s for s in sections if s.id == "3")
        assert section.meta == {}
        assert "Body without metadata" in section.body

    def test_handles_separator_chars_in_heading(self) -> None:
        text = "## #5: Title with colon\n\nbody"
        sections = _parse_sections(text)
        assert sections[0].id == "5"
        assert sections[0].title == "Title with colon"

    def test_handles_em_dash(self) -> None:
        text = "## #6 — Title with dash\n\nbody"
        sections = _parse_sections(text)
        assert sections[0].id == "6"
        assert sections[0].title == "Title with dash"

    def test_subheadings_are_part_of_body(self) -> None:
        text = "## #1 Title\n\n<!-- wade\nstate: open\n-->\n\nbody\n\n### Sub\n\nmore body\n"
        sections = _parse_sections(text)
        assert "### Sub" in sections[0].body

    def test_no_sections_returns_empty(self) -> None:
        assert _parse_sections("# No issues here yet\n") == []


# ---------------------------------------------------------------------------
# Constructor / path resolution
# ---------------------------------------------------------------------------


class TestPathResolution:
    def test_relative_path_resolves_to_project_root(self, tmp_path: Path) -> None:
        config = ProviderConfig(name=ProviderID.MARKDOWN, settings={"path": "docs/ISSUES.md"})
        provider = MarkdownIssueProvider(config, project_root=tmp_path)
        assert provider._path == (tmp_path / "docs/ISSUES.md").resolve()

    def test_absolute_path_honored(self, tmp_path: Path) -> None:
        abs_path = tmp_path / "absolute.md"
        config = ProviderConfig(name=ProviderID.MARKDOWN, settings={"path": str(abs_path)})
        provider = MarkdownIssueProvider(config, project_root=Path("/wherever"))
        assert provider._path == abs_path

    def test_default_filename(self, tmp_path: Path) -> None:
        cfg = ProviderConfig(name=ProviderID.MARKDOWN)
        provider = MarkdownIssueProvider(cfg, project_root=tmp_path)
        assert provider._path.name == "ISSUES.md"


# ---------------------------------------------------------------------------
# CRUD tests
# ---------------------------------------------------------------------------


class TestListTasks:
    def test_lists_open_tasks_only_by_default(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        tasks = provider.list_tasks()
        ids = [t.id for t in tasks]
        # #2 is closed, #3 has no metadata (defaults to open).
        assert "1" in ids
        assert "3" in ids
        assert "2" not in ids

    def test_filters_by_label(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        tasks = provider.list_tasks(label="feature")
        assert [t.id for t in tasks] == ["1"]

    def test_excludes_labels(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        tasks = provider.list_tasks(state=None, exclude_labels=["bug"])
        ids = [t.id for t in tasks]
        assert "2" not in ids
        assert "1" in ids

    def test_lists_closed_state(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        tasks = provider.list_tasks(state=TaskState.CLOSED)
        assert [t.id for t in tasks] == ["2"]

    def test_lists_all_states(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        tasks = provider.list_tasks(state=None)
        assert {t.id for t in tasks} == {"1", "2", "3"}

    def test_respects_limit(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        tasks = provider.list_tasks(state=None, limit=2)
        assert len(tasks) == 2

    def test_complexity_parsed_from_label(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        task = next(t for t in provider.list_tasks() if t.id == "1")
        assert task.complexity == Complexity.MEDIUM

    def test_empty_file(self, config_factory) -> None:
        provider = config_factory("# Wade Issues\n\n")
        assert provider.list_tasks() == []

    def test_missing_file(self, config_factory) -> None:
        provider = config_factory(None)  # No file written
        assert provider.list_tasks() == []


class TestCreateTask:
    def test_creates_first_task(self, config_factory) -> None:
        provider = config_factory(None)
        task = provider.create_task("New task", "Body here", labels=["feature"])
        assert task.id == "1"
        assert task.title == "New task"
        assert task.state == TaskState.OPEN
        assert any(lbl.name == "feature" for lbl in task.labels)
        # Round-trip: read it back.
        reread = provider.read_task("1")
        assert reread.title == "New task"

    def test_appends_to_existing_file(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        task = provider.create_task("Another", "Body", labels=["x"])
        assert task.id == "4"
        # Original tasks still readable.
        assert provider.read_task("1").title == "Add login feature"
        assert provider.read_task("4").title == "Another"

    def test_no_labels(self, config_factory) -> None:
        provider = config_factory(None)
        task = provider.create_task("No labels", "Body")
        assert task.labels == []

    def test_writes_default_header_when_creating_in_empty_file(self, config_factory) -> None:
        provider = config_factory(None)
        provider.create_task("First", "Body")
        text = provider._path.read_text(encoding="utf-8")
        assert DEFAULT_FILE_HEADER.strip() in text


class TestReadTask:
    def test_reads_existing(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        task = provider.read_task("2")
        assert task.title == "Fix parser bug"
        assert task.state == TaskState.CLOSED

    def test_raises_when_not_found(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        with pytest.raises(TaskNotFoundError):
            provider.read_task("999")

    def test_read_or_none_returns_none_when_missing(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        assert provider.read_task_or_none("999") is None

    def test_read_or_none_returns_task_when_present(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        task = provider.read_task_or_none("1")
        assert task is not None
        assert task.id == "1"


class TestUpdateTask:
    def test_update_title(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        task = provider.update_task("1", title="Renamed")
        assert task.title == "Renamed"
        assert provider.read_task("1").title == "Renamed"

    def test_update_body(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        provider.update_task("1", body="New body content")
        assert provider.read_task("1").body == "New body content"

    def test_update_preserves_other_sections(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        provider.update_task("1", title="Renamed")
        assert provider.read_task("2").title == "Fix parser bug"
        assert provider.read_task("3").title == "Refactor"


class TestCloseTask:
    def test_close_marks_closed(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        task = provider.close_task("1")
        assert task.state == TaskState.CLOSED
        # Persisted.
        assert provider.read_task("1").state == TaskState.CLOSED

    def test_close_strips_in_progress_label(self, config_factory) -> None:
        content = (
            "## #1 Title\n\n"
            "<!-- wade\nstate: in_progress\nlabels: in-progress, feature\n-->\n\n"
            "body\n"
        )
        provider = config_factory(content)
        provider.close_task("1")
        task = provider.read_task("1")
        names = {lbl.name for lbl in task.labels}
        assert "in-progress" not in names
        assert "feature" in names

    def test_close_unknown_raises(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        with pytest.raises(TaskNotFoundError):
            provider.close_task("999")


class TestCommentOnTask:
    def test_comment_appended_to_body(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        provider.comment_on_task("1", "Hello world")
        body = provider.read_task("1").body
        assert "Hello world" in body
        assert "### Comment" in body


# ---------------------------------------------------------------------------
# Label management
# ---------------------------------------------------------------------------


class TestAddLabel:
    def test_adds_when_missing(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        provider.add_label("3", "new-label")
        names = {lbl.name for lbl in provider.read_task("3").labels}
        assert "new-label" in names

    def test_idempotent(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        provider.add_label("1", "feature")  # Already present.
        names = [lbl.name for lbl in provider.read_task("1").labels]
        assert names.count("feature") == 1

    def test_unknown_task_is_non_fatal(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        # Should not raise.
        provider.add_label("999", "anything")


class TestRemoveLabel:
    def test_removes_existing(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        provider.remove_label("1", "feature")
        names = {lbl.name for lbl in provider.read_task("1").labels}
        assert "feature" not in names

    def test_unknown_task_is_non_fatal(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        provider.remove_label("999", "anything")


class TestEnsureLabel:
    def test_noop(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        # Should not raise — markdown labels are inline.
        from wade.models.task import Label

        provider.ensure_label(Label(name="anything"))


# ---------------------------------------------------------------------------
# Project board ops
# ---------------------------------------------------------------------------


class TestMoveToInProgress:
    def test_marks_in_progress(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        assert provider.move_to_in_progress("1") is True
        assert provider.read_task("1").state == TaskState.IN_PROGRESS

    def test_unknown_returns_false(self, config_factory) -> None:
        provider = config_factory(SAMPLE_FILE)
        assert provider.move_to_in_progress("999") is False


# ---------------------------------------------------------------------------
# Round-trip: file remains parseable after multiple operations.
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_full_lifecycle(self, config_factory) -> None:
        provider = config_factory(None)
        a = provider.create_task("Task A", "Body A", labels=["feature"])
        b = provider.create_task("Task B", "Body B")
        assert a.id == "1"
        assert b.id == "2"

        provider.add_label("1", "complexity:complex")
        provider.update_task("2", body="Updated body for B")
        provider.move_to_in_progress("1")
        provider.close_task("2")

        # Re-read the whole file via a fresh provider instance.
        fresh = config_factory.__self__ if hasattr(config_factory, "__self__") else None  # noqa
        # Same provider instance; reload from disk.
        assert provider.read_task("1").state == TaskState.IN_PROGRESS
        assert provider.read_task("2").state == TaskState.CLOSED
        assert provider.read_task("1").complexity == Complexity.COMPLEX
        assert provider.read_task("2").body == "Updated body for B"


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_registry_returns_markdown_provider(self, tmp_path: Path) -> None:
        config = ProjectConfig(
            provider=ProviderConfig(
                name=ProviderID.MARKDOWN,
                settings={"path": "ISSUES.md"},
            ),
            project_root=str(tmp_path),
        )
        provider = get_provider(config)
        assert isinstance(provider, MarkdownIssueProvider)
        assert provider._path == (tmp_path / "ISSUES.md").resolve()


# ---------------------------------------------------------------------------
# PR-review delegation to GitHub
# ---------------------------------------------------------------------------


class TestPRReviewDelegation:
    """Markdown issues live in a GitHub repo; PR-review ops delegate to gh."""

    def _build(self, tmp_path: Path) -> tuple[MarkdownIssueProvider, MagicMock]:
        gh = MagicMock()
        provider = MarkdownIssueProvider(
            ProviderConfig(name=ProviderID.MARKDOWN),
            project_root=tmp_path,
            github_provider=gh,
        )
        return provider, gh

    def test_get_pr_review_threads_delegates(self, tmp_path: Path) -> None:
        provider, gh = self._build(tmp_path)
        gh.get_pr_review_threads.return_value = ["t1", "t2"]
        assert provider.get_pr_review_threads(42) == ["t1", "t2"]
        gh.get_pr_review_threads.assert_called_once_with(42)

    def test_resolve_review_thread_delegates(self, tmp_path: Path) -> None:
        provider, gh = self._build(tmp_path)
        gh.resolve_review_thread.return_value = True
        assert provider.resolve_review_thread("THREAD_ID") is True
        gh.resolve_review_thread.assert_called_once_with("THREAD_ID")

    def test_get_pr_issue_comments_delegates(self, tmp_path: Path) -> None:
        provider, gh = self._build(tmp_path)
        gh.get_pr_issue_comments.return_value = [{"login": "u", "body": "hi"}]
        assert provider.get_pr_issue_comments(7) == [{"login": "u", "body": "hi"}]
        gh.get_pr_issue_comments.assert_called_once_with(7)

    def test_get_pr_review_status_delegates(self, tmp_path: Path) -> None:
        provider, gh = self._build(tmp_path)
        status = PRReviewStatus()
        gh.get_pr_review_status.return_value = status
        assert provider.get_pr_review_status(99) is status
        gh.get_pr_review_status.assert_called_once_with(99)

    def test_get_repo_nwo_delegates(self, tmp_path: Path) -> None:
        provider, gh = self._build(tmp_path)
        gh.get_repo_nwo.return_value = "owner/repo"
        assert provider.get_repo_nwo() == "owner/repo"

    def test_inner_github_provider_lazy_init(self, tmp_path: Path) -> None:
        # No github_provider injected — should not construct one until needed.
        provider = MarkdownIssueProvider(
            ProviderConfig(name=ProviderID.MARKDOWN),
            project_root=tmp_path,
        )
        assert provider._github is None
        # Calling _gh() should construct one lazily.
        gh = provider._gh()
        assert gh is not None
        # And cache it.
        assert provider._gh() is gh
