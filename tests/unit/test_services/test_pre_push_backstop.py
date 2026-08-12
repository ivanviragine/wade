"""Tests for the per-worktree pre-push backstop install + hook behavior (#349)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from wade.git import repo as git_repo
from wade.git.hooks import install_worktree_git_hooks
from wade.utils import markers
from wade.utils.templates import load_hook_template

_ZERO = "0" * 40


def _install_pre_push(wt: Path) -> bool:
    return install_worktree_git_hooks(wt, {"pre-push": load_hook_template("pre-push")})


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
        assert _install_pre_push(wt) is True

        hook = wt / ".wade" / "githooks" / "pre-push"
        assert hook.is_file()
        assert os.access(hook, os.X_OK)
        assert git_repo.get_config_value(wt, "core.hooksPath", worktree=True) == ".wade/githooks"

    def test_not_leaked_to_main_checkout(self, tmp_path: Path) -> None:
        main, wt, _branch = _main_and_worktree(tmp_path)
        _install_pre_push(wt)
        # The main checkout must not pick up the worktree-scoped hooksPath.
        assert git_repo.get_config_value(main, "core.hooksPath") is None
        assert git_repo.get_config_value(main, "core.hooksPath", worktree=True) is None

    def test_not_leaked_to_sibling_worktree(self, tmp_path: Path) -> None:
        main, wt, _branch = _main_and_worktree(tmp_path)
        sib = tmp_path / "sib"
        _git(main, "worktree", "add", "-b", "feat/2-y", str(sib))
        _install_pre_push(wt)
        assert git_repo.get_config_value(sib, "core.hooksPath", worktree=True) is None
        assert git_repo.get_config_value(sib, "core.hooksPath") is None


class TestHookMarkerGate:
    def test_refuses_without_marker(self, tmp_path: Path) -> None:
        _main, wt, branch = _main_and_worktree(tmp_path)
        _install_pre_push(wt)
        head = _head(wt)
        stdin = f"refs/heads/{branch} {head} refs/heads/{branch} {_ZERO}\n"
        r = _run_hook(wt, stdin)
        assert r.returncode == 1
        assert "done@" in r.stderr

    def test_allows_with_marker(self, tmp_path: Path) -> None:
        _main, wt, branch = _main_and_worktree(tmp_path)
        _install_pre_push(wt)
        head = _head(wt)
        markers.write_marker(wt, "done", head)
        stdin = f"refs/heads/{branch} {head} refs/heads/{branch} {_ZERO}\n"
        r = _run_hook(wt, stdin)
        assert r.returncode == 0

    def test_deletion_is_skipped(self, tmp_path: Path) -> None:
        _main, wt, branch = _main_and_worktree(tmp_path)
        _install_pre_push(wt)
        # local sha all-zero = branch deletion → nothing pushed, no marker needed.
        stdin = f"refs/heads/{branch} {_ZERO} refs/heads/{branch} {_head(wt)}\n"
        r = _run_hook(wt, stdin)
        assert r.returncode == 0

    def test_refuses_when_marker_is_for_an_older_sha(self, tmp_path: Path) -> None:
        # The core invariant is sha-keying: a marker written for an earlier
        # commit must not authorize a push of a newer commit.
        _main, wt, branch = _main_and_worktree(tmp_path)
        _install_pre_push(wt)
        old_head = _head(wt)
        markers.write_marker(wt, "done", old_head)
        (wt / "b.txt").write_text("b\n")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-m", "b")
        new_head = _head(wt)
        stdin = f"refs/heads/{branch} {new_head} refs/heads/{branch} {_ZERO}\n"
        r = _run_hook(wt, stdin)
        assert r.returncode == 1
        assert new_head in r.stderr

    def test_other_ref_not_gated(self, tmp_path: Path) -> None:
        _main, wt, _branch = _main_and_worktree(tmp_path)
        _install_pre_push(wt)
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

    def _install_prior_hook_body(self, main: Path, body: str) -> Path:
        hooks = main / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        prior = hooks / "pre-push"
        prior.write_text(body)
        prior.chmod(0o755)
        return prior

    def test_chained_hook_that_ignores_stdin_still_allows(self, tmp_path: Path) -> None:
        # A prior hook that never reads stdin closes the pipe early. Under
        # `pipefail` the writer's SIGPIPE must NOT mask the hook's 0 exit.
        main, wt, branch = _main_and_worktree(tmp_path)
        self._install_prior_hook_body(main, "#!/usr/bin/env bash\nexit 0\n")
        _install_pre_push(wt)
        head = _head(wt)
        markers.write_marker(wt, "done", head)
        stdin = f"refs/heads/{branch} {head} refs/heads/{branch} {_ZERO}\n"
        r = _run_hook(wt, stdin)
        assert r.returncode == 0, r.stderr

    def test_chained_hook_failure_is_propagated(self, tmp_path: Path) -> None:
        # The mirror case: a non-stdin-reading hook that fails must propagate its
        # own status, not the writer's.
        main, wt, branch = _main_and_worktree(tmp_path)
        self._install_prior_hook_body(main, "#!/usr/bin/env bash\nexit 3\n")
        _install_pre_push(wt)
        head = _head(wt)
        markers.write_marker(wt, "done", head)
        stdin = f"refs/heads/{branch} {head} refs/heads/{branch} {_ZERO}\n"
        r = _run_hook(wt, stdin)
        assert r.returncode == 3

    def test_captures_and_chains_to_preexisting_hook(self, tmp_path: Path) -> None:
        main, wt, branch = _main_and_worktree(tmp_path)
        record = tmp_path / "chained_stdin.txt"
        prior = self._install_prior_common_hook(main, record)

        _install_pre_push(wt)
        chain = (wt / ".wade" / "githooks" / ".chain-pre-push").read_text().strip()
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
        _install_pre_push(wt)

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

        _install_pre_push(wt)
        first = (wt / ".wade" / "githooks" / ".chain-pre-push").read_text().strip()
        # Re-run bootstrap install against the already-managed worktree.
        _install_pre_push(wt)
        second = (wt / ".wade" / "githooks" / ".chain-pre-push").read_text().strip()
        # The chain target is captured once and never re-points at wade's own hook.
        assert first == second == str(prior.resolve())
        assert "githooks/pre-push" not in second


class TestGracefulDegrade:
    def test_skips_when_worktree_config_unsupported(self, tmp_path: Path, monkeypatch) -> None:
        _main, wt, _branch = _main_and_worktree(tmp_path)
        # Simulate old/locked-down git: worktree-scoped config writes fail.
        monkeypatch.setattr(git_repo, "set_config_value", lambda *a, **k: False)
        # Must warn-and-skip (return False), never raise.
        assert _install_pre_push(wt) is False

    def test_rolls_back_extension_when_hookspath_write_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Enabling extensions.worktreeConfig is a repo-WIDE change; if the
        # follow-up worktree hooksPath write fails, it must be rolled back so a
        # failed optional backstop leaves no persistent config behind.
        _main, wt, _branch = _main_and_worktree(tmp_path)
        assert git_repo.get_config_value(wt, "extensions.worktreeConfig") is None

        real_set = git_repo.set_config_value

        def fake_set(path: Path, key: str, value: str, *, worktree: bool = False) -> bool:
            if key == "core.hooksPath":
                return False  # simulate the worktree hooksPath write failing
            return real_set(path, key, value, worktree=worktree)

        monkeypatch.setattr(git_repo, "set_config_value", fake_set)
        assert _install_pre_push(wt) is False
        # The extension was unset (prior value was None), not left enabled.
        assert git_repo.get_config_value(wt, "extensions.worktreeConfig") is None

    def test_restores_prior_extension_when_hookspath_write_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Mirror of the unset case: when extensions.worktreeConfig was ALREADY
        # set (here "false"), the failed hooksPath write must restore that prior
        # value rather than unset the key.
        _main, wt, _branch = _main_and_worktree(tmp_path)
        assert git_repo.set_config_value(wt, "extensions.worktreeConfig", "false")

        real_set = git_repo.set_config_value

        def fake_set(path: Path, key: str, value: str, *, worktree: bool = False) -> bool:
            if key == "core.hooksPath":
                return False  # simulate the worktree hooksPath write failing
            return real_set(path, key, value, worktree=worktree)

        monkeypatch.setattr(git_repo, "set_config_value", fake_set)
        assert _install_pre_push(wt) is False
        # The extension was restored to its prior value, not unset.
        assert git_repo.get_config_value(wt, "extensions.worktreeConfig") == "false"
