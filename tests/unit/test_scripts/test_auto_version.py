"""Release failures must never create an unsynchronized commit or tag."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import auto_version
import pytest


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "src/wade").mkdir(parents=True)
    (tmp_path / "src/wade/__init__.py").write_text('__version__ = "1.2.13"\n')
    (tmp_path / "pyproject.toml").write_text('version = "1.2.13"\n')
    (tmp_path / "uv.lock").write_text("existing lock\n")
    monkeypatch.setattr(auto_version, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(auto_version, "INIT_FILE", tmp_path / "src/wade/__init__.py")
    monkeypatch.setattr(auto_version, "PYPROJECT_FILE", tmp_path / "pyproject.toml")
    monkeypatch.setattr(auto_version, "LOCK_FILE", tmp_path / "uv.lock")
    monkeypatch.setattr(sys, "argv", ["auto_version.py", "patch", "--push"])
    return tmp_path


def test_lock_precedes_staging_tagging_and_push_and_selects_script_root(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UV_PROJECT", "/different/project")
    monkeypatch.setattr(auto_version, "is_git_repo", lambda: True)
    runner = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout=""))
    monkeypatch.setattr(auto_version.subprocess, "run", runner)
    auto_version.main()
    calls = runner.call_args_list
    commands = [c.args[0] for c in calls]
    lock_index = next(i for i, cmd in enumerate(commands) if cmd[0] == "uv")
    assert commands[lock_index] == ["uv", "lock", "--project", str(project)]
    assert calls[lock_index].kwargs == {"cwd": project, "check": True}
    assert commands[lock_index + 1] == [
        "git",
        "add",
        str(project / "src/wade/__init__.py"),
        str(project / "pyproject.toml"),
        str(project / "uv.lock"),
    ]
    assert commands[lock_index + 2 :] == [
        ["git", "commit", "-m", "chore: bump version to v1.2.14"],
        ["git", "tag", "-a", "v1.2.14", "-m", "Release v1.2.14"],
        ["git", "push"],
        ["git", "push", "--tags"],
    ]


@pytest.mark.parametrize(
    "failure", [FileNotFoundError("uv missing"), subprocess.CalledProcessError(7, "uv lock")]
)
def test_lock_failure_prevents_staging_changelog_commit_tag_and_push(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(auto_version, "is_git_repo", lambda: True)
    (project / "scripts").mkdir()
    (project / "scripts/changelog.py").write_text("raise AssertionError('must not run')\n")
    commands: list[list[str]] = []

    def runner(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[0] == "uv":
            assert 'version = "1.2.14"' in (project / "pyproject.toml").read_text()
            raise failure
        return subprocess.CompletedProcess(args, 0, stdout="")

    monkeypatch.setattr(auto_version.subprocess, "run", runner)
    with pytest.raises(SystemExit, match="1"):
        auto_version.main()
    assert [cmd[:2] for cmd in commands] == [["git", "diff"], ["uv", "lock"]]
    assert "no release commit/tag created" in capsys.readouterr().err


@pytest.mark.parametrize("push", [False, True])
def test_dry_run_does_not_run_commands_or_change_files(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    push: bool,
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["auto_version.py", "patch", "--dry-run"] + (["--push"] if push else [])
    )
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    runner = MagicMock()
    monkeypatch.setattr(auto_version.subprocess, "run", runner)
    auto_version.main()
    runner.assert_not_called()
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize("with_lock", [False, True])
def test_standalone_without_git_preserves_no_lock_compatibility(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_lock: bool,
) -> None:
    monkeypatch.setattr(auto_version, "is_git_repo", lambda: False)
    if not with_lock:
        (project / "uv.lock").unlink()
    runner = MagicMock()
    monkeypatch.setattr(auto_version.subprocess, "run", runner)
    auto_version.main()
    assert 'version = "1.2.14"' in (project / "pyproject.toml").read_text()
    if with_lock:
        runner.assert_called_once_with(
            ["uv", "lock", "--project", str(project)], cwd=project, check=True
        )
    else:
        runner.assert_not_called()


def test_missing_tracked_lock_fails_before_changing_release_files(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auto_version, "is_git_repo", lambda: True)
    (project / "uv.lock").unlink()
    runner = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout=""))
    monkeypatch.setattr(auto_version.subprocess, "run", runner)
    with pytest.raises(SystemExit, match="1"):
        auto_version.main()
    assert (project / "pyproject.toml").read_text() == 'version = "1.2.13"\n'
    assert [c.args[0][:2] for c in runner.call_args_list] == [["git", "diff"], ["git", "ls-files"]]
