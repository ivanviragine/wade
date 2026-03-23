"""Unit tests for knowledge_service."""

from __future__ import annotations

from pathlib import Path

import pytest

from wade.models.config import KnowledgeConfig
from wade.services.knowledge_service import (
    KNOWLEDGE_TEMPLATE,
    append_knowledge,
    ensure_knowledge_file,
    read_knowledge,
    resolve_knowledge_path,
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

    def test_returns_empty_string_when_file_missing(
        self, project_root: Path, config: KnowledgeConfig
    ) -> None:
        assert read_knowledge(project_root, config) == ""

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
    def test_appends_entry_with_issue(self, project_root: Path, config: KnowledgeConfig) -> None:
        path = append_knowledge(
            project_root=project_root,
            config=config,
            content="Discovered that tests need DB setup.",
            session_type="implementation",
            issue_ref="42",
        )
        text = path.read_text(encoding="utf-8")
        assert "## " in text
        assert "| implementation | Issue #42" in text
        assert "Discovered that tests need DB setup." in text
        assert text.endswith("---\n")

    def test_appends_entry_without_issue(self, project_root: Path, config: KnowledgeConfig) -> None:
        path = append_knowledge(
            project_root=project_root,
            config=config,
            content="General project pattern.",
            session_type="plan",
        )
        text = path.read_text(encoding="utf-8")
        assert "| plan" in text
        assert "Issue #" not in text
        assert "General project pattern." in text

    def test_creates_file_if_missing(self, project_root: Path, config: KnowledgeConfig) -> None:
        path = append_knowledge(
            project_root=project_root,
            config=config,
            content="First entry.",
            session_type="implementation",
            issue_ref="1",
        )
        text = path.read_text(encoding="utf-8")
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
