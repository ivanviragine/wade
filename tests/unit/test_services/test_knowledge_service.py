"""Unit tests for knowledge_service."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from wade.models.config import KnowledgeConfig
from wade.services.knowledge_service import (
    KNOWLEDGE_TEMPLATE,
    EntryRating,
    KnowledgeEntry,
    ParsedEntry,
    _canonical_project_root,
    add_tag_to_entry,
    append_knowledge,
    compute_auto_filter_threshold,
    create_rating_event,
    disable_knowledge,
    enable_knowledge,
    ensure_knowledge_file,
    find_entry_id,
    flush_staged_ratings,
    get_annotated_knowledge,
    knowledge_status,
    list_tags,
    parse_entries,
    read_knowledge,
    read_ratings,
    record_rating,
    record_rating_for_session,
    record_supersede,
    refusal_for_throwaway_write,
    remove_tag_from_entry,
    resolve_canonical_knowledge_path,
    resolve_knowledge_path,
    resolve_ratings_path,
    stage_rating_event,
    staged_ratings_path,
    validate_tag,
)
from wade.utils.knowledge_file import validate_knowledge_file


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def config() -> KnowledgeConfig:
    return KnowledgeConfig(enabled=True, path="KNOWLEDGE.md")


class TestResolveKnowledgePath:
    def test_default_path(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = resolve_knowledge_path(project_root, config)
        assert result == (project_root / "KNOWLEDGE.md").resolve()

    def test_custom_path(self, project_root: Path) -> None:
        config = KnowledgeConfig(enabled=True, path="docs/LEARNINGS.md")
        result = resolve_knowledge_path(project_root, config)
        assert result == (project_root / "docs/LEARNINGS.md").resolve()


class TestResolveKnowledgePathSecurity:
    def test_rejects_absolute_path(self, project_root: Path) -> None:
        config = KnowledgeConfig(enabled=True, path="/etc/passwd")
        with pytest.raises(ValueError, match="must be inside project root"):
            resolve_knowledge_path(project_root, config)

    def test_rejects_path_traversal(self, project_root: Path) -> None:
        config = KnowledgeConfig(enabled=True, path="../../etc/passwd")
        with pytest.raises(ValueError, match="must be inside project root"):
            resolve_knowledge_path(project_root, config)

    def test_allows_nested_path(self, project_root: Path) -> None:
        config = KnowledgeConfig(enabled=True, path="docs/LEARNINGS.md")
        result = resolve_knowledge_path(project_root, config)
        assert result == (project_root / "docs/LEARNINGS.md").resolve()


class TestResolveRatingsPath:
    def test_derives_from_knowledge_path(self) -> None:
        result = resolve_ratings_path(Path("/project/KNOWLEDGE.md"))
        assert result == Path("/project/KNOWLEDGE.ratings.jsonl")

    def test_works_with_custom_name(self) -> None:
        result = resolve_ratings_path(Path("/project/docs/LEARNINGS.md"))
        assert result == Path("/project/docs/LEARNINGS.ratings.jsonl")


class TestEnsureKnowledgeFile:
    def test_creates_file_with_template(self, project_root: Path, config: KnowledgeConfig) -> None:
        path = ensure_knowledge_file(project_root, config)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == KNOWLEDGE_TEMPLATE

    def test_does_not_overwrite_existing(self, project_root: Path, config: KnowledgeConfig) -> None:
        existing_content = "# Existing knowledge\n\nSome content.\n"
        knowledge_path = project_root / "KNOWLEDGE.md"
        knowledge_path.write_text(existing_content, encoding="utf-8")

        path = ensure_knowledge_file(project_root, config)
        assert path.read_text(encoding="utf-8") == existing_content

    def test_creates_parent_directories(self, project_root: Path) -> None:
        config = KnowledgeConfig(enabled=True, path="docs/LEARNINGS.md")
        path = ensure_knowledge_file(project_root, config)
        assert path.exists()
        assert path.parent.name == "docs"

    def test_rejects_directory_path(self, project_root: Path) -> None:
        (project_root / "somedir").mkdir()
        config = KnowledgeConfig(enabled=True, path="somedir")
        with pytest.raises(ValueError, match="points to a directory"):
            ensure_knowledge_file(project_root, config)


class TestReadKnowledge:
    def test_returns_content_when_file_exists(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        content = "# Knowledge\n\nSome content.\n"
        (project_root / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        assert read_knowledge(project_root, config) == content

    def test_returns_none_when_file_missing(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        assert read_knowledge(project_root, config) is None

    def test_does_not_create_file_when_missing(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        read_knowledge(project_root, config)
        assert not (project_root / "KNOWLEDGE.md").exists()

    def test_raises_when_path_is_directory(self, project_root: Path) -> None:
        (project_root / "somedir").mkdir()
        config = KnowledgeConfig(enabled=True, path="somedir")
        with pytest.raises(ValueError, match="points to a directory"):
            read_knowledge(project_root, config)

    def test_reads_from_custom_path(self, project_root: Path) -> None:
        config = KnowledgeConfig(enabled=True, path="docs/LEARNINGS.md")
        (project_root / "docs").mkdir()
        (project_root / "docs" / "LEARNINGS.md").write_text("# Learnings\n", encoding="utf-8")
        assert read_knowledge(project_root, config) == "# Learnings\n"


class TestAppendKnowledge:
    def test_returns_knowledge_entry(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(
            project_root=project_root,
            config=config,
            content="Some content.",
            session_type="plan",
        )
        assert isinstance(result, KnowledgeEntry)
        assert result.path == (project_root / "KNOWLEDGE.md").resolve()
        # Widened to 12 hex chars (48 bits) for #358 — cross-worktree collision safety.
        assert re.match(r"^[0-9a-f]{12}$", result.entry_id)

    def test_generates_uuid_in_heading(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(
            project_root=project_root,
            config=config,
            content="Test content.",
            session_type="implementation",
            issue_ref="42",
        )
        text = result.path.read_text(encoding="utf-8")
        assert f"## {result.entry_id} | " in text
        assert "| implementation | Issue #42" in text

    def test_appends_entry_with_issue(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(
            project_root=project_root,
            config=config,
            content="Discovered that tests need DB setup.",
            session_type="implementation",
            issue_ref="42",
        )
        text = result.path.read_text(encoding="utf-8")
        assert "## " in text
        assert "| implementation | Issue #42" in text
        assert "Discovered that tests need DB setup." in text
        assert text.endswith("---\n")

    def test_appends_entry_without_issue(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(
            project_root=project_root,
            config=config,
            content="General project pattern.",
            session_type="plan",
        )
        text = result.path.read_text(encoding="utf-8")
        assert "| plan" in text
        assert "Issue #" not in text
        assert "General project pattern." in text

    def test_creates_file_if_missing(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(
            project_root=project_root,
            config=config,
            content="First entry.",
            session_type="implementation",
            issue_ref="1",
        )
        text = result.path.read_text(encoding="utf-8")
        # Should start with the template header
        assert text.startswith("# Project Knowledge")
        # And contain the entry
        assert "First entry." in text

    def test_appends_multiple_entries(self, project_root: Path, config: KnowledgeConfig) -> None:
        append_knowledge(
            project_root=project_root,
            config=config,
            content="First learning.",
            session_type="plan",
            issue_ref="1",
        )
        append_knowledge(
            project_root=project_root,
            config=config,
            content="Second learning.",
            session_type="implementation",
            issue_ref="2",
        )
        text = (project_root / "KNOWLEDGE.md").read_text(encoding="utf-8")
        assert "First learning." in text
        assert "Second learning." in text
        assert text.count("---") >= 3  # template separator + 2 entries

    def test_strips_whitespace_from_content(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        append_knowledge(
            project_root=project_root,
            config=config,
            content="  \n  Some content with whitespace  \n  ",
            session_type="plan",
        )
        text = (project_root / "KNOWLEDGE.md").read_text(encoding="utf-8")
        assert "Some content with whitespace" in text
        # Shouldn't have leading/trailing whitespace in the content block
        lines = text.split("\n")
        content_lines = [line for line in lines if "Some content" in line]
        assert content_lines[0] == "Some content with whitespace"

    def test_unique_ids_across_entries(self, project_root: Path, config: KnowledgeConfig) -> None:
        r1 = append_knowledge(
            project_root=project_root, config=config, content="A.", session_type="plan"
        )
        r2 = append_knowledge(
            project_root=project_root, config=config, content="B.", session_type="plan"
        )
        assert r1.entry_id != r2.entry_id


class TestParseEntries:
    def test_parses_entry_with_id(self) -> None:
        text = "## a1b2c3d4 | 2026-03-24 | plan\n\nSome content.\n\n---\n"
        entries = parse_entries(text)
        assert len(entries) == 1
        assert entries[0].entry_id == "a1b2c3d4"
        assert entries[0].date == "2026-03-24"
        assert entries[0].heading_rest == "plan"
        assert entries[0].content == "Some content."

    def test_parses_entry_without_id(self) -> None:
        text = "## 2026-03-24 | plan\n\nOld content.\n\n---\n"
        entries = parse_entries(text)
        assert len(entries) == 1
        assert entries[0].entry_id is None
        assert entries[0].date == "2026-03-24"
        assert entries[0].content == "Old content."

    def test_parses_multiple_entries(self) -> None:
        text = (
            "# Header\n\n---\n\n"
            "## a1b2c3d4 | 2026-03-24 | plan\n\nFirst.\n\n---\n\n"
            "## b2c3d4e5 | 2026-03-25 | implementation | Issue #42\n\nSecond.\n\n---\n"
        )
        entries = parse_entries(text)
        assert len(entries) == 2
        assert entries[0].entry_id == "a1b2c3d4"
        assert entries[1].entry_id == "b2c3d4e5"

    def test_skips_template_header(self) -> None:
        entries = parse_entries(KNOWLEDGE_TEMPLATE)
        assert len(entries) == 0

    def test_parses_entry_with_score_annotation(self) -> None:
        text = "## a1b2c3d4 | 2026-03-24 | plan [+3/-1]\n\nContent.\n\n---\n"
        entries = parse_entries(text)
        assert len(entries) == 1
        assert entries[0].entry_id == "a1b2c3d4"
        assert entries[0].heading_rest == "plan"

    def test_parses_entry_with_descriptive_id_hyphens(self) -> None:
        text = "## config-sync-tool | 2026-03-24 | plan\n\nContent with custom ID.\n\n---\n"
        entries = parse_entries(text)
        assert len(entries) == 1
        assert entries[0].entry_id == "config-sync-tool"
        assert entries[0].date == "2026-03-24"
        assert entries[0].heading_rest == "plan"
        assert entries[0].content == "Content with custom ID."

    def test_parses_entry_with_descriptive_id_underscores(self) -> None:
        text = "## my_entry_name | 2026-03-24 | implementation | Issue #42\n\nContent.\n\n---\n"
        entries = parse_entries(text)
        assert len(entries) == 1
        assert entries[0].entry_id == "my_entry_name"
        assert entries[0].heading_rest == "implementation | Issue #42"

    def test_parses_entry_with_descriptive_id_mixed_alphanumeric(self) -> None:
        text = "## entry123abc | 2026-03-24 | plan\n\nMixed alphanumeric ID.\n\n---\n"
        entries = parse_entries(text)
        assert len(entries) == 1
        assert entries[0].entry_id == "entry123abc"

    def test_handles_empty_text(self) -> None:
        assert parse_entries("") == []

    def test_mixed_id_and_no_id_entries(self) -> None:
        text = (
            "## 2026-03-20 | plan\n\nOld.\n\n---\n\n"
            "## a1b2c3d4 | 2026-03-24 | implementation\n\nNew.\n\n---\n"
        )
        entries = parse_entries(text)
        assert len(entries) == 2
        assert entries[0].entry_id is None
        assert entries[1].entry_id == "a1b2c3d4"


class TestFindEntryId:
    def test_finds_existing_id(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(
            project_root=project_root, config=config, content="Test.", session_type="plan"
        )
        knowledge_path = resolve_knowledge_path(project_root, config)
        assert find_entry_id(knowledge_path, result.entry_id) is True

    def test_returns_false_for_missing_id(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        append_knowledge(
            project_root=project_root, config=config, content="Test.", session_type="plan"
        )
        knowledge_path = resolve_knowledge_path(project_root, config)
        assert find_entry_id(knowledge_path, "nonexist") is False

    def test_returns_false_when_file_missing(self, tmp_path: Path) -> None:
        assert find_entry_id(tmp_path / "KNOWLEDGE.md", "abcd1234") is False


class TestReadRatings:
    def test_returns_empty_when_file_missing(self, tmp_path: Path) -> None:
        assert read_ratings(tmp_path / "KNOWLEDGE.ratings.jsonl") == {}

    def test_returns_empty_for_empty_file(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        ratings_path.write_text("", encoding="utf-8")
        assert read_ratings(ratings_path) == {}

    def test_folds_jsonl_vote_log(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        ratings_path.write_text(
            '{"dir": "up", "id": "a1b2c3d4", "ts": "t"}\n'
            '{"dir": "up", "id": "a1b2c3d4", "ts": "t"}\n'
            '{"dir": "up", "id": "a1b2c3d4", "ts": "t"}\n'
            '{"dir": "down", "id": "a1b2c3d4", "ts": "t"}\n',
            encoding="utf-8",
        )
        result = read_ratings(ratings_path)
        assert result["a1b2c3d4"].up == 3
        assert result["a1b2c3d4"].down == 1

    def test_skips_malformed_lines_defensively(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        ratings_path.write_text(
            '{"dir": "up", "id": "a1b2c3d4", "ts": "t"}\n'
            "not json at all\n"
            '{"dir": "down", "id": "a1b2c3d4", "ts": "t"}\n',
            encoding="utf-8",
        )
        result = read_ratings(ratings_path)
        assert result["a1b2c3d4"].up == 1
        assert result["a1b2c3d4"].down == 1

    def test_folds_legacy_yaml_in_memory_without_writing(self, tmp_path: Path) -> None:
        # A pre-#358 counter YAML is folded to the same scores on read, and the read
        # must NOT materialize the .jsonl (reads stay pure — no dirty main).
        jsonl_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        legacy_path = tmp_path / "KNOWLEDGE.ratings.yml"
        legacy_path.write_text(yaml.safe_dump({"a1b2c3d4": {"up": 3, "down": 1}}), encoding="utf-8")
        result = read_ratings(jsonl_path)
        assert result["a1b2c3d4"].up == 3
        assert result["a1b2c3d4"].down == 1
        assert not jsonl_path.exists()  # read did not write the .jsonl
        assert legacy_path.exists()  # legacy left untouched


class TestRecordRating:
    def test_creates_file_and_records_up(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        record_rating(ratings_path, "a1b2c3d4", "up")
        data = read_ratings(ratings_path)
        assert data["a1b2c3d4"].up == 1
        assert data["a1b2c3d4"].down == 0

    def test_records_down(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        record_rating(ratings_path, "a1b2c3d4", "down")
        data = read_ratings(ratings_path)
        assert data["a1b2c3d4"].up == 0
        assert data["a1b2c3d4"].down == 1

    def test_increments_existing_count(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "a1b2c3d4", "down")
        data = read_ratings(ratings_path)
        assert data["a1b2c3d4"].up == 2
        assert data["a1b2c3d4"].down == 1

    def test_records_stale(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        record_rating(ratings_path, "a1b2c3d4", "stale")
        data = read_ratings(ratings_path)
        assert data["a1b2c3d4"].stale == 1
        assert data["a1b2c3d4"].up == 0
        assert data["a1b2c3d4"].down == 0

    def test_stale_does_not_affect_net_score(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "a1b2c3d4", "stale")
        record_rating(ratings_path, "a1b2c3d4", "stale")
        data = read_ratings(ratings_path)
        assert data["a1b2c3d4"].up == 1
        assert data["a1b2c3d4"].down == 0
        assert data["a1b2c3d4"].stale == 2
        assert data["a1b2c3d4"].up - data["a1b2c3d4"].down == 1  # net score unaffected

    def test_stale_increments_independently(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        record_rating(ratings_path, "a1b2c3d4", "stale")
        record_rating(ratings_path, "a1b2c3d4", "stale")
        data = read_ratings(ratings_path)
        assert data["a1b2c3d4"].stale == 2

    def test_rejects_invalid_direction(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        with pytest.raises(ValueError, match="must be 'up', 'down', or 'stale'"):
            record_rating(ratings_path, "a1b2c3d4", "sideways")

    def test_multiple_entries(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "f5e6d7c8", "down")
        data = read_ratings(ratings_path)
        assert data["a1b2c3d4"].up == 1
        assert data["f5e6d7c8"].down == 1


class TestRecordSupersede:
    def test_records_supersede_link(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        record_supersede(ratings_path, "old12345", "new67890")
        data = read_ratings(ratings_path)
        assert data["old12345"].superseded_by == "new67890"

    def test_preserves_existing_ratings(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.jsonl"
        record_rating(ratings_path, "old12345", "down")
        record_rating(ratings_path, "old12345", "down")
        record_supersede(ratings_path, "old12345", "new67890")
        data = read_ratings(ratings_path)
        assert data["old12345"].down == 2
        assert data["old12345"].superseded_by == "new67890"


class TestGetAnnotatedKnowledge:
    def _make_knowledge_file(self, project_root: Path) -> None:
        content = (
            KNOWLEDGE_TEMPLATE
            + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nUseful content.\n\n---\n"
            + "\n## f5e6d7c8 | 2026-03-20 | implementation\n\nOutdated content.\n\n---\n"
        )
        (project_root / "KNOWLEDGE.md").write_text(content, encoding="utf-8")

    def test_returns_none_when_file_missing(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        result = get_annotated_knowledge(project_root, config)
        assert result.content is None
        assert result.entries_count == 0

    def test_annotates_id_backed_entries_with_zero_scores(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        result = get_annotated_knowledge(project_root, config)
        assert result.content is not None
        assert result.content.count("[+0/-0]") == 2
        assert "Useful content." in result.content
        assert "Outdated content." in result.content

    def test_annotates_heading_with_scores(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        ratings_path = resolve_ratings_path(project_root / "KNOWLEDGE.md")
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "f5e6d7c8", "down")

        result = get_annotated_knowledge(project_root, config)
        assert result.content is not None
        assert "[+3/-0]" in result.content
        assert "[+0/-1]" in result.content

    def test_min_score_filters_entries(self, project_root: Path, config: KnowledgeConfig) -> None:
        self._make_knowledge_file(project_root)
        ratings_path = resolve_ratings_path(project_root / "KNOWLEDGE.md")
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "f5e6d7c8", "down")
        record_rating(ratings_path, "f5e6d7c8", "down")

        result = get_annotated_knowledge(project_root, config, min_score=0)
        assert result.content is not None
        assert "Useful content." in result.content
        assert "Outdated content." not in result.content

    def test_min_score_zero_includes_unrated(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        # No ratings at all — implicit score 0
        result = get_annotated_knowledge(project_root, config, min_score=0)
        assert result.content is not None
        assert "Useful content." in result.content
        assert "Outdated content." in result.content

    def test_min_score_one_excludes_unrated(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        result = get_annotated_knowledge(project_root, config, min_score=1)
        assert result.content is not None
        assert "Useful content." not in result.content
        assert "Outdated content." not in result.content

    def test_preserves_header(self, project_root: Path, config: KnowledgeConfig) -> None:
        self._make_knowledge_file(project_root)
        result = get_annotated_knowledge(project_root, config)
        assert result.content is not None
        assert "# Project Knowledge" in result.content

    def test_no_annotation_for_id_less_entries(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## 2026-03-24 | plan\n\nOld entry.\n\n---\n"
        (project_root / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        result = get_annotated_knowledge(project_root, config)
        assert result.content is not None
        assert "Old entry." in result.content
        assert "[+" not in result.content

    def test_stale_annotation_shown_when_positive(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        ratings_path = resolve_ratings_path(project_root / "KNOWLEDGE.md")
        record_rating(ratings_path, "a1b2c3d4", "stale")

        result = get_annotated_knowledge(project_root, config, no_filter=True)
        assert result.content is not None
        assert "[+0/-0/stale:1]" in result.content

    def test_stale_annotation_not_shown_when_zero(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        result = get_annotated_knowledge(project_root, config, no_filter=True)
        assert result.content is not None
        assert "stale:" not in result.content
        assert "[+0/-0]" in result.content

    def test_stale_filter_hides_entries_at_threshold(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        ratings_path = resolve_ratings_path(project_root / "KNOWLEDGE.md")
        record_rating(ratings_path, "a1b2c3d4", "stale")
        record_rating(ratings_path, "a1b2c3d4", "stale")  # stale=2 → hidden

        result = get_annotated_knowledge(project_root, config)
        assert result.content is not None
        assert "Useful content." not in result.content
        assert "Outdated content." in result.content  # unaffected entry still shown

    def test_stale_filter_shows_entries_below_threshold(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        ratings_path = resolve_ratings_path(project_root / "KNOWLEDGE.md")
        record_rating(ratings_path, "a1b2c3d4", "stale")  # stale=1 → not hidden

        result = get_annotated_knowledge(project_root, config)
        assert result.content is not None
        assert "Useful content." in result.content

    def test_no_filter_bypasses_stale_threshold(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        ratings_path = resolve_ratings_path(project_root / "KNOWLEDGE.md")
        for _ in range(5):
            record_rating(ratings_path, "a1b2c3d4", "stale")

        result = get_annotated_knowledge(project_root, config, no_filter=True)
        assert result.content is not None
        assert "Useful content." in result.content

    def test_stale_filter_with_min_score_also_applies(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        ratings_path = resolve_ratings_path(project_root / "KNOWLEDGE.md")
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "a1b2c3d4", "stale")
        record_rating(ratings_path, "a1b2c3d4", "stale")  # stale=2 → hidden even with min_score

        result = get_annotated_knowledge(project_root, config, min_score=0)
        assert result.content is not None
        assert "Useful content." not in result.content


class TestBackwardCompatibility:
    def test_old_entries_without_ids_returned_by_get(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        content = (
            KNOWLEDGE_TEMPLATE
            + "\n## 2026-03-20 | plan\n\nOld content without ID.\n\n---\n"
            + "\n## a1b2c3d4 | 2026-03-24 | implementation\n\nNew content with ID.\n\n---\n"
        )
        (project_root / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        result = get_annotated_knowledge(project_root, config)
        assert result.content is not None
        assert "Old content without ID." in result.content
        assert "New content with ID." in result.content

    def test_old_entries_cannot_be_rated(self, project_root: Path, config: KnowledgeConfig) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## 2026-03-20 | plan\n\nOld content.\n\n---\n"
        (project_root / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        entries = parse_entries(content)
        assert len(entries) == 1
        assert entries[0].entry_id is None

    def test_old_entries_mixed_with_new(self, project_root: Path, config: KnowledgeConfig) -> None:
        # Old entry, then new entry via append_knowledge
        old_content = KNOWLEDGE_TEMPLATE + "\n## 2026-03-20 | plan\n\nOld.\n\n---\n"
        (project_root / "KNOWLEDGE.md").write_text(old_content, encoding="utf-8")
        _ = append_knowledge(
            project_root=project_root,
            config=config,
            content="New with ID.",
            session_type="implementation",
        )
        entries = parse_entries((project_root / "KNOWLEDGE.md").read_text(encoding="utf-8"))
        assert len(entries) == 2
        assert entries[0].entry_id is None


class TestEnableKnowledge:
    def test_enables_knowledge_and_creates_file(self, project_root: Path) -> None:

        # Create a .wade.yml file first
        config_path = project_root / ".wade.yml"
        config_path.write_text("version: 2\n", encoding="utf-8")

        enable_knowledge(project_root)

        # Check that config was updated
        config_content = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config_content["knowledge"]["enabled"] is True
        assert config_content["knowledge"]["path"] == "KNOWLEDGE.md"

        # Check that knowledge file was created
        knowledge_path = project_root / "KNOWLEDGE.md"
        assert knowledge_path.exists()
        assert knowledge_path.read_text(encoding="utf-8") == KNOWLEDGE_TEMPLATE

    def test_enables_knowledge_with_custom_path(self, project_root: Path) -> None:

        config_path = project_root / ".wade.yml"
        config_path.write_text("version: 2\n", encoding="utf-8")

        enable_knowledge(project_root, path="docs/LEARNINGS.md")

        config_content = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config_content["knowledge"]["enabled"] is True
        assert config_content["knowledge"]["path"] == "docs/LEARNINGS.md"

        knowledge_path = project_root / "docs" / "LEARNINGS.md"
        assert knowledge_path.exists()

    def test_rejects_absolute_path(self, project_root: Path) -> None:

        config_path = project_root / ".wade.yml"
        config_path.write_text("version: 2\n", encoding="utf-8")

        with pytest.raises(ValueError, match="must be inside project root"):
            enable_knowledge(project_root, path="/etc/passwd")

    def test_rejects_path_traversal(self, project_root: Path) -> None:

        config_path = project_root / ".wade.yml"
        config_path.write_text("version: 2\n", encoding="utf-8")

        with pytest.raises(ValueError, match="must be inside project root"):
            enable_knowledge(project_root, path="../../etc/passwd")

    def test_fails_when_no_config_exists(self, project_root: Path) -> None:

        with pytest.raises(FileNotFoundError, match=r"\.wade\.yml not found"):
            enable_knowledge(project_root)

    def test_preserves_existing_config(self, project_root: Path) -> None:

        config_path = project_root / ".wade.yml"
        original_config = """version: 2
project:
  main_branch: main
ai:
  default_tool: claude
"""
        config_path.write_text(original_config, encoding="utf-8")

        enable_knowledge(project_root)

        config_content = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config_content["version"] == 2
        assert config_content["project"]["main_branch"] == "main"
        assert config_content["knowledge"]["enabled"] is True

    def test_overwrites_disabled_knowledge(self, project_root: Path) -> None:

        config_path = project_root / ".wade.yml"
        config_path.write_text("version: 2\nknowledge:\n  enabled: false\n", encoding="utf-8")

        enable_knowledge(project_root)

        config_content = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config_content["knowledge"]["enabled"] is True


class TestDisableKnowledge:
    def test_disables_knowledge(self, project_root: Path) -> None:

        config_path = project_root / ".wade.yml"
        config_path.write_text(
            "version: 2\nknowledge:\n  enabled: true\n  path: KNOWLEDGE.md\n",
            encoding="utf-8",
        )
        knowledge_path = project_root / "KNOWLEDGE.md"
        knowledge_path.write_text(KNOWLEDGE_TEMPLATE, encoding="utf-8")

        disable_knowledge(project_root)

        # Check that config was updated
        config_content = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config_content["knowledge"]["enabled"] is False

        # Check that knowledge file still exists (not deleted)
        assert knowledge_path.exists()

    def test_fails_when_no_config_exists(self, project_root: Path) -> None:

        with pytest.raises(FileNotFoundError, match=r"\.wade\.yml not found"):
            disable_knowledge(project_root)

    def test_preserves_existing_config(self, project_root: Path) -> None:

        config_path = project_root / ".wade.yml"
        original_config = """version: 2
project:
  main_branch: main
ai:
  default_tool: claude
knowledge:
  enabled: true
  path: KNOWLEDGE.md
"""
        config_path.write_text(original_config, encoding="utf-8")

        disable_knowledge(project_root)

        config_content = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config_content["version"] == 2
        assert config_content["project"]["main_branch"] == "main"
        assert config_content["knowledge"]["enabled"] is False
        assert config_content["knowledge"]["path"] == "KNOWLEDGE.md"

    def test_idempotent_when_already_disabled(self, project_root: Path) -> None:

        config_path = project_root / ".wade.yml"
        config_path.write_text("version: 2\nknowledge:\n  enabled: false\n", encoding="utf-8")

        disable_knowledge(project_root)

        config_content = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config_content["knowledge"]["enabled"] is False


# --- Tag validation ---


class TestValidateTag:
    def test_valid_simple(self) -> None:
        assert validate_tag("git") is None

    def test_valid_kebab(self) -> None:
        assert validate_tag("worktree-safety") is None

    def test_valid_with_numbers(self) -> None:
        assert validate_tag("python3") is None

    def test_empty(self) -> None:
        assert validate_tag("") is not None

    def test_too_long(self) -> None:
        assert validate_tag("a" * 31) is not None

    def test_max_length_ok(self) -> None:
        assert validate_tag("a" * 30) is None

    def test_uppercase_rejected(self) -> None:
        assert validate_tag("Git") is not None

    def test_spaces_rejected(self) -> None:
        assert validate_tag("my tag") is not None

    def test_underscores_rejected(self) -> None:
        assert validate_tag("my_tag") is not None

    def test_leading_hyphen_rejected(self) -> None:
        assert validate_tag("-leading") is not None

    def test_trailing_hyphen_rejected(self) -> None:
        assert validate_tag("trailing-") is not None

    def test_double_hyphen_rejected(self) -> None:
        assert validate_tag("double--hyphen") is not None


# --- Tag parsing from headings ---


class TestTagParsing:
    def test_no_tags(self) -> None:
        text = "## abc12345 | 2026-03-24 | plan\n\nSome content\n\n---\n"
        entries = parse_entries(text)
        assert len(entries) == 1
        assert entries[0].tags == []

    def test_tags_no_issue(self) -> None:
        text = "## abc12345 | 2026-03-24 | plan | tags: git, worktree\n\nContent\n\n---\n"
        entries = parse_entries(text)
        assert entries[0].tags == ["git", "worktree"]

    def test_tags_and_issue(self) -> None:
        text = (
            "## abc12345 | 2026-03-24 | plan | tags: git, worktree | Issue #7\n\nContent\n\n---\n"
        )
        entries = parse_entries(text)
        assert entries[0].tags == ["git", "worktree"]

    def test_issue_no_tags(self) -> None:
        text = "## abc12345 | 2026-03-24 | plan | Issue #7\n\nContent\n\n---\n"
        entries = parse_entries(text)
        assert entries[0].tags == []

    def test_single_tag(self) -> None:
        text = "## abc12345 | 2026-03-24 | plan | tags: git\n\nContent\n\n---\n"
        entries = parse_entries(text)
        assert entries[0].tags == ["git"]

    def test_old_entries_no_tags(self) -> None:
        text = "## 2026-03-24 | plan\n\nOld content\n\n---\n"
        entries = parse_entries(text)
        assert entries[0].tags == []


# --- Append with tags ---


class TestAppendWithTags:
    def test_append_with_tags(self, project_root: Path, config: KnowledgeConfig) -> None:
        append_knowledge(project_root, config, "content", "plan", tags=["git", "worktree"])
        text = (project_root / "KNOWLEDGE.md").read_text(encoding="utf-8")
        entries = parse_entries(text)
        assert len(entries) == 1
        assert entries[0].tags == ["git", "worktree"]
        assert "tags: git, worktree" in entries[0].raw

    def test_append_with_tags_and_issue(self, project_root: Path, config: KnowledgeConfig) -> None:
        append_knowledge(project_root, config, "content", "plan", issue_ref="42", tags=["git"])
        text = (project_root / "KNOWLEDGE.md").read_text(encoding="utf-8")
        entries = parse_entries(text)
        assert entries[0].tags == ["git"]
        assert "tags: git" in entries[0].raw
        assert "Issue #42" in entries[0].raw

    def test_append_without_tags(self, project_root: Path, config: KnowledgeConfig) -> None:
        append_knowledge(project_root, config, "content", "plan")
        text = (project_root / "KNOWLEDGE.md").read_text(encoding="utf-8")
        entries = parse_entries(text)
        assert entries[0].tags == []
        assert "tags:" not in entries[0].raw

    def test_append_rejects_invalid_tags(self, project_root: Path, config: KnowledgeConfig) -> None:
        with pytest.raises(ValueError, match="kebab-case"):
            append_knowledge(project_root, config, "content", "plan", tags=["Invalid"])


# --- Tag CRUD operations ---


class TestAddTagToEntry:
    def test_add_tag(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(project_root, config, "content", "plan")
        kpath = project_root / "KNOWLEDGE.md"
        add_tag_to_entry(kpath, result.entry_id, "git")
        entries = parse_entries(kpath.read_text(encoding="utf-8"))
        assert "git" in entries[0].tags

    def test_add_second_tag(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(project_root, config, "content", "plan", tags=["git"])
        kpath = project_root / "KNOWLEDGE.md"
        add_tag_to_entry(kpath, result.entry_id, "worktree")
        entries = parse_entries(kpath.read_text(encoding="utf-8"))
        assert set(entries[0].tags) == {"git", "worktree"}

    def test_add_duplicate_tag_is_noop(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(project_root, config, "content", "plan", tags=["git"])
        kpath = project_root / "KNOWLEDGE.md"
        add_tag_to_entry(kpath, result.entry_id, "git")
        entries = parse_entries(kpath.read_text(encoding="utf-8"))
        assert entries[0].tags == ["git"]

    def test_add_tag_invalid_entry(self, project_root: Path, config: KnowledgeConfig) -> None:
        append_knowledge(project_root, config, "content", "plan")
        kpath = project_root / "KNOWLEDGE.md"
        with pytest.raises(ValueError, match="not found"):
            add_tag_to_entry(kpath, "nonexist", "git")

    def test_add_invalid_tag(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(project_root, config, "content", "plan")
        kpath = project_root / "KNOWLEDGE.md"
        with pytest.raises(ValueError, match="kebab-case"):
            add_tag_to_entry(kpath, result.entry_id, "Invalid")

    def test_add_tag_preserves_issue(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(project_root, config, "content", "plan", issue_ref="42")
        kpath = project_root / "KNOWLEDGE.md"
        add_tag_to_entry(kpath, result.entry_id, "git")
        entries = parse_entries(kpath.read_text(encoding="utf-8"))
        assert "git" in entries[0].tags
        assert "Issue #42" in entries[0].heading_rest


class TestRemoveTagFromEntry:
    def test_remove_tag(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(project_root, config, "content", "plan", tags=["git", "worktree"])
        kpath = project_root / "KNOWLEDGE.md"
        remove_tag_from_entry(kpath, result.entry_id, "git")
        entries = parse_entries(kpath.read_text(encoding="utf-8"))
        assert entries[0].tags == ["worktree"]

    def test_remove_last_tag(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(project_root, config, "content", "plan", tags=["git"])
        kpath = project_root / "KNOWLEDGE.md"
        remove_tag_from_entry(kpath, result.entry_id, "git")
        entries = parse_entries(kpath.read_text(encoding="utf-8"))
        assert entries[0].tags == []
        assert "tags:" not in entries[0].raw

    def test_remove_nonexistent_tag(self, project_root: Path, config: KnowledgeConfig) -> None:
        result = append_knowledge(project_root, config, "content", "plan", tags=["git"])
        kpath = project_root / "KNOWLEDGE.md"
        with pytest.raises(ValueError, match="not found"):
            remove_tag_from_entry(kpath, result.entry_id, "worktree")

    def test_remove_from_nonexistent_entry(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        append_knowledge(project_root, config, "content", "plan")
        kpath = project_root / "KNOWLEDGE.md"
        with pytest.raises(ValueError, match="not found"):
            remove_tag_from_entry(kpath, "nonexist", "git")


class TestListTags:
    def test_list_all_tags(self, project_root: Path, config: KnowledgeConfig) -> None:
        append_knowledge(project_root, config, "c1", "plan", tags=["git", "worktree"])
        append_knowledge(project_root, config, "c2", "plan", tags=["testing", "git"])
        kpath = project_root / "KNOWLEDGE.md"
        result = list_tags(kpath)
        assert result == ["git", "testing", "worktree"]

    def test_list_entry_tags(self, project_root: Path, config: KnowledgeConfig) -> None:
        r1 = append_knowledge(project_root, config, "c1", "plan", tags=["git", "worktree"])
        kpath = project_root / "KNOWLEDGE.md"
        result = list_tags(kpath, entry_id=r1.entry_id)
        assert result == ["git", "worktree"]

    def test_list_no_tags(self, project_root: Path, config: KnowledgeConfig) -> None:
        append_knowledge(project_root, config, "c1", "plan")
        kpath = project_root / "KNOWLEDGE.md"
        result = list_tags(kpath)
        assert result == []

    def test_list_nonexistent_entry(self, project_root: Path, config: KnowledgeConfig) -> None:
        append_knowledge(project_root, config, "c1", "plan")
        kpath = project_root / "KNOWLEDGE.md"
        with pytest.raises(ValueError, match="not found"):
            list_tags(kpath, entry_id="nonexist")

    def test_list_missing_file(self, project_root: Path) -> None:
        result = list_tags(project_root / "KNOWLEDGE.md")
        assert result == []


# --- Statistical auto-filter ---


class TestComputeAutoFilterThreshold:
    def test_no_qualifying_entries(self) -> None:
        entries = [
            _make_parsed_entry("e1"),
            _make_parsed_entry("e2"),
        ]
        ratings = {
            "e1": EntryRating(up=1, down=0),  # 1 vote < 5
            "e2": EntryRating(up=2, down=1),  # 3 votes < 5
        }
        assert compute_auto_filter_threshold(entries, ratings) is None

    def test_fewer_than_3_qualifying(self) -> None:
        entries = [_make_parsed_entry("e1"), _make_parsed_entry("e2")]
        ratings = {
            "e1": EntryRating(up=5, down=0),  # 5 votes, qualifies
            "e2": EntryRating(up=3, down=3),  # 6 votes, qualifies
        }
        # Only 2 qualifying entries — not enough
        assert compute_auto_filter_threshold(entries, ratings) is None

    def test_exactly_3_qualifying(self) -> None:
        entries = [_make_parsed_entry(f"e{i}") for i in range(3)]
        ratings = {
            "e0": EntryRating(up=10, down=0),  # net=10
            "e1": EntryRating(up=8, down=2),  # net=6
            "e2": EntryRating(up=2, down=5),  # net=-3
        }
        threshold = compute_auto_filter_threshold(entries, ratings)
        assert threshold is not None
        # Threshold should allow filtering of very negative entries
        assert threshold <= 10  # sanity check

    def test_all_same_score(self) -> None:
        entries = [_make_parsed_entry(f"e{i}") for i in range(5)]
        ratings = {f"e{i}": EntryRating(up=5, down=0) for i in range(5)}
        threshold = compute_auto_filter_threshold(entries, ratings)
        assert threshold is not None
        # All scores are 5, stdev=0, so threshold = max(p10=5, 5 - 0) = 5
        assert threshold == 5.0

    def test_entries_without_ids_skipped(self) -> None:
        entries = [
            _make_parsed_entry(None),
            _make_parsed_entry("e1"),
            _make_parsed_entry("e2"),
        ]
        ratings = {
            "e1": EntryRating(up=5, down=0),
            "e2": EntryRating(up=3, down=3),
        }
        # Only 2 qualifying entries (ID-less one skipped)
        assert compute_auto_filter_threshold(entries, ratings) is None

    def test_entries_with_few_votes_not_counted(self) -> None:
        entries = [_make_parsed_entry(f"e{i}") for i in range(5)]
        ratings = {
            "e0": EntryRating(up=10, down=0),  # 10 votes
            "e1": EntryRating(up=8, down=2),  # 10 votes
            "e2": EntryRating(up=3, down=5),  # 8 votes
            "e3": EntryRating(up=1, down=0),  # 1 vote — not counted
            "e4": EntryRating(up=2, down=1),  # 3 votes — not counted
        }
        threshold = compute_auto_filter_threshold(entries, ratings)
        assert threshold is not None


# --- Search + tag filtering in get_annotated_knowledge ---


class TestGetAnnotatedKnowledgeSearch:
    def test_search_filters_entries(self, project_root: Path, config: KnowledgeConfig) -> None:
        append_knowledge(project_root, config, "Git worktree is useful", "plan")
        append_knowledge(project_root, config, "Docker is also useful", "plan")
        result = get_annotated_knowledge(
            project_root, config, search_query="worktree", no_filter=True
        )
        assert result.content is not None
        assert "worktree" in result.content
        assert "Docker" not in result.content

    def test_tag_filters_entries(self, project_root: Path, config: KnowledgeConfig) -> None:
        append_knowledge(project_root, config, "Git stuff", "plan", tags=["git"])
        append_knowledge(project_root, config, "Docker stuff", "plan", tags=["docker"])
        result = get_annotated_knowledge(project_root, config, filter_tags=["git"], no_filter=True)
        assert result.content is not None
        assert "Git stuff" in result.content
        assert "Docker stuff" not in result.content

    def test_search_and_tag_or_semantics(self, project_root: Path, config: KnowledgeConfig) -> None:
        append_knowledge(project_root, config, "Git worktree tips", "plan", tags=["git"])
        append_knowledge(project_root, config, "Testing patterns", "plan", tags=["testing"])
        append_knowledge(project_root, config, "Unrelated stuff", "plan")
        result = get_annotated_knowledge(
            project_root, config, search_query="worktree", filter_tags=["testing"], no_filter=True
        )
        assert result is not None
        assert "worktree tips" in result.content  # matches search
        assert "Testing patterns" in result.content  # matches tag
        assert "Unrelated stuff" not in result.content  # matches neither

    def test_search_on_empty_knowledge_file(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        # Knowledge file with only template header (no entries)
        ensure_knowledge_file(project_root, config)
        result = get_annotated_knowledge(project_root, config, search_query="foo", no_filter=True)
        assert result.content is not None
        assert result.entries_count == 0
        # Should return template header, but parse_entries should find no entries
        entries = parse_entries(result.content)
        assert len(entries) == 0

    def test_tag_filter_on_empty_knowledge_file(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        # Knowledge file with only template header (no entries)
        ensure_knowledge_file(project_root, config)
        result = get_annotated_knowledge(project_root, config, filter_tags=["foo"], no_filter=True)
        assert result.content is not None
        assert result.entries_count == 0
        # Should return template header, but parse_entries should find no entries
        entries = parse_entries(result.content)
        assert len(entries) == 0

    def test_no_filter_on_empty_knowledge_file(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        # Knowledge file with only template header (no entries)
        ensure_knowledge_file(project_root, config)
        result = get_annotated_knowledge(project_root, config)
        assert result.content is not None
        assert result.entries_count == 0
        # Should return the exact template since no filters were applied
        assert result.content == KNOWLEDGE_TEMPLATE

    def test_no_filter_shows_everything(self, project_root: Path, config: KnowledgeConfig) -> None:
        r = append_knowledge(project_root, config, "content", "plan")
        kpath = resolve_knowledge_path(project_root, config)
        ratings_path = resolve_ratings_path(kpath)
        # Give it lots of downvotes
        for _ in range(10):
            record_rating(ratings_path, r.entry_id, "down")
        result = get_annotated_knowledge(project_root, config, no_filter=True)
        assert result.content is not None
        assert "content" in result.content


class TestGetAnnotatedKnowledgeAutoFilter:
    def test_auto_filter_prunes_low_rated(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        # Need >= 11 qualifying entries so p10_idx > 0 (p10 is 2nd smallest, not min)
        kpath = resolve_knowledge_path(project_root, config)
        ensure_knowledge_file(project_root, config)
        ratings_path = resolve_ratings_path(kpath)

        # 10 good entries with 5 upvotes each (net=5, total=5, qualifies)
        good_ids = []
        for i in range(10):
            r = append_knowledge(project_root, config, f"Good entry {i}", "plan")
            good_ids.append(r.entry_id)
        # 1 bad entry with 5 downvotes (net=-5, total=5, qualifies)
        bad = append_knowledge(project_root, config, "Bad entry", "plan")

        for eid in good_ids:
            for _ in range(5):
                record_rating(ratings_path, eid, "up")
        for _ in range(5):
            record_rating(ratings_path, bad.entry_id, "down")

        # With 11 qualifying entries, p10 = sorted[1] = 5
        # Bad entry (net=-5) < 5 → filtered out
        result = get_annotated_knowledge(project_root, config)
        assert result.content is not None
        assert "Good entry 0" in result.content
        assert "Bad entry" not in result.content

    def test_auto_filter_passes_low_vote_entries(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        kpath = resolve_knowledge_path(project_root, config)
        ensure_knowledge_file(project_root, config)
        ratings_path = resolve_ratings_path(kpath)

        r1 = append_knowledge(project_root, config, "Rated entry", "plan")
        r2 = append_knowledge(project_root, config, "Rated entry 2", "plan")
        r3 = append_knowledge(project_root, config, "Rated entry 3", "plan")
        append_knowledge(project_root, config, "New unrated entry", "plan")

        for _ in range(10):
            record_rating(ratings_path, r1.entry_id, "up")
        for _ in range(8):
            record_rating(ratings_path, r2.entry_id, "up")
        for _ in range(6):
            record_rating(ratings_path, r3.entry_id, "up")
        # r4 has no votes — should always pass through

        result = get_annotated_knowledge(project_root, config)
        assert result.content is not None
        assert "New unrated entry" in result.content

    def test_min_score_overrides_auto_filter(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        kpath = resolve_knowledge_path(project_root, config)
        ensure_knowledge_file(project_root, config)
        ratings_path = resolve_ratings_path(kpath)

        r1 = append_knowledge(project_root, config, "High entry", "plan")
        r2 = append_knowledge(project_root, config, "Low entry", "plan")

        for _ in range(10):
            record_rating(ratings_path, r1.entry_id, "up")
        record_rating(ratings_path, r2.entry_id, "up")

        # min_score=5 is a hard cutoff
        result = get_annotated_knowledge(project_root, config, min_score=5)
        assert result.content is not None
        assert "High entry" in result.content
        assert "Low entry" not in result.content


def _make_parsed_entry(entry_id: str | None, tags: list[str] | None = None) -> ParsedEntry:
    """Helper to create a ParsedEntry for testing."""
    if entry_id:
        raw = f"## {entry_id} | 2026-01-01 | plan\n\ncontent\n\n---\n"
    else:
        raw = "## 2026-01-01 | plan\n\ncontent\n\n---\n"
    return ParsedEntry(
        entry_id=entry_id,
        date="2026-01-01",
        heading_rest="plan",
        tags=tags or [],
        content="content",
        raw=raw,
    )


class TestCanonicalProjectRoot:
    def test_returns_same_path_when_not_a_worktree(self, tmp_path: Path) -> None:
        with patch(
            "wade.git.repo.get_main_worktree_path",
            return_value=None,
        ):
            result = _canonical_project_root(tmp_path)
            assert result == tmp_path

    def test_redirects_to_main_worktree_path(self, tmp_path: Path) -> None:
        main_path = tmp_path / "main"
        main_path.mkdir()
        with patch(
            "wade.git.repo.get_main_worktree_path",
            return_value=main_path,
        ):
            result = _canonical_project_root(tmp_path)
            assert result == main_path

    def test_swallows_git_error_and_returns_original(self, tmp_path: Path) -> None:
        from wade.git.repo import GitError

        with patch(
            "wade.git.repo.get_main_worktree_path",
            side_effect=GitError("git failure"),
        ):
            result = _canonical_project_root(tmp_path)
            assert result == tmp_path

    def test_swallows_os_error_and_returns_original(self, tmp_path: Path) -> None:
        with patch(
            "wade.git.repo.get_main_worktree_path",
            side_effect=OSError("path not found"),
        ):
            result = _canonical_project_root(tmp_path)
            assert result == tmp_path

    def test_unexpected_exception_propagates(self, tmp_path: Path) -> None:
        with (
            patch(
                "wade.git.repo.get_main_worktree_path",
                side_effect=RuntimeError("unexpected"),
            ),
            pytest.raises(RuntimeError, match="unexpected"),
        ):
            _canonical_project_root(tmp_path)

    def test_returns_original_when_main_equals_project_root(self, tmp_path: Path) -> None:
        with patch(
            "wade.git.repo.get_main_worktree_path",
            return_value=tmp_path,
        ):
            result = _canonical_project_root(tmp_path)
            assert result == tmp_path


class TestResolveCanonicalKnowledgePath:
    def test_resolves_from_main_worktree_when_in_linked_worktree(self, tmp_path: Path) -> None:
        main_path = tmp_path / "main"
        main_path.mkdir()
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        config = KnowledgeConfig(enabled=True, path="KNOWLEDGE.md")
        with patch(
            "wade.git.repo.get_main_worktree_path",
            return_value=main_path,
        ):
            result = resolve_canonical_knowledge_path(worktree_path, config)
            assert result == (main_path / "KNOWLEDGE.md").resolve()

    def test_resolves_from_project_root_when_not_in_worktree(self, tmp_path: Path) -> None:
        config = KnowledgeConfig(enabled=True, path="KNOWLEDGE.md")
        with patch(
            "wade.git.repo.get_main_worktree_path",
            return_value=None,
        ):
            result = resolve_canonical_knowledge_path(tmp_path, config)
            assert result == (tmp_path / "KNOWLEDGE.md").resolve()


class TestAppendKnowledgeWorktreeRedirect:
    def test_writes_to_main_worktree_when_called_from_linked_worktree(self, tmp_path: Path) -> None:
        main_root = tmp_path / "main"
        main_root.mkdir()
        worktree_root = tmp_path / "worktree"
        worktree_root.mkdir()
        config = KnowledgeConfig(enabled=True, path="KNOWLEDGE.md")

        with patch(
            "wade.git.repo.get_main_worktree_path",
            return_value=main_root,
        ):
            result = append_knowledge(
                project_root=worktree_root,
                config=config,
                content="Learned from worktree.",
                session_type="implementation",
            )

        # Entry must be in the main repo, not the worktree
        assert result.path == (main_root / "KNOWLEDGE.md").resolve()
        assert not (worktree_root / "KNOWLEDGE.md").exists()
        text = (main_root / "KNOWLEDGE.md").read_text(encoding="utf-8")
        assert "Learned from worktree." in text


# --- Plain ## Title entry support ---


class TestPlainEntryParsing:
    def test_parses_plain_heading(self) -> None:
        text = "## My Plain Entry\n\nSome plain content.\n\n---\n"
        entries = parse_entries(text)
        assert len(entries) == 1
        assert entries[0].entry_id is None
        assert entries[0].date is None
        assert entries[0].heading_rest == "My Plain Entry"
        assert entries[0].content == "Some plain content."

    def test_parses_multiple_plain_entries(self) -> None:
        text = (
            "## First Entry\n\nFirst content.\n\n---\n\n## Second Entry\n\nSecond content.\n\n---\n"
        )
        entries = parse_entries(text)
        assert len(entries) == 2
        assert entries[0].heading_rest == "First Entry"
        assert entries[1].heading_rest == "Second Entry"

    def test_plain_entries_mixed_with_dated_entries(self) -> None:
        text = (
            KNOWLEDGE_TEMPLATE
            + "\n## My Plain Entry\n\nPlain content.\n\n---\n\n"
            + "## a1b2c3d4 | 2026-03-24 | plan\n\nDated content.\n\n---\n"
        )
        entries = parse_entries(text)
        assert len(entries) == 2
        assert entries[0].entry_id is None
        assert entries[0].date is None
        assert entries[1].entry_id == "a1b2c3d4"
        assert entries[1].date == "2026-03-24"

    def test_plain_entry_not_score_annotated(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## My Plain Entry\n\nPlain content.\n\n---\n"
        (project_root / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        result = get_annotated_knowledge(project_root, config)
        assert result.content is not None
        assert "Plain content." in result.content
        assert "[+" not in result.content

    def test_search_finds_plain_entry_by_content(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        content = (
            KNOWLEDGE_TEMPLATE
            + "\n## My Plain Entry\n\nWorktree isolation tip.\n\n---\n\n"
            + "## Another Entry\n\nUnrelated stuff.\n\n---\n"
        )
        (project_root / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        result = get_annotated_knowledge(
            project_root, config, search_query="worktree", no_filter=True
        )
        assert result.content is not None
        assert "Worktree isolation tip." in result.content
        assert "Unrelated stuff." not in result.content
        assert result.entries_count == 1

    def test_search_finds_plain_entry_by_heading(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        content = (
            KNOWLEDGE_TEMPLATE
            + "\n## Git Worktree Tips\n\nSome content here.\n\n---\n\n"
            + "## Docker Tips\n\nOther content.\n\n---\n"
        )
        (project_root / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        result = get_annotated_knowledge(
            project_root, config, search_query="worktree", no_filter=True
        )
        assert result.content is not None
        assert "Git Worktree Tips" in result.content
        assert "Docker Tips" not in result.content

    def test_plain_entries_not_excluded_by_score_filter(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## My Plain Entry\n\nPlain content.\n\n---\n"
        (project_root / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        result = get_annotated_knowledge(project_root, config, min_score=0)
        assert result.content is not None
        assert "Plain content." in result.content


class TestNoMatchHeaderOnlyOutput:
    def test_no_match_returns_header_not_full_file(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nDocker tips.\n\n---\n"
        (project_root / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        result = get_annotated_knowledge(
            project_root, config, search_query="nonexistent", no_filter=True
        )
        assert result.entries_count == 0
        assert result.content is not None
        assert "Docker tips." not in result.content

    def test_no_match_with_plain_entries_returns_header_not_full_file(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## My Plain Entry\n\nPlain content.\n\n---\n"
        (project_root / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        result = get_annotated_knowledge(
            project_root, config, search_query="nonexistent", no_filter=True
        )
        assert result.entries_count == 0
        assert result.content is not None
        assert "Plain content." not in result.content


class TestPlainEntrySubheadingBehavior:
    def test_dated_entry_body_with_hash_hash_subheading(self) -> None:
        # Document current behavior: ## subheading inside a dated entry body is
        # treated as a boundary and becomes a second plain entry.
        text = (
            "## a1b2c3d4 | 2026-03-24 | plan\n\nIntro.\n\n## Background\n\nMore content.\n\n---\n"
        )
        entries = parse_entries(text)
        assert len(entries) == 2
        assert entries[0].entry_id == "a1b2c3d4"
        assert entries[1].heading_rest == "Background"


class TestWritePathMigration:
    def test_first_write_materializes_seed_and_removes_yaml(self, tmp_path: Path) -> None:
        # Reads never migrate; the first ratings WRITE converts .yml -> .jsonl.
        jsonl = tmp_path / "KNOWLEDGE.ratings.jsonl"
        legacy = tmp_path / "KNOWLEDGE.ratings.yml"
        legacy.write_text(yaml.safe_dump({"entry1": {"up": 5, "down": 1}}), encoding="utf-8")

        record_rating(jsonl, "entry1", "up")

        assert jsonl.exists()
        assert not legacy.exists()  # legacy removed as part of the write
        lines = jsonl.read_text(encoding="utf-8").splitlines()
        # A byte-deterministic seed (no ts) plus the appended vote line.
        expected_seed = (
            '{"down": 1, "event_id": "legacy-seed:entry1", "id": "entry1", "seed": true, '
            '"stale": 0, "superseded_by": null, "up": 5}'
        )
        assert expected_seed in lines
        folded = read_ratings(jsonl)
        assert folded["entry1"].up == 6  # 5 seed + 1 vote
        assert folded["entry1"].down == 1

    def test_seed_is_byte_deterministic_across_runs(self, tmp_path: Path) -> None:
        # Two independent migrations of the same legacy yml produce identical seed bytes.
        def _seed_line(dir_: Path) -> str:
            dir_.mkdir()
            legacy = dir_ / "KNOWLEDGE.ratings.yml"
            legacy.write_text(
                yaml.safe_dump({"e": {"up": 2, "down": 3, "stale": 1}}), encoding="utf-8"
            )
            jsonl = dir_ / "KNOWLEDGE.ratings.jsonl"
            record_rating(jsonl, "e", "up")
            return jsonl.read_text(encoding="utf-8").splitlines()[0]

        assert _seed_line(tmp_path / "a") == _seed_line(tmp_path / "b")

    def test_write_does_not_migrate_when_jsonl_present(self, tmp_path: Path) -> None:
        # A legacy yml alongside an existing jsonl is left untouched (already migrated).
        jsonl = tmp_path / "KNOWLEDGE.ratings.jsonl"
        jsonl.write_text('{"dir": "up", "id": "e", "ts": "t"}\n', encoding="utf-8")
        legacy = tmp_path / "KNOWLEDGE.ratings.yml"
        legacy.write_text("stale-legacy: {}\n", encoding="utf-8")

        record_rating(jsonl, "e", "down")

        assert legacy.exists()  # not touched — jsonl already exists
        folded = read_ratings(jsonl)
        assert folded["e"].up == 1
        assert folded["e"].down == 1


class TestStagedRatingEvents:
    """Detached vote transport keeps main clean and retries idempotently."""

    def test_empty_staging_is_removed_without_materializing_main_ratings(
        self, tmp_path: Path, config: KnowledgeConfig
    ) -> None:
        main = tmp_path / "main"
        worktree = tmp_path / "plan-worktree"
        main.mkdir()
        worktree.mkdir()
        staged = staged_ratings_path(worktree)
        staged.parent.mkdir()
        staged.write_text("", encoding="utf-8")

        result = flush_staged_ratings(worktree, main, config)

        assert result.success
        assert not staged.exists()
        assert not (main / "KNOWLEDGE.ratings.jsonl").exists()

    def test_new_event_id_deduplicates_delivery_but_legacy_duplicate_does_not(
        self, tmp_path: Path
    ) -> None:
        ratings = tmp_path / "KNOWLEDGE.ratings.jsonl"
        event = create_rating_event("entry", "up")
        record = event.to_record()
        ratings.write_text(
            "\n".join(
                [
                    json.dumps(record),
                    json.dumps(record),
                    # Legacy records have no event_id, so two historical votes
                    # remain two votes rather than being accidentally collapsed.
                    '{"dir": "up", "id": "legacy", "ts": "same"}',
                    '{"dir": "up", "id": "legacy", "ts": "same"}',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        folded = read_ratings(ratings)

        assert folded["entry"].up == 1
        assert folded["legacy"].up == 2

    def test_detached_rate_stages_without_writing_canonical_sidecar(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "wade.services.knowledge_service.is_throwaway_knowledge_session", lambda _: True
        )

        event = record_rating_for_session(tmp_path, config, "entry", "up")

        staged = staged_ratings_path(tmp_path)
        assert staged.is_file()
        assert event.event_id in staged.read_text(encoding="utf-8")
        assert not (tmp_path / "KNOWLEDGE.ratings.jsonl").exists()

    def test_flush_hands_off_once_and_retry_only_cleans_staging(
        self, tmp_path: Path, config: KnowledgeConfig
    ) -> None:
        main = tmp_path / "main"
        worktree = tmp_path / "plan-worktree"
        main.mkdir()
        worktree.mkdir()
        event = create_rating_event("entry", "up")
        stage_rating_event(worktree, event)

        first = flush_staged_ratings(worktree, main, config)

        assert first.success
        assert first.appended_count == 1
        assert not staged_ratings_path(worktree).exists()
        canonical = main / "KNOWLEDGE.ratings.jsonl"
        assert read_ratings(canonical)["entry"].up == 1

        # Simulate interruption after a durable append but before staging cleanup.
        stage_rating_event(worktree, event)
        retry = flush_staged_ratings(worktree, main, config)

        assert retry.success
        assert retry.appended_count == 0
        assert not staged_ratings_path(worktree).exists()
        assert read_ratings(canonical)["entry"].up == 1

    def test_flush_blocks_a_live_append_until_after_staging_cleanup(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A sibling append cannot be lost between the drain snapshot and unlink."""
        from wade.services import knowledge_service

        main = tmp_path / "main"
        worktree = tmp_path / "plan-worktree"
        main.mkdir()
        worktree.mkdir()
        first = create_rating_event("first", "up")
        second = create_rating_event("second", "up")
        stage_rating_event(worktree, first)

        snapshot_loaded = threading.Event()
        continue_flush = threading.Event()
        append_finished = threading.Event()
        original_load = knowledge_service._load_staged_rating_records

        def pause_after_snapshot(path: Path) -> list[dict[str, object]]:
            records = original_load(path)
            snapshot_loaded.set()
            assert continue_flush.wait(timeout=1)
            return records

        monkeypatch.setattr(
            "wade.services.knowledge_service._load_staged_rating_records", pause_after_snapshot
        )
        outcomes = []
        drain = threading.Thread(
            target=lambda: outcomes.append(flush_staged_ratings(worktree, main, config))
        )
        append = threading.Thread(
            target=lambda: (stage_rating_event(worktree, second), append_finished.set())
        )
        drain.start()
        assert snapshot_loaded.wait(timeout=1)
        append.start()

        # The writer uses the same staging-path lock, so it must remain blocked
        # until the handoff has unlinked the snapshot it just delivered.
        assert not append_finished.wait(timeout=0.05)
        continue_flush.set()
        drain.join(timeout=1)
        append.join(timeout=1)

        assert not drain.is_alive()
        assert not append.is_alive()
        assert len(outcomes) == 1
        assert outcomes[0].success
        assert read_ratings(main / "KNOWLEDGE.ratings.jsonl")["first"].up == 1
        staged = staged_ratings_path(worktree)
        assert staged.is_file()
        assert second.event_id in staged.read_text(encoding="utf-8")
        assert first.event_id not in staged.read_text(encoding="utf-8")

    def test_flush_failure_retains_staging_for_recovery(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        main = tmp_path / "main"
        worktree = tmp_path / "plan-worktree"
        main.mkdir()
        worktree.mkdir()
        stage_rating_event(worktree, create_rating_event("entry", "up"))

        def fail_migration(_: Path) -> None:
            raise OSError("main checkout is not writable")

        monkeypatch.setattr(
            "wade.services.knowledge_service._materialize_migration_locked", fail_migration
        )
        result = flush_staged_ratings(worktree, main, config)

        assert not result.success
        assert "not writable" in (result.message or "")
        assert staged_ratings_path(worktree).is_file()

    def test_malformed_staging_log_retains_artefact_and_writes_nothing(
        self, tmp_path: Path, config: KnowledgeConfig
    ) -> None:
        """A corrupt transport log is a retryable handoff failure, not garbage to drop."""
        main = tmp_path / "main"
        worktree = tmp_path / "plan-worktree"
        main.mkdir()
        worktree.mkdir()
        stage_rating_event(worktree, create_rating_event("entry", "up"))
        staging = staged_ratings_path(worktree)
        with staging.open("a", encoding="utf-8") as fd:
            fd.write("not json at all\n")

        result = flush_staged_ratings(worktree, main, config)

        assert not result.success
        assert staging.is_file()
        assert not (main / "KNOWLEDGE.ratings.jsonl").exists()


class TestStagingContainment:
    """A vote must never be redirected out of the throwaway session worktree."""

    def test_symlinked_staging_dir_is_refused_by_the_writer(self, tmp_path: Path) -> None:
        worktree = tmp_path / "plan-worktree"
        outside = tmp_path / "elsewhere"
        worktree.mkdir()
        outside.mkdir()
        (worktree / ".wade").symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError, match="outside the session worktree"):
            stage_rating_event(worktree, create_rating_event("entry", "up"))

        assert list(outside.iterdir()) == []

    def test_contained_staging_dir_is_accepted(self, tmp_path: Path) -> None:
        worktree = tmp_path / "plan-worktree"
        worktree.mkdir()

        path = stage_rating_event(worktree, create_rating_event("entry", "up"))

        assert path == staged_ratings_path(worktree)
        assert path.is_file()

    def test_staging_creates_a_missing_wade_dir(self, tmp_path: Path) -> None:
        # `stage_rating_event` never mkdirs explicitly — `file_lock` creates the
        # guarded path's parent. Pinned because it is easy to misread as a
        # FileNotFoundError waiting to happen (the readiness probe removes any
        # `.wade/` it had to create, so "absent at write time" is a real state).
        worktree = tmp_path / "plan-worktree"
        worktree.mkdir()
        assert not (worktree / ".wade").exists()

        path = stage_rating_event(worktree, create_rating_event("entry", "up"))

        assert path.is_file()
        assert '"id": "entry"' in path.read_text(encoding="utf-8")

    def test_readiness_probe_rejects_a_symlinked_staging_dir(self, tmp_path: Path) -> None:
        from wade.services.check_service import _probe_staging_path

        worktree = tmp_path / "plan-worktree"
        outside = tmp_path / "elsewhere"
        worktree.mkdir()
        outside.mkdir()
        (worktree / ".wade").symlink_to(outside, target_is_directory=True)

        assert _probe_staging_path(worktree) is False

    def test_symlinked_marker_dir_is_refused_by_the_marker_writer(self, tmp_path: Path) -> None:
        """`.wade/` is repo-controlled, so marking must not become a write outside."""
        from wade.services.knowledge_service import mark_throwaway_knowledge_session

        worktree = tmp_path / "plan-worktree"
        outside = tmp_path / "elsewhere"
        worktree.mkdir()
        outside.mkdir()
        (worktree / ".wade").symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError, match="outside the worktree"):
            mark_throwaway_knowledge_session(worktree)

        assert list(outside.iterdir()) == []

    def test_flush_neither_reads_nor_deletes_through_a_symlinked_staging_dir(
        self, tmp_path: Path, config: KnowledgeConfig
    ) -> None:
        """The post-transfer unlink must not be redirected onto an outside file."""
        main = tmp_path / "main"
        worktree = tmp_path / "plan-worktree"
        outside = tmp_path / "elsewhere"
        for directory in (main, worktree, outside):
            directory.mkdir()
        planted = outside / "knowledge-ratings-staged.jsonl"
        planted.write_text("", encoding="utf-8")
        (worktree / ".wade").symlink_to(outside, target_is_directory=True)

        result = flush_staged_ratings(worktree, main, config)

        # Skipped, not failed: nothing could legitimately be staged there, and a
        # failure would strand the worktree on a condition no retry can clear.
        assert result.success
        assert "outside the session worktree" in (result.message or "")
        assert planted.exists()
        assert not (main / "KNOWLEDGE.ratings.jsonl").exists()


class TestRetainedStagedRatingsSweep:
    """A retained worktree's votes are recovered by the next plan/deps run."""

    @staticmethod
    def _throwaway_worktree(root: Path, name: str) -> Path:
        worktree = root / name
        worktree.mkdir()
        (worktree / ".wade").mkdir()
        (worktree / ".wade" / "throwaway-session").write_text("marker\n", encoding="utf-8")
        return worktree

    @staticmethod
    def _patch_worktree_list(monkeypatch: pytest.MonkeyPatch, paths: list[Path]) -> None:
        from wade.models import Worktree

        entries = [Worktree(path=str(p), branch="(detached)") for p in paths]
        monkeypatch.setattr(
            "wade.git.worktree.list_worktrees",
            lambda _: entries,
        )
        monkeypatch.setattr("wade.git.repo.is_worktree", lambda _: True)
        monkeypatch.setattr("wade.git.repo.is_head_attached", lambda _: False)

    def test_recovers_votes_left_behind_by_a_failed_handoff(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from wade.services.knowledge_service import flush_retained_staged_ratings

        main = tmp_path / "main"
        main.mkdir()
        retained = self._throwaway_worktree(tmp_path, "plan-retained")
        stage_rating_event(retained, create_rating_event("entry", "up"))
        self._patch_worktree_list(monkeypatch, [retained])

        outcomes = flush_retained_staged_ratings(main, config)

        assert [(o.success, o.appended_count, o.worktree) for o in outcomes] == [
            (True, 1, retained)
        ]
        assert not staged_ratings_path(retained).exists()
        assert read_ratings(main / "KNOWLEDGE.ratings.jsonl")["entry"].up == 1

    def test_reports_nothing_when_no_worktree_has_staged_votes(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from wade.services.knowledge_service import flush_retained_staged_ratings

        main = tmp_path / "main"
        main.mkdir()
        clean = self._throwaway_worktree(tmp_path, "plan-clean")
        self._patch_worktree_list(monkeypatch, [clean])

        assert flush_retained_staged_ratings(main, config) == []

    def test_skips_a_symlinked_repo_root(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The self-worktree guard compares canonical paths from git worktree list."""
        from wade.services.knowledge_service import flush_retained_staged_ratings

        main = self._throwaway_worktree(tmp_path, "main")
        repo_root = tmp_path / "main-link"
        repo_root.symlink_to(main, target_is_directory=True)
        stage_rating_event(main, create_rating_event("entry", "up"))
        self._patch_worktree_list(monkeypatch, [main])

        assert flush_retained_staged_ratings(repo_root, config) == []
        assert staged_ratings_path(main).exists()

    def test_skips_a_worktree_without_the_throwaway_marker(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No marker means no wade parent — its `.wade/` is not ours to drain."""
        from wade.services.knowledge_service import flush_retained_staged_ratings

        main = tmp_path / "main"
        main.mkdir()
        foreign = tmp_path / "hand-made"
        foreign.mkdir()
        staged = staged_ratings_path(foreign)
        staged.parent.mkdir()
        staged.write_text("", encoding="utf-8")
        self._patch_worktree_list(monkeypatch, [foreign])

        assert flush_retained_staged_ratings(main, config) == []
        assert staged.exists()

    def test_a_failed_retry_keeps_the_staging_log_for_the_next_run(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from wade.services.knowledge_service import flush_retained_staged_ratings

        main = tmp_path / "main"
        main.mkdir()
        retained = self._throwaway_worktree(tmp_path, "plan-retained")
        stage_rating_event(retained, create_rating_event("entry", "up"))
        self._patch_worktree_list(monkeypatch, [retained])

        def fail_migration(_: Path) -> None:
            raise OSError("main checkout is not writable")

        monkeypatch.setattr(
            "wade.services.knowledge_service._materialize_migration_locked", fail_migration
        )

        outcomes = flush_retained_staged_ratings(main, config)

        assert [(o.success, o.worktree) for o in outcomes] == [(False, retained)]
        assert staged_ratings_path(retained).is_file()

    def test_git_failure_is_not_a_vote_error(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from wade.git.repo import GitError
        from wade.services.knowledge_service import flush_retained_staged_ratings

        def boom(_: Path) -> list[object]:
            raise GitError("git unavailable")

        monkeypatch.setattr("wade.git.worktree.list_worktrees", boom)

        assert flush_retained_staged_ratings(tmp_path, config) == []


class TestDetachedKnowledgeReads:
    """Read-only plan/deps commands must not cross into the main checkout."""

    def test_annotated_get_uses_local_snapshot_without_canonical_resolution(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "KNOWLEDGE.md").write_text(
            KNOWLEDGE_TEMPLATE
            + "\n## a1b2c3d4 | 2026-03-24 | plan\n\nLocal detached knowledge.\n\n---\n",
            encoding="utf-8",
        )

        def main_checkout_must_not_be_read(_: Path) -> Path:
            raise AssertionError("read-only detached knowledge command escaped its worktree")

        monkeypatch.setattr(
            "wade.services.knowledge_service._resolve_knowledge_root",
            main_checkout_must_not_be_read,
        )

        result = get_annotated_knowledge(tmp_path, config, no_filter=True)

        assert result.content is not None
        assert "Local detached knowledge." in result.content

    def test_status_uses_local_snapshot_without_canonical_resolution(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "KNOWLEDGE.ratings.yml").write_text("entry: {up: 1}\n", encoding="utf-8")

        def main_checkout_must_not_be_read(_: Path) -> Path:
            raise AssertionError("status escaped its detached worktree")

        monkeypatch.setattr(
            "wade.services.knowledge_service._resolve_knowledge_root",
            main_checkout_must_not_be_read,
        )

        result = knowledge_status(tmp_path, config)

        assert result.root == tmp_path
        assert result.legacy_migration_pending is True


class TestValidateKnowledgeFile:
    def test_valid_file_has_no_problems(self, tmp_path: Path) -> None:
        path = tmp_path / "KNOWLEDGE.md"
        path.write_text(
            "# Project Knowledge\n\n## abcd1234 | 2026-01-01 | plan\n\nbody\n\n---\n",
            encoding="utf-8",
        )
        assert validate_knowledge_file(path) == []

    def test_missing_file_has_no_problems(self, tmp_path: Path) -> None:
        assert validate_knowledge_file(tmp_path / "nope.md") == []

    def test_duplicate_entry_id_is_flagged(self, tmp_path: Path) -> None:
        # A union merge of a rewrite-in-place + append can leave two copies of one heading.
        path = tmp_path / "KNOWLEDGE.md"
        path.write_text(
            "# Project Knowledge\n\n"
            "## abcd1234 | 2026-01-01 | plan\n\nbody one\n\n---\n"
            "## abcd1234 | 2026-01-01 | plan | tags: git\n\nbody two\n\n---\n",
            encoding="utf-8",
        )
        problems = validate_knowledge_file(path)
        assert any("Duplicate entry ID 'abcd1234'" in p for p in problems)

    def test_heading_shaped_body_line_is_not_a_false_positive(self, tmp_path: Path) -> None:
        # An entry body may legitimately quote the heading format (e.g. an entry about
        # the knowledge schema). parse_entries accepts such lines as plain headings, so
        # validation must NOT flag them — a false positive would block done.
        path = tmp_path / "KNOWLEDGE.md"
        path.write_text(
            "# Project Knowledge\n\n"
            "## abcd1234 | 2026-01-01 | plan\n\n"
            "Entry headings look like: ## someid | 2026-01-01 | plan | tags: git\n\n---\n",
            encoding="utf-8",
        )
        assert validate_knowledge_file(path) == []

    def test_conflict_markers_are_flagged(self, tmp_path: Path) -> None:
        path = tmp_path / "KNOWLEDGE.md"
        path.write_text(
            "# Project Knowledge\n\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n",
            encoding="utf-8",
        )
        problems = validate_knowledge_file(path)
        assert any("conflict markers" in p for p in problems)

    def test_decorative_separator_is_not_a_false_positive(self, tmp_path: Path) -> None:
        # A long run of `=` in an entry body is a decorative separator, NOT a git
        # conflict marker (git writes markers as exactly seven chars). Validation must
        # not flag it — a false positive would block an otherwise-valid PR at `done`.
        path = tmp_path / "KNOWLEDGE.md"
        path.write_text(
            "# Project Knowledge\n\n"
            "## abcd1234 | 2026-01-01 | plan\n\n"
            "Section divider:\n====================\nmore body\n\n---\n",
            encoding="utf-8",
        )
        assert validate_knowledge_file(path) == []


class TestRefusalForThrowawayWrite:
    """`refusal_for_throwaway_write` — the detached-session write policy (service layer)."""

    def test_none_outside_git_repo(self, tmp_path: Path) -> None:
        # A non-repo / git-unavailable path is not a throwaway session — allow the write.
        with patch("wade.git.repo.get_git_dir", return_value=None):
            assert refusal_for_throwaway_write(tmp_path, "wade knowledge add") is None

    def test_none_when_head_attached(self, tmp_path: Path) -> None:
        # A branch-backed worktree (or the main checkout) carries the edit — allow it.
        with (
            patch("wade.git.repo.get_git_dir", return_value=".git"),
            patch("wade.git.repo.is_head_attached", return_value=True),
        ):
            assert refusal_for_throwaway_write(tmp_path, "wade knowledge add") is None

    def test_refuses_detached_without_plan_hint(self, tmp_path: Path) -> None:
        # A detached HEAD with no plan dir (task deps) refuses, and gives no plan hint.
        with (
            patch("wade.git.repo.get_git_dir", return_value=".git"),
            patch("wade.git.repo.is_head_attached", return_value=False),
        ):
            refusal = refusal_for_throwaway_write(tmp_path, "wade knowledge add")
        assert refusal is not None
        assert refusal.command == "wade knowledge add"
        assert refusal.plan_hint is False

    def test_refuses_detached_with_plan_hint(self, tmp_path: Path) -> None:
        # A detached HEAD with a plan dir (plan session) refuses AND flags the plan hint.
        (tmp_path / ".wade" / "plans").mkdir(parents=True)
        with (
            patch("wade.git.repo.get_git_dir", return_value=".git"),
            patch("wade.git.repo.is_head_attached", return_value=False),
        ):
            refusal = refusal_for_throwaway_write(tmp_path, "wade knowledge tag add")
        assert refusal is not None
        assert refusal.plan_hint is True


class TestKnowledgeStatus:
    def test_clean_when_no_changes(self, tmp_path: Path, config: KnowledgeConfig) -> None:
        # Not a git repo → scoped porcelain returns nothing, and no legacy pending.
        result = knowledge_status(tmp_path, config)
        assert result.dirty_paths == []
        assert result.legacy_migration_pending is False

    def test_reports_pending_legacy_migration(
        self, tmp_path: Path, config: KnowledgeConfig
    ) -> None:
        # A legacy .yml with no .jsonl yet is reported as pending on-disk migration.
        (tmp_path / "KNOWLEDGE.ratings.yml").write_text("e: {up: 1}\n", encoding="utf-8")
        result = knowledge_status(tmp_path, config)
        assert result.legacy_migration_pending is True

    def test_reports_staged_detached_votes(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "wade.services.knowledge_service.is_throwaway_knowledge_session", lambda _: True
        )
        stage_rating_event(tmp_path, create_rating_event("entry", "up"))

        result = knowledge_status(tmp_path, config)

        assert result.staged_vote_count == 1
        assert result.staging_path == staged_ratings_path(tmp_path)

    def test_corrupt_staging_log_still_reports_the_rest_of_the_status(
        self, tmp_path: Path, config: KnowledgeConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`status` is the diagnostic for a failed handoff — it must not fail with it."""
        monkeypatch.setattr(
            "wade.services.knowledge_service.is_throwaway_knowledge_session", lambda _: True
        )
        (tmp_path / "KNOWLEDGE.ratings.yml").write_text("e: {up: 1}\n", encoding="utf-8")
        staged_ratings_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        staged_ratings_path(tmp_path).write_text("not json at all\n", encoding="utf-8")

        result = knowledge_status(tmp_path, config)

        assert result.staged_vote_count == 0
        assert result.staging_path == staged_ratings_path(tmp_path)
        assert result.legacy_migration_pending is True
