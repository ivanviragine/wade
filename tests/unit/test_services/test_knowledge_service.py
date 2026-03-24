"""Unit tests for knowledge_service."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from wade.models.config import KnowledgeConfig
from wade.services.knowledge_service import (
    KNOWLEDGE_TEMPLATE,
    KnowledgeEntry,
    append_knowledge,
    ensure_knowledge_file,
    find_entry_id,
    get_annotated_knowledge,
    parse_entries,
    read_knowledge,
    read_ratings,
    record_rating,
    record_supersede,
    resolve_knowledge_path,
    resolve_ratings_path,
)


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
        assert result == Path("/project/KNOWLEDGE.ratings.yml")

    def test_works_with_custom_name(self) -> None:
        result = resolve_ratings_path(Path("/project/docs/LEARNINGS.md"))
        assert result == Path("/project/docs/LEARNINGS.ratings.yml")


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
        assert re.match(r"^[0-9a-f]{8}$", result.entry_id)

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
        assert read_ratings(tmp_path / "KNOWLEDGE.ratings.yml") == {}

    def test_returns_empty_for_empty_file(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.yml"
        ratings_path.write_text("", encoding="utf-8")
        assert read_ratings(ratings_path) == {}

    def test_loads_existing_ratings(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.yml"
        data = {"a1b2c3d4": {"up": 3, "down": 1}}
        ratings_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        result = read_ratings(ratings_path)
        assert result["a1b2c3d4"]["up"] == 3
        assert result["a1b2c3d4"]["down"] == 1


class TestRecordRating:
    def test_creates_file_and_records_up(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.yml"
        record_rating(ratings_path, "a1b2c3d4", "up")
        data = read_ratings(ratings_path)
        assert data["a1b2c3d4"]["up"] == 1
        assert data["a1b2c3d4"]["down"] == 0

    def test_records_down(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.yml"
        record_rating(ratings_path, "a1b2c3d4", "down")
        data = read_ratings(ratings_path)
        assert data["a1b2c3d4"]["up"] == 0
        assert data["a1b2c3d4"]["down"] == 1

    def test_increments_existing_count(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.yml"
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "a1b2c3d4", "down")
        data = read_ratings(ratings_path)
        assert data["a1b2c3d4"]["up"] == 2
        assert data["a1b2c3d4"]["down"] == 1

    def test_rejects_invalid_direction(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.yml"
        with pytest.raises(ValueError, match="must be 'up' or 'down'"):
            record_rating(ratings_path, "a1b2c3d4", "sideways")

    def test_multiple_entries(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.yml"
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "f5e6d7c8", "down")
        data = read_ratings(ratings_path)
        assert data["a1b2c3d4"]["up"] == 1
        assert data["f5e6d7c8"]["down"] == 1


class TestRecordSupersede:
    def test_records_supersede_link(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.yml"
        record_supersede(ratings_path, "old12345", "new67890")
        data = read_ratings(ratings_path)
        assert data["old12345"]["superseded_by"] == "new67890"

    def test_preserves_existing_ratings(self, tmp_path: Path) -> None:
        ratings_path = tmp_path / "KNOWLEDGE.ratings.yml"
        record_rating(ratings_path, "old12345", "down")
        record_rating(ratings_path, "old12345", "down")
        record_supersede(ratings_path, "old12345", "new67890")
        data = read_ratings(ratings_path)
        assert data["old12345"]["down"] == 2
        assert data["old12345"]["superseded_by"] == "new67890"


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
        assert get_annotated_knowledge(project_root, config) is None

    def test_returns_unmodified_when_no_ratings(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        result = get_annotated_knowledge(project_root, config)
        assert result is not None
        assert "[+" not in result
        assert "Useful content." in result
        assert "Outdated content." in result

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
        assert result is not None
        assert "[+3/-0]" in result
        assert "[+0/-1]" in result

    def test_min_score_filters_entries(self, project_root: Path, config: KnowledgeConfig) -> None:
        self._make_knowledge_file(project_root)
        ratings_path = resolve_ratings_path(project_root / "KNOWLEDGE.md")
        record_rating(ratings_path, "a1b2c3d4", "up")
        record_rating(ratings_path, "f5e6d7c8", "down")
        record_rating(ratings_path, "f5e6d7c8", "down")

        result = get_annotated_knowledge(project_root, config, min_score=0)
        assert result is not None
        assert "Useful content." in result
        assert "Outdated content." not in result

    def test_min_score_zero_includes_unrated(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        # No ratings at all — implicit score 0
        result = get_annotated_knowledge(project_root, config, min_score=0)
        assert result is not None
        assert "Useful content." in result
        assert "Outdated content." in result

    def test_min_score_one_excludes_unrated(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        self._make_knowledge_file(project_root)
        result = get_annotated_knowledge(project_root, config, min_score=1)
        assert result is not None
        assert "Useful content." not in result
        assert "Outdated content." not in result

    def test_preserves_header(self, project_root: Path, config: KnowledgeConfig) -> None:
        self._make_knowledge_file(project_root)
        result = get_annotated_knowledge(project_root, config)
        assert result is not None
        assert "# Project Knowledge" in result

    def test_no_annotation_for_id_less_entries(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        content = KNOWLEDGE_TEMPLATE + "\n## 2026-03-24 | plan\n\nOld entry.\n\n---\n"
        (project_root / "KNOWLEDGE.md").write_text(content, encoding="utf-8")
        result = get_annotated_knowledge(project_root, config)
        assert result is not None
        assert "Old entry." in result
        assert "[+" not in result


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
        assert result is not None
        assert "Old content without ID." in result
        assert "New content with ID." in result

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
        result = append_knowledge(
            project_root=project_root,
            config=config,
            content="New with ID.",
            session_type="implementation",
        )
        entries = parse_entries((project_root / "KNOWLEDGE.md").read_text(encoding="utf-8"))
        assert len(entries) == 2
        assert entries[0].entry_id is None
        assert entries[1].entry_id == result.entry_id
