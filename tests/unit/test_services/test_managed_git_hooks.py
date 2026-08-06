"""Tests for the #352 managed pre-commit / commit-msg git hooks.

Covers install + worktree scoping, the block/allow gate behavior via a real
``git commit``, per-hook chaining to a pre-existing user hook, the #349
``.chain`` → ``.chain-pre-push`` migration, and that installing several hooks in
one batch never cross-wires their ``.chain-*`` files.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from wade.git import repo as git_repo
from wade.skills.installer import (
    build_commit_msg_hook_script,
    build_pre_commit_hook_script,
    install_worktree_git_hooks,
    reconcile_worktree_git_hooks,
)

_NOOP_PRE_PUSH = "#!/usr/bin/env bash\nexit 0\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _main_and_worktree(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-b", "main")
    _git(main, "config", "user.email", "t@e.st")
    _git(main, "config", "user.name", "T")
    (main / "a.txt").write_text("a\n")
    _git(main, "add", "-A")
    _git(main, "commit", "-m", "chore: init")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-b", "feat/1-x", str(wt))
    return main, wt


def _commit(wt: Path, filename: str, message: str) -> subprocess.CompletedProcess[str]:
    """Stage a new file and attempt a commit; return the completed process."""
    (wt / filename).write_text("x\n")
    _git(wt, "add", "-A")
    return subprocess.run(
        ["git", "-C", str(wt), "commit", "-m", message],
        capture_output=True,
        text=True,
    )


class TestInstallScoping:
    def test_installs_worktree_scoped_hooks(self, tmp_path: Path) -> None:
        _main, wt = _main_and_worktree(tmp_path)
        ok = install_worktree_git_hooks(
            wt,
            {
                "pre-commit": build_pre_commit_hook_script("true", None),
                "commit-msg": build_commit_msg_hook_script(),
            },
        )
        assert ok is True
        assert (wt / ".wade" / "githooks" / "pre-commit").is_file()
        assert (wt / ".wade" / "githooks" / "commit-msg").is_file()
        assert os.access(wt / ".wade" / "githooks" / "pre-commit", os.X_OK)
        assert git_repo.get_config_value(wt, "core.hooksPath", worktree=True) == ".wade/githooks"

    def test_not_leaked_to_main_or_sibling(self, tmp_path: Path) -> None:
        main, wt = _main_and_worktree(tmp_path)
        sib = tmp_path / "sib"
        _git(main, "worktree", "add", "-b", "feat/2-y", str(sib))
        install_worktree_git_hooks(wt, {"commit-msg": build_commit_msg_hook_script()})
        assert git_repo.get_config_value(main, "core.hooksPath", worktree=True) is None
        assert git_repo.get_config_value(main, "core.hooksPath") is None
        assert git_repo.get_config_value(sib, "core.hooksPath", worktree=True) is None

    def test_empty_hooks_installs_nothing(self, tmp_path: Path) -> None:
        _main, wt = _main_and_worktree(tmp_path)
        assert install_worktree_git_hooks(wt, {}) is False
        assert git_repo.get_config_value(wt, "core.hooksPath", worktree=True) is None


class TestPreCommitGate:
    def test_blocks_when_lint_fails(self, tmp_path: Path) -> None:
        _main, wt = _main_and_worktree(tmp_path)
        install_worktree_git_hooks(wt, {"pre-commit": build_pre_commit_hook_script("false", None)})
        r = _commit(wt, "b.txt", "chore: b")
        assert r.returncode != 0
        assert "pre-commit" in (r.stdout + r.stderr)

    def test_blocks_when_test_fails(self, tmp_path: Path) -> None:
        _main, wt = _main_and_worktree(tmp_path)
        # Lint passes, test fails — the test step must still block.
        install_worktree_git_hooks(
            wt, {"pre-commit": build_pre_commit_hook_script("true", "false")}
        )
        r = _commit(wt, "b.txt", "chore: b")
        assert r.returncode != 0

    def test_allows_when_clean(self, tmp_path: Path) -> None:
        _main, wt = _main_and_worktree(tmp_path)
        install_worktree_git_hooks(wt, {"pre-commit": build_pre_commit_hook_script("true", "true")})
        r = _commit(wt, "b.txt", "chore: b")
        assert r.returncode == 0, r.stderr

    def test_lint_command_with_single_quote_bakes_valid_script(self, tmp_path: Path) -> None:
        # A command containing a single quote must not break the single-quoted
        # shell literal it is baked into — it should still parse and run.
        _main, wt = _main_and_worktree(tmp_path)
        install_worktree_git_hooks(
            wt, {"pre-commit": build_pre_commit_hook_script("echo 'it works'", None)}
        )
        r = _commit(wt, "b.txt", "chore: b")
        assert r.returncode == 0, r.stderr

    def test_lint_command_receives_no_stray_positional(self, tmp_path: Path) -> None:
        # A whole-repo lint command must run exactly as configured (baked, run via
        # `bash -c`), so a multi-word command with flags works unmodified.
        _main, wt = _main_and_worktree(tmp_path)
        install_worktree_git_hooks(
            wt, {"pre-commit": build_pre_commit_hook_script("test 1 -eq 1", None)}
        )
        r = _commit(wt, "b.txt", "chore: b")
        assert r.returncode == 0, r.stderr


class TestCommitMsgGate:
    def _install(self, wt: Path) -> None:
        install_worktree_git_hooks(wt, {"commit-msg": build_commit_msg_hook_script()})

    def test_blocks_non_conventional(self, tmp_path: Path) -> None:
        _main, wt = _main_and_worktree(tmp_path)
        self._install(wt)
        r = _commit(wt, "b.txt", "just some words")
        assert r.returncode != 0
        assert "Conventional Commit" in (r.stdout + r.stderr)

    def test_allows_conventional(self, tmp_path: Path) -> None:
        _main, wt = _main_and_worktree(tmp_path)
        self._install(wt)
        r = _commit(wt, "b.txt", "feat(hooks): add a thing")
        assert r.returncode == 0, r.stderr

    def test_blocks_untyped_subject_with_breaking_footer(self, tmp_path: Path) -> None:
        # A BREAKING CHANGE footer marks a typed commit as breaking; it does not
        # license an untyped subject, which must still be rejected.
        _main, wt = _main_and_worktree(tmp_path)
        self._install(wt)
        r = _commit(wt, "b.txt", "drop the old flag\n\nBREAKING CHANGE: removed --foo")
        assert r.returncode != 0
        assert "Conventional Commit" in (r.stdout + r.stderr)

    def test_allows_typed_subject_with_breaking_footer(self, tmp_path: Path) -> None:
        _main, wt = _main_and_worktree(tmp_path)
        self._install(wt)
        r = _commit(wt, "b.txt", "feat: add a thing\n\nBREAKING CHANGE: removed --foo")
        assert r.returncode == 0, r.stderr

    def test_allows_bang_breaking_subject(self, tmp_path: Path) -> None:
        _main, wt = _main_and_worktree(tmp_path)
        self._install(wt)
        r = _commit(wt, "b.txt", "feat!: breaking thing")
        assert r.returncode == 0, r.stderr


class TestOffByDefault:
    def test_no_hooks_no_config_change(self, tmp_path: Path) -> None:
        # With no hooks installed a plain commit is ungated.
        _main, wt = _main_and_worktree(tmp_path)
        r = _commit(wt, "b.txt", "literally anything")
        assert r.returncode == 0, r.stderr


class TestChaining:
    def _install_prior(self, main: Path, hook_name: str, body: str) -> Path:
        hooks = main / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        prior = hooks / hook_name
        prior.write_text(body)
        prior.chmod(0o755)
        return prior

    def test_preexisting_pre_commit_still_runs(self, tmp_path: Path) -> None:
        main, wt = _main_and_worktree(tmp_path)
        record = tmp_path / "prior_ran.txt"
        prior = self._install_prior(
            main, "pre-commit", f"#!/usr/bin/env bash\necho ran > '{record}'\nexit 0\n"
        )
        install_worktree_git_hooks(wt, {"pre-commit": build_pre_commit_hook_script("true", None)})
        chain = (wt / ".wade" / "githooks" / ".chain-pre-commit").read_text().strip()
        assert chain == str(prior.resolve())
        r = _commit(wt, "b.txt", "chore: b")
        assert r.returncode == 0, r.stderr
        assert record.read_text().strip() == "ran"

    def test_preexisting_pre_commit_can_still_block(self, tmp_path: Path) -> None:
        main, wt = _main_and_worktree(tmp_path)
        self._install_prior(main, "pre-commit", "#!/usr/bin/env bash\nexit 7\n")
        install_worktree_git_hooks(wt, {"pre-commit": build_pre_commit_hook_script("true", None)})
        # wade's own checks pass, but the chained prior fails → commit blocked.
        r = _commit(wt, "b.txt", "chore: b")
        assert r.returncode != 0

    def test_installing_multiple_hooks_does_not_cross_wire_chains(self, tmp_path: Path) -> None:
        main, wt = _main_and_worktree(tmp_path)
        prior_push = self._install_prior(main, "pre-push", "#!/usr/bin/env bash\nexit 0\n")
        prior_commit = self._install_prior(main, "pre-commit", "#!/usr/bin/env bash\nexit 0\n")
        install_worktree_git_hooks(
            wt,
            {
                "pre-push": "#!/usr/bin/env bash\nexit 0\n",
                "pre-commit": build_pre_commit_hook_script("true", None),
            },
        )
        assert (wt / ".wade" / "githooks" / ".chain-pre-push").read_text().strip() == str(
            prior_push.resolve()
        )
        assert (wt / ".wade" / "githooks" / ".chain-pre-commit").read_text().strip() == str(
            prior_commit.resolve()
        )


class TestReconcileDisable:
    """Re-bootstrapping a reused worktree must honor a gate turned off since a
    prior session — the core fix for the 'disable does nothing' defect."""

    def test_disabling_gate_neutralizes_stale_hook(self, tmp_path: Path) -> None:
        _main, wt = _main_and_worktree(tmp_path)
        # Session 1: pre-commit gate ON (a failing lint) alongside pre-push.
        reconcile_worktree_git_hooks(
            wt,
            {
                "pre-push": _NOOP_PRE_PUSH,
                "pre-commit": build_pre_commit_hook_script("false", None),
            },
        )
        assert _commit(wt, "b.txt", "chore: b").returncode != 0  # blocked

        # Session 2: pre-commit gate removed from config — must stop firing.
        reconcile_worktree_git_hooks(wt, {"pre-push": _NOOP_PRE_PUSH})
        assert _commit(wt, "c.txt", "chore: c").returncode == 0

    def test_uninstall_when_nothing_desired_restores_git_hooks(self, tmp_path: Path) -> None:
        _main, wt = _main_and_worktree(tmp_path)
        reconcile_worktree_git_hooks(
            wt, {"pre-commit": build_pre_commit_hook_script("false", None)}
        )
        assert git_repo.get_config_value(wt, "core.hooksPath", worktree=True) == ".wade/githooks"

        # Everything off → full uninstall: hooksPath unset, scripts gone.
        assert reconcile_worktree_git_hooks(wt, {}) is False
        assert git_repo.get_config_value(wt, "core.hooksPath", worktree=True) is None
        assert not (wt / ".wade" / "githooks" / "pre-commit").exists()
        assert _commit(wt, "b.txt", "anything at all goes now").returncode == 0

    def test_uninstall_rolls_back_extension_when_last(self, tmp_path: Path) -> None:
        # Uninstall leaves no persistent config behind: the repo-wide
        # extensions.worktreeConfig that install enabled is rolled back once no
        # worktree still uses a worktree-scoped hooksPath (symmetry with the
        # install-failure rollback).
        _main, wt = _main_and_worktree(tmp_path)
        reconcile_worktree_git_hooks(wt, {"pre-commit": build_pre_commit_hook_script("true", None)})
        assert git_repo.get_config_value(wt, "extensions.worktreeConfig") == "true"

        assert reconcile_worktree_git_hooks(wt, {}) is False
        assert git_repo.get_config_value(wt, "extensions.worktreeConfig") is None

    def test_uninstall_keeps_extension_when_sibling_relies_on_it(self, tmp_path: Path) -> None:
        # The extension is repo-WIDE. If a sibling worktree still carries a
        # worktree-scoped hooksPath, disabling it would silently stop git reading
        # that sibling's config — so uninstall must leave it enabled.
        main, wt = _main_and_worktree(tmp_path)
        sib = tmp_path / "sib"
        _git(main, "worktree", "add", "-b", "feat/2-y", str(sib))
        reconcile_worktree_git_hooks(wt, {"pre-commit": build_pre_commit_hook_script("true", None)})
        reconcile_worktree_git_hooks(sib, {"commit-msg": build_commit_msg_hook_script()})

        assert reconcile_worktree_git_hooks(wt, {}) is False
        # Extension stays; the sibling's worktree-scoped hooksPath is still read.
        assert git_repo.get_config_value(main, "extensions.worktreeConfig") == "true"
        assert git_repo.get_config_value(sib, "core.hooksPath", worktree=True) == ".wade/githooks"

    def test_disabled_gate_preserves_prior_via_passthrough(self, tmp_path: Path) -> None:
        main, wt = _main_and_worktree(tmp_path)
        record = tmp_path / "prior_ran.txt"
        hooks = main / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        prior = hooks / "pre-commit"
        prior.write_text(f"#!/usr/bin/env bash\necho ran > '{record}'\nexit 0\n")
        prior.chmod(0o755)

        # Session 1: wade pre-commit gate ON (chains to the user's prior).
        reconcile_worktree_git_hooks(
            wt,
            {"pre-push": _NOOP_PRE_PUSH, "pre-commit": build_pre_commit_hook_script("true", None)},
        )
        # Session 2: disable the wade gate — the user's prior must still run.
        reconcile_worktree_git_hooks(wt, {"pre-push": _NOOP_PRE_PUSH})
        record.unlink(missing_ok=True)
        r = _commit(wt, "b.txt", "chore: b")
        assert r.returncode == 0, r.stderr
        assert record.read_text().strip() == "ran"


class TestLegacyChainMigration:
    def test_old_chain_migrated_and_still_chains_pre_push(self, tmp_path: Path) -> None:
        main, wt = _main_and_worktree(tmp_path)
        # Simulate a worktree bootstrapped under #349: hooksPath already set, and
        # an old unsuffixed .chain recording the user's real prior pre-push.
        hooks = main / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        prior = hooks / "pre-push"
        prior.write_text("#!/usr/bin/env bash\nexit 0\n")
        prior.chmod(0o755)

        githooks = wt / ".wade" / "githooks"
        githooks.mkdir(parents=True, exist_ok=True)
        (githooks / ".chain").write_text(str(prior.resolve()) + "\n")
        git_repo.set_config_value(wt, "extensions.worktreeConfig", "true")
        git_repo.set_config_value(wt, "core.hooksPath", ".wade/githooks", worktree=True)

        # Upgrade: reinstall pre-push via the new batch installer.
        install_worktree_git_hooks(wt, {"pre-push": "#!/usr/bin/env bash\nexit 0\n"})

        # The old .chain was migrated (renamed), not orphaned or self-chained.
        assert not (githooks / ".chain").exists()
        migrated = (githooks / ".chain-pre-push").read_text().strip()
        assert migrated == str(prior.resolve())
        assert "githooks/pre-push" not in migrated
