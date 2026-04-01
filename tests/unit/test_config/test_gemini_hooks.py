"""Tests for Gemini hook configuration for plan and worktree sessions."""

from __future__ import annotations

import json
from pathlib import Path

from wade.config.gemini_hooks import configure_plan_hooks, configure_worktree_hooks


class TestGeminiWorktreeHooks:
    def test_creates_settings_json(self, tmp_path: Path) -> None:
        guard = tmp_path / "guard.py"
        guard.touch()
        configure_worktree_hooks(tmp_path, guard)

        settings_file = tmp_path / ".gemini" / "settings.json"
        assert settings_file.is_file()
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        # hooks must be an object keyed by event name
        assert isinstance(data["hooks"], dict)
        before_tool = data["hooks"]["BeforeTool"]
        assert len(before_tool) == 1
        entry = before_tool[0]
        assert entry["matcher"] == ".*"
        assert len(entry["hooks"]) == 1
        assert entry["hooks"][0]["type"] == "command"
        assert f"python3 {guard.resolve()}" in entry["hooks"][0]["command"]

    def test_idempotent(self, tmp_path: Path) -> None:
        guard = tmp_path / "guard.py"
        guard.touch()
        configure_worktree_hooks(tmp_path, guard)
        configure_worktree_hooks(tmp_path, guard)

        settings_file = tmp_path / ".gemini" / "settings.json"
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        assert len(data["hooks"]["BeforeTool"]) == 1

    def test_preserves_existing(self, tmp_path: Path) -> None:
        settings_file = tmp_path / ".gemini" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(json.dumps({"other": "setting"}), encoding="utf-8")

        guard = tmp_path / "guard.py"
        guard.touch()
        configure_worktree_hooks(tmp_path, guard)

        data = json.loads(settings_file.read_text(encoding="utf-8"))
        assert data["other"] == "setting"
        assert len(data["hooks"]["BeforeTool"]) == 1


class TestGeminiPlanHooks:
    def test_creates_settings_json(self, tmp_path: Path) -> None:
        guard = tmp_path / "guard.py"
        guard.touch()
        configure_plan_hooks(tmp_path, guard)

        settings_file = tmp_path / ".gemini" / "settings.json"
        assert settings_file.is_file()
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        # hooks must be an object keyed by event name
        assert isinstance(data["hooks"], dict)
        before_tool = data["hooks"]["BeforeTool"]
        assert len(before_tool) == 1
        entry = before_tool[0]
        assert entry["matcher"] == ".*"
        assert len(entry["hooks"]) == 1
        assert entry["hooks"][0]["type"] == "command"
        assert f"python3 {guard.resolve()}" in entry["hooks"][0]["command"]

    def test_idempotent(self, tmp_path: Path) -> None:
        guard = tmp_path / "guard.py"
        guard.touch()
        configure_plan_hooks(tmp_path, guard)
        configure_plan_hooks(tmp_path, guard)

        settings_file = tmp_path / ".gemini" / "settings.json"
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        assert len(data["hooks"]["BeforeTool"]) == 1

    def test_preserves_existing(self, tmp_path: Path) -> None:
        settings_file = tmp_path / ".gemini" / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(json.dumps({"other": "setting"}), encoding="utf-8")

        guard = tmp_path / "guard.py"
        guard.touch()
        configure_plan_hooks(tmp_path, guard)

        data = json.loads(settings_file.read_text(encoding="utf-8"))
        assert data["other"] == "setting"
        assert len(data["hooks"]["BeforeTool"]) == 1
