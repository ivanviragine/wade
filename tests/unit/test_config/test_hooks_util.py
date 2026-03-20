"""Tests for the shared hooks_util helper (upsert_hook_entry)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from wade.config.hooks_util import upsert_hook_entry


class _Entry(BaseModel):
    command: str
    description: str = ""


class TestMalformedJSON:
    def test_raises_on_non_json_content(self, tmp_path: Path) -> None:
        hooks_file = tmp_path / "hooks.json"
        hooks_file.write_text("not-valid-json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            upsert_hook_entry(hooks_file, _Entry(command="cmd"), dedup_key="command")


class TestNonDictTopLevel:
    def test_raises_when_top_level_is_not_object(self, tmp_path: Path) -> None:
        hooks_file = tmp_path / "hooks.json"
        hooks_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(ValueError, match="must contain a JSON object"):
            upsert_hook_entry(hooks_file, _Entry(command="cmd"), dedup_key="command")


class TestEnsurePathTraversal:
    def test_raises_when_intermediate_segment_is_not_dict(self, tmp_path: Path) -> None:
        hooks_file = tmp_path / "hooks.json"
        hooks_file.write_text(json.dumps({"hooks": "not-a-dict"}), encoding="utf-8")
        with pytest.raises(ValueError, match="non-object data"):
            upsert_hook_entry(
                hooks_file,
                _Entry(command="cmd"),
                dedup_key="command",
                ensure_path=["hooks", "preToolUse"],
            )

    def test_creates_intermediate_dicts_and_appends(self, tmp_path: Path) -> None:
        hooks_file = tmp_path / "sub" / "hooks.json"
        upsert_hook_entry(
            hooks_file,
            _Entry(command="cmd"),
            dedup_key="command",
            ensure_path=["hooks", "preToolUse"],
        )
        assert hooks_file.is_file()
        data = json.loads(hooks_file.read_text(encoding="utf-8"))
        assert data["hooks"]["preToolUse"] == [{"command": "cmd", "description": ""}]


class TestNonListAtFinalKey:
    def test_raises_when_list_key_is_not_a_list(self, tmp_path: Path) -> None:
        hooks_file = tmp_path / "hooks.json"
        hooks_file.write_text(json.dumps({"command": "not-a-list"}), encoding="utf-8")
        with pytest.raises(ValueError, match="non-list data"):
            upsert_hook_entry(hooks_file, _Entry(command="cmd"), dedup_key="command")


class TestDedup:
    def test_dedup_prevents_duplicate_append(self, tmp_path: Path) -> None:
        hooks_file = tmp_path / "hooks.json"
        existing = {"command": [{"command": "cmd", "description": ""}]}
        hooks_file.write_text(json.dumps(existing), encoding="utf-8")

        upsert_hook_entry(hooks_file, _Entry(command="cmd"), dedup_key="command")

        data = json.loads(hooks_file.read_text(encoding="utf-8"))
        assert len(data["command"]) == 1

    def test_dedup_without_defaults_does_not_write(self, tmp_path: Path) -> None:
        hooks_file = tmp_path / "hooks.json"
        original_content = json.dumps({"command": [{"command": "cmd", "description": ""}]})
        hooks_file.write_text(original_content, encoding="utf-8")

        upsert_hook_entry(hooks_file, _Entry(command="cmd"), dedup_key="command")

        # No top_level_defaults → function returns without writing
        assert hooks_file.read_text(encoding="utf-8") == original_content

    def test_dedup_with_top_level_defaults_writes_merged_defaults(self, tmp_path: Path) -> None:
        hooks_file = tmp_path / "hooks.json"
        existing = {"command": [{"command": "cmd", "description": ""}]}
        hooks_file.write_text(json.dumps(existing), encoding="utf-8")

        upsert_hook_entry(
            hooks_file,
            _Entry(command="cmd"),
            dedup_key="command",
            top_level_defaults={"version": 1},
        )

        data = json.loads(hooks_file.read_text(encoding="utf-8"))
        # Entry was not duplicated
        assert len(data["command"]) == 1
        # But top-level defaults were written back
        assert data["version"] == 1
