"""Tests for init service permissions helpers."""

from __future__ import annotations

from pathlib import Path

from wade.services.init_service import _build_permissions_commands, _detect_scripts


class TestDetectScripts:
    """Tests for _detect_scripts()."""

    def test_detects_sh_files(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "check.sh").write_text("#!/bin/bash\n")
        (scripts_dir / "fmt.sh").write_text("#!/bin/bash\n")
        (scripts_dir / "test.sh").write_text("#!/bin/bash\n")

        result = _detect_scripts(tmp_path)
        assert result == [
            "./scripts/check.sh *",
            "./scripts/fmt.sh *",
            "./scripts/test.sh *",
        ]

    def test_no_scripts_dir(self, tmp_path: Path) -> None:
        result = _detect_scripts(tmp_path)
        assert result == []

    def test_empty_scripts_dir(self, tmp_path: Path) -> None:
        (tmp_path / "scripts").mkdir()
        result = _detect_scripts(tmp_path)
        assert result == []

    def test_ignores_non_sh_files(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "check.sh").write_text("#!/bin/bash\n")
        (scripts_dir / "helper.py").write_text("print('hi')\n")
        (scripts_dir / "README.md").write_text("docs\n")

        result = _detect_scripts(tmp_path)
        assert result == ["./scripts/check.sh *"]


class TestBuildPermissionsCommands:
    """Tests for _build_permissions_commands()."""

    def test_includes_wade_and_scripts(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "check.sh").write_text("#!/bin/bash\n")

        result = _build_permissions_commands(tmp_path)
        assert result[0] == "wade *"
        assert "./scripts/check.sh *" in result

    def test_wade_only_without_scripts(self, tmp_path: Path) -> None:
        result = _build_permissions_commands(tmp_path)
        assert result == ["wade *"]
