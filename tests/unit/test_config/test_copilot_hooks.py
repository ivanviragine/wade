"""Tests for Copilot hook configuration for plan sessions."""

from __future__ import annotations

import json
from pathlib import Path

from wade.config.copilot_hooks import configure_plan_hooks


class TestCopilotPlanHooks:
    def test_creates_hooks_json(self, tmp_path: Path) -> None:
        guard = tmp_path / "guard.py"
        guard.touch()
        configure_plan_hooks(tmp_path, guard)

        hooks_file = tmp_path / ".copilot" / "hooks.json"
        assert hooks_file.is_file()
        data = json.loads(hooks_file.read_text(encoding="utf-8"))
        assert len(data["preToolUse"]) == 1
        assert data["preToolUse"][0]["tools"] == ["edit", "create"]

    def test_idempotent(self, tmp_path: Path) -> None:
        guard = tmp_path / "guard.py"
        guard.touch()
        configure_plan_hooks(tmp_path, guard)
        configure_plan_hooks(tmp_path, guard)

        hooks_file = tmp_path / ".copilot" / "hooks.json"
        data = json.loads(hooks_file.read_text(encoding="utf-8"))
        assert len(data["preToolUse"]) == 1

    def test_preserves_existing(self, tmp_path: Path) -> None:
        hooks_file = tmp_path / ".copilot" / "hooks.json"
        hooks_file.parent.mkdir(parents=True)
        hooks_file.write_text(json.dumps({"other": "setting"}), encoding="utf-8")

        guard = tmp_path / "guard.py"
        guard.touch()
        configure_plan_hooks(tmp_path, guard)

        data = json.loads(hooks_file.read_text(encoding="utf-8"))
        assert data["other"] == "setting"
        assert len(data["preToolUse"]) == 1
