"""Real-git release coherence without network dependencies or remote publication."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

SCRIPT_ROOT = Path(__file__).resolve().parents[2] / "scripts"


def run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["UV_PROJECT"] = str(repo)
    env["UV_PROJECT_ENVIRONMENT"] = str(repo / ".venv")
    env["UV_OFFLINE"] = "true"
    return subprocess.run(args, cwd=repo, env=env, text=True, capture_output=True, check=check)


@pytest.fixture
def release_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "release"
    (repo / "src/wade").mkdir(parents=True)
    (repo / "scripts").mkdir()
    for filename in ("auto_version.py", "changelog.py"):
        shutil.copy2(SCRIPT_ROOT / filename, repo / "scripts" / filename)
    (repo / "src/wade/__init__.py").write_text('__version__ = "1.2.12"\n')
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "wade-cli"\nversion = "1.2.12"\nrequires-python = ">=3.11"\n'
    )
    (repo / ".gitignore").write_text(".venv/\n")
    run(repo, "git", "init", "-b", "main")
    run(repo, "git", "config", "user.name", "Release test")
    run(repo, "git", "config", "user.email", "release@example.invalid")
    run(repo, "uv", "lock")
    run(repo, "git", "add", ".")
    run(repo, "git", "commit", "-m", "chore: initial release fixture")
    run(repo, "git", "tag", "-a", "v1.2.12", "-m", "Initial release")
    # Reproduce the previous automatic bump: tracked lock remains one version behind.
    (repo / "src/wade/__init__.py").write_text('__version__ = "1.2.13"\n')
    (repo / "pyproject.toml").write_text(
        (repo / "pyproject.toml").read_text().replace("1.2.12", "1.2.13")
    )
    (repo / "sentinel.txt").write_text("original\n")
    run(repo, "git", "add", "src", "pyproject.toml", "sentinel.txt")
    run(repo, "git", "commit", "-m", "fix: fixture preceding release")
    run(repo, "git", "clone", "--bare", str(repo), str(tmp_path / "origin.git"))
    run(repo, "git", "remote", "add", "origin", str(tmp_path / "origin.git"))
    return repo


def assert_release(repo: Path, version: str) -> None:
    def committed(path: str) -> str:
        return run(repo, "git", "show", f"HEAD:{path}").stdout

    assert committed("src/wade/__init__.py") == f'__version__ = "{version}"\n'
    assert tomllib.loads(committed("pyproject.toml"))["project"]["version"] == version
    packages = tomllib.loads(committed("uv.lock"))["package"]
    assert next(p["version"] for p in packages if p["name"] == "wade-cli") == version
    assert version in committed("CHANGELOG.md")
    assert run(repo, "git", "cat-file", "-t", f"v{version}").stdout.strip() == "tag"
    assert (
        run(repo, "git", "rev-parse", f"v{version}^{{}}").stdout
        == run(repo, "git", "rev-parse", "HEAD").stdout
    )


def test_stale_lock_and_consecutive_workflow_cycles(release_repo: Path) -> None:
    repo = release_repo
    for version in ("1.2.14", "1.2.15"):
        if version == "1.2.15":
            run(repo, "git", "commit", "--allow-empty", "-m", "fix: next release fixture")
        run(repo, "uv", "sync", "--all-extras")
        run(repo, "uv", "run", "python", "scripts/auto_version.py", "patch")
        assert_release(repo, version)
        assert run(repo, "git", "status", "--porcelain").stdout == ""
        run(repo, "git", "pull", "--rebase", "origin", "main")
        run(repo, "uv", "sync", "--locked", "--all-extras")
        assert run(repo, "git", "status", "--porcelain").stdout == ""


def test_unrelated_unstaged_edit_is_preserved(release_repo: Path) -> None:
    repo = release_repo
    (repo / "sentinel.txt").write_text("user edit\n")
    run(repo, sys.executable, "scripts/auto_version.py", "patch")
    assert_release(repo, "1.2.14")
    assert run(repo, "git", "status", "--porcelain").stdout == " M sentinel.txt\n"
    assert run(repo, "git", "show", "HEAD:sentinel.txt").stdout == "original\n"
    assert (repo / "sentinel.txt").read_text() == "user edit\n"


def test_unrelated_staged_edit_rejected_before_writes(release_repo: Path) -> None:
    repo = release_repo
    (repo / "sentinel.txt").write_text("user edit\n")
    run(repo, "git", "add", "sentinel.txt")
    before = {
        p: (repo / p).read_bytes() for p in ("pyproject.toml", "uv.lock", "src/wade/__init__.py")
    }
    head = run(repo, "git", "rev-parse", "HEAD").stdout
    result = run(repo, sys.executable, "scripts/auto_version.py", "patch", check=False)
    assert result.returncode != 0
    assert "staged" in result.stderr.lower()
    assert run(repo, "git", "rev-parse", "HEAD").stdout == head
    assert not run(repo, "git", "tag", "--list", "v1.2.14").stdout
    assert all((repo / p).read_bytes() == content for p, content in before.items())
    assert run(repo, "git", "diff", "--cached", "--name-only").stdout == "sentinel.txt\n"


def test_direct_dry_run_push_leaves_everything_unchanged(release_repo: Path) -> None:
    repo = release_repo
    before = run(repo, "git", "status", "--porcelain").stdout
    head = run(repo, "git", "rev-parse", "HEAD").stdout
    files = {
        p: (repo / p).read_bytes() for p in ("pyproject.toml", "uv.lock", "src/wade/__init__.py")
    }
    run(repo, sys.executable, "scripts/auto_version.py", "patch", "--dry-run", "--push")
    assert run(repo, "git", "rev-parse", "HEAD").stdout == head
    assert run(repo, "git", "status", "--porcelain").stdout == before
    assert not run(repo, "git", "tag", "--list", "v1.2.14").stdout
    assert all((repo / p).read_bytes() == content for p, content in files.items())


def test_lock_failure_keeps_head_index_tags_and_stops_push(
    release_repo: Path,
    tmp_path: Path,
) -> None:
    repo = release_repo
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "uv").write_text("#!/bin/sh\necho deliberate-lock-failure >&2\nexit 7\n")
    (shim / "uv").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(shim) + os.pathsep + env["PATH"]
    head = run(repo, "git", "rev-parse", "HEAD").stdout
    tags = run(repo, "git", "tag").stdout
    index = run(repo, "git", "diff", "--cached").stdout
    result = subprocess.run(
        [sys.executable, "scripts/auto_version.py", "patch", "--push"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "deliberate-lock-failure" in result.stderr
    assert "no release commit/tag created" in result.stderr
    assert run(repo, "git", "rev-parse", "HEAD").stdout == head
    assert run(repo, "git", "tag").stdout == tags
    assert run(repo, "git", "diff", "--cached").stdout == index
    assert run(repo, "git", "ls-remote", "origin", "refs/tags/v1.2.14").stdout == ""
    # Failed synchronization leaves visible release-file edits for inspection.
    assert 'version = "1.2.14"' in (repo / "pyproject.toml").read_text()


def test_ambient_project_does_not_redirect_lock(release_repo: Path, tmp_path: Path) -> None:
    repo = release_repo
    other = tmp_path / "other"
    other.mkdir()
    (other / "pyproject.toml").write_text('[project]\nname = "other"\nversion = "9.0.0"\n')
    env = os.environ.copy()
    env["UV_PROJECT"] = str(other)
    env["UV_OFFLINE"] = "true"
    subprocess.run(
        [sys.executable, "scripts/auto_version.py", "patch"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert_release(repo, "1.2.14")
    assert not (other / "uv.lock").exists()


@pytest.mark.parametrize("staged", [False, True])
def test_missing_tracked_lock_cannot_be_deleted_from_release(
    release_repo: Path,
    staged: bool,
) -> None:
    repo = release_repo
    (repo / "uv.lock").unlink()
    if staged:
        run(repo, "git", "add", "uv.lock")
    head = run(repo, "git", "rev-parse", "HEAD").stdout
    result = run(repo, sys.executable, "scripts/auto_version.py", "patch", check=False)
    assert result.returncode != 0
    assert "tracked uv.lock is missing" in result.stderr
    assert run(repo, "git", "rev-parse", "HEAD").stdout == head
    assert (repo / "src/wade/__init__.py").read_text() == '__version__ = "1.2.13"\n'
    assert not run(repo, "git", "tag", "--list", "v1.2.14").stdout


def test_inherited_frozen_setting_cannot_create_stale_release(release_repo: Path) -> None:
    repo = release_repo
    env = os.environ.copy()
    env["UV_FROZEN"] = "true"
    env["UV_OFFLINE"] = "true"
    subprocess.run(
        [sys.executable, "scripts/auto_version.py", "patch"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert_release(repo, "1.2.14")
    assert run(repo, "git", "status", "--porcelain").stdout == ""
