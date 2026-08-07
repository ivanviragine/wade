"""Tests for the wade-managed knowledge ``merge=union`` .gitattributes block (#358)."""

from __future__ import annotations

from pathlib import Path

from wade.models.config import KnowledgeConfig, ProjectConfig
from wade.skills.installer import (
    KNOWLEDGE_ATTRIBUTES_MARKER_END,
    KNOWLEDGE_ATTRIBUTES_MARKER_START,
    ensure_knowledge_merge_attributes,
)


def _config(path: str = "KNOWLEDGE.md") -> ProjectConfig:
    return ProjectConfig(knowledge=KnowledgeConfig(enabled=True, path=path))


def test_writes_union_block_when_absent(tmp_path: Path) -> None:
    ensure_knowledge_merge_attributes(tmp_path, _config())
    content = (tmp_path / ".gitattributes").read_text(encoding="utf-8")
    assert KNOWLEDGE_ATTRIBUTES_MARKER_START in content
    assert KNOWLEDGE_ATTRIBUTES_MARKER_END in content
    assert "KNOWLEDGE.md merge=union" in content
    assert "KNOWLEDGE.ratings.jsonl merge=union" in content


def test_derives_paths_from_custom_knowledge_path(tmp_path: Path) -> None:
    ensure_knowledge_merge_attributes(tmp_path, _config("docs/LEARNINGS.md"))
    content = (tmp_path / ".gitattributes").read_text(encoding="utf-8")
    assert "docs/LEARNINGS.md merge=union" in content
    assert "docs/LEARNINGS.ratings.jsonl merge=union" in content


def test_preserves_existing_unrelated_content(tmp_path: Path) -> None:
    gitattributes = tmp_path / ".gitattributes"
    gitattributes.write_text("*.png binary\n", encoding="utf-8")
    ensure_knowledge_merge_attributes(tmp_path, _config())
    content = gitattributes.read_text(encoding="utf-8")
    assert "*.png binary" in content
    assert "KNOWLEDGE.md merge=union" in content


def test_idempotent_no_rewrite_when_already_correct(tmp_path: Path) -> None:
    ensure_knowledge_merge_attributes(tmp_path, _config())
    gitattributes = tmp_path / ".gitattributes"
    first = gitattributes.read_text(encoding="utf-8")
    mtime_before = gitattributes.stat().st_mtime_ns

    ensure_knowledge_merge_attributes(tmp_path, _config())
    # Content identical, and the file is not rewritten (mtime unchanged) so a project
    # that already has the block is never marked dirty.
    assert gitattributes.read_text(encoding="utf-8") == first
    assert gitattributes.stat().st_mtime_ns == mtime_before
    assert first.count(KNOWLEDGE_ATTRIBUTES_MARKER_START) == 1


def test_replaces_stale_block_on_path_change(tmp_path: Path) -> None:
    ensure_knowledge_merge_attributes(tmp_path, _config("KNOWLEDGE.md"))
    ensure_knowledge_merge_attributes(tmp_path, _config("docs/LEARNINGS.md"))
    content = (tmp_path / ".gitattributes").read_text(encoding="utf-8")
    # Only one managed block, pointing at the new path.
    assert content.count(KNOWLEDGE_ATTRIBUTES_MARKER_START) == 1
    assert "docs/LEARNINGS.md merge=union" in content
    assert "\nKNOWLEDGE.md merge=union" not in content
