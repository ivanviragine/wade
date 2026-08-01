"""Tests for detect_doc_targets/format_doc_targets in skills/doc_targets.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from wade.skills.doc_targets import detect_doc_targets, format_doc_targets


class TestDetectDocTargetsRootFiles:
    @pytest.mark.parametrize("filename", ["README.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md"])
    def test_included_when_present(self, tmp_path: Path, filename: str) -> None:
        (tmp_path / filename).write_text("content")
        assert detect_doc_targets(tmp_path) == [filename]

    @pytest.mark.parametrize("filename", ["README.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md"])
    def test_excluded_when_absent(self, tmp_path: Path, filename: str) -> None:
        assert filename not in detect_doc_targets(tmp_path)

    def test_presentation_order(self, tmp_path: Path) -> None:
        # Create out of order — result must follow README, AGENTS, CLAUDE, CONTRIBUTING.
        (tmp_path / "CONTRIBUTING.md").write_text("x")
        (tmp_path / "CLAUDE.md").write_text("x")
        (tmp_path / "README.md").write_text("x")
        (tmp_path / "AGENTS.md").write_text("x")

        assert detect_doc_targets(tmp_path) == [
            "README.md",
            "AGENTS.md",
            "CLAUDE.md",
            "CONTRIBUTING.md",
        ]

    def test_empty_project_produces_empty_list(self, tmp_path: Path) -> None:
        assert detect_doc_targets(tmp_path) == []


class TestDetectDocTargetsDocsDir:
    def test_non_generated_docs_dir_included(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.md").write_text("x")

        assert detect_doc_targets(tmp_path) == ["docs/"]

    def test_missing_docs_dir_not_included(self, tmp_path: Path) -> None:
        assert "docs/" not in detect_doc_targets(tmp_path)

    @pytest.mark.parametrize(
        "marker",
        [
            "mkdocs.yml",
            "mkdocs.yaml",
            "docs/conf.py",
            "docs/_build",
            "docs/.vitepress",
            "docs/book.toml",
            "docs/_config.yml",
            "docusaurus.config.js",
            "docusaurus.config.ts",
        ],
    )
    def test_generated_docs_skipped_via_marker(self, tmp_path: Path, marker: str) -> None:
        (tmp_path / "docs").mkdir()
        marker_path = tmp_path / marker
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        if marker.endswith("_build") or marker.endswith(".vitepress"):
            marker_path.mkdir()
        else:
            marker_path.write_text("x")

        assert "docs/" not in detect_doc_targets(tmp_path)

    @pytest.mark.parametrize("entry", ["docs", "docs/", "/docs", "/docs/"])
    def test_generated_docs_skipped_via_gitignore(self, tmp_path: Path, entry: str) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / ".gitignore").write_text(f"node_modules\n{entry}\n# comment\n")

        assert "docs/" not in detect_doc_targets(tmp_path)

    def test_gitignore_unrelated_entries_do_not_suppress_docs(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / ".gitignore").write_text("node_modules\n# docs\ndocs/**\napps/docs/\n")

        # None of these lines are a bare docs/docs//docs//docs/ match — docs/ stays included.
        assert "docs/" in detect_doc_targets(tmp_path)

    def test_gitignore_without_docs_entry_does_not_suppress(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / ".gitignore").write_text("node_modules\n*.log\n")

        assert "docs/" in detect_doc_targets(tmp_path)


class TestFormatDocTargets:
    def test_empty_list_uses_fallback_wording(self) -> None:
        assert format_doc_targets([]) == "the project's documentation, if it has any"

    def test_non_empty_list_backtick_quoted_comma_joined(self) -> None:
        assert format_doc_targets(["README.md", "AGENTS.md", "docs/"]) == (
            "`README.md`, `AGENTS.md`, `docs/`"
        )

    def test_single_target(self) -> None:
        assert format_doc_targets(["README.md"]) == "`README.md`"
