"""Tests for the per-worktree pre-push backstop install + hook behavior (#349)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from wade.git import repo as git_repo
from wade.skills.installer import install_pre_push_backstop
from wade.utils import markers

_ZERO = "0" * 40


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _main_and_worktree(tmp_path: Path) -> tuple[Path, Path, str]:
    """Build a main checkout + a linked worktree on ``feat/1-x``."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-b", "main")
    _git(main, "config", "user.email", "t@e.st")
    _git(main, "config", "user.name", "T")
    (main / "a.txt").write_text("a\n")
    _git(main, "add", "-A")
    _git(main, "commit", "-m", "a")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-b", "feat/1-x", str(wt))
    return main, wt, "feat/1-x"


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD")


def _run_hook(wt: Path, stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(wt / ".wade" / "githooks" / "pre-push"), "origin", "https://example/repo.git"],
        cwd=str(wt),
        input=stdin,
        capture_output=True,
        text=True,
    )


class TestInstallScoping:
    def test_installs_worktree_scoped_hook(self, tmp_path: Path) -> None:
        _main, wt, _branch = _main_and_worktree(tmp_path)
        assert install_pre_push_backstop(wt) is True

        hook = wt / ".wade" / "githooks" / "pre-push"
        assert hook.is_file()
        assert os.access(hook, os.X_OK)
        assert git_repo.get_config_value(wt, "core.hooksPath", worktree=True) == ".wade/githooks"

    def test_not_leaked_to_main_checkout(self, tmp_path: Path) -> None:
        main, wt, _branch = _main_and_worktree(tmp_path)
        install_pre_push_backstop(wt)
        # The main checkout must not pick up the worktree-scoped hooksPath.
        assert git_repo.get_config_value(main, "core.hooksPath") is None
        assert git_repo.get_config_value(main, "core.hooksPath", worktree=True) is None

    def test_not_leaked_to_sibling_worktree(self, tmp_path: Path) -> None:
        main, wt, _branch = _main_and_worktree(tmp_path)
        sib = tmp_path / "sib"
        _git(main, "worktree", "add", "-b", "feat/2-y", str(sib))
        install_pre_push_backstop(wt)
        assert git_repo.get_config_value(sib, "core.hooksPath", worktree=True) is None
        assert git_repo.get_config_value(sib, "core.hooksPath") is None


class TestHookMarkerGate:
    def test_refuses_without_marker(self, tmp_path: Path) -> None:
        _main, wt, branch = _main_and_worktree(tmp_path)
        install_pre_push_backstop(wt)
        head = _head(wt)
        stdin = f"refs/heads/{branch} {head} refs/heads/{branch} {_ZERO}\n"
        r = _run_hook(wt, stdin)
        assert r.returncode == 1
        assert "done@" in r.stderr

    def test_allows_with_marker(self, tmp_path: Path) -> None:
        _main, wt, branch = _main_and_worktree(tmp_path)
        install_pre_push_backstop(wt)
        head = _head(wt)
        markers.write_marker(wt, "done", head)
        stdin = f"refs/heads/{branch} {head} refs/heads/{branch} {_ZERO}\n"
        r = _run_hook(wt, stdin)
        assert r.returncode == 0

    def test_deletion_is_skipped(self, tmp_path: Path) -> None:
        _main, wt, branch = _main_and_worktree(tmp_path)
        install_pre_push_backstop(wt)
        # local sha all-zero = branch deletion → nothing pushed, no marker needed.
        stdin = f"refs/heads/{branch} {_ZERO} refs/heads/{branch} {_head(wt)}\n"
        r = _run_hook(wt, stdin)
        assert r.returncode == 0

    def test_other_ref_not_gated(self, tmp_path: Path) -> None:
        _main, wt, _branch = _main_and_worktree(tmp_path)
        install_pre_push_backstop(wt)
        head = _head(wt)
        # A ref that isn't the session branch (e.g. a tag push) passes ungated.
        stdin = f"refs/tags/v1 {head} refs/tags/v1 {_ZERO}\n"
        r = _run_hook(wt, stdin)
        assert r.returncode == 0


class TestChaining:
    def _install_prior_common_hook(self, main: Path, record: Path) -> Path:
        hooks = main / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        prior = hooks / "pre-push"
        prior.write_text(f"#!/usr/bin/env bash\ncat > '{record}'\nexit 0\n")
        prior.chmod(0o755)
        return prior

    def test_captures_and_chains_to_preexisting_hook(self, tmp_path: Path) -> None:
        main, wt, branch = _main_and_worktree(tmp_path)
        record = tmp_path / "chained_stdin.txt"
        prior = self._install_prior_common_hook(main, record)

        install_pre_push_backstop(wt)
        chain = (wt / ".wade" / "githooks" / ".chain").read_text().strip()
        assert chain == str(prior.resolve())

        head = _head(wt)
        markers.write_marker(wt, "done", head)
        stdin = f"refs/heads/{branch} {head} refs/heads/{branch} {_ZERO}\n"
        r = _run_hook(wt, stdin)
        assert r.returncode == 0
        # The chained hook ran and saw the buffered stdin.
        assert record.read_text() == stdin

    def test_multi_ref_push_forwards_all_buffered_stdin(self, tmp_path: Path) -> None:
        main, wt, branch = _main_and_worktree(tmp_path)
        record = tmp_path / "chained_stdin.txt"
        self._install_prior_common_hook(main, record)
        install_pre_push_backstop(wt)

        head = _head(wt)
        markers.write_marker(wt, "done", head)
        # A `git push --all`-style multi-line stdin: the session branch + another
        # ref. Only the session branch is gated; the full buffer must be forwarded.
        stdin = (
            f"refs/heads/{branch} {head} refs/heads/{branch} {_ZERO}\n"
            f"refs/heads/other {head} refs/heads/other {_ZERO}\n"
        )
        r = _run_hook(wt, stdin)
        assert r.returncode == 0
        assert record.read_text() == stdin

    def test_reinstall_does_not_self_chain(self, tmp_path: Path) -> None:
        main, wt, _branch = _main_and_worktree(tmp_path)
        record = tmp_path / "chained_stdin.txt"
        prior = self._install_prior_common_hook(main, record)

        install_pre_push_backstop(wt)
        first = (wt / ".wade" / "githooks" / ".chain").read_text().strip()
        # Re-run bootstrap install against the already-managed worktree.
        install_pre_push_backstop(wt)
        second = (wt / ".wade" / "githooks" / ".chain").read_text().strip()
        # The chain target is captured once and never re-points at wade's own hook.
        assert first == second == str(prior.resolve())
        assert "githooks/pre-push" not in second


class TestGracefulDegrade:
    def test_skips_when_worktree_config_unsupported(self, tmp_path: Path, monkeypatch) -> None:
        _main, wt, _branch = _main_and_worktree(tmp_path)
        # Simulate old/locked-down git: worktree-scoped config writes fail.
        monkeypatch.setattr(git_repo, "set_config_value", lambda *a, **k: False)
        # Must warn-and-skip (return False), never raise.
        assert install_pre_push_backstop(wt) is False
