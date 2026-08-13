"""Per-worktree git hook install/reconcile — deterministic, no AI reasoning."""

from __future__ import annotations

import os
from pathlib import Path

import structlog

from wade.git import repo as git_repo
from wade.git import worktree as git_worktree
from wade.utils.templates import load_hook_template

logger = structlog.get_logger()

# --- Per-worktree git-hook install (#349 pre-push backstop, reusable by #352) ---

# Relative to the worktree top. ``core.hooksPath`` is set to this dir per-worktree
# so a relative path resolves against each working tree's top level and cannot
# leak to the main checkout or sibling worktrees. ``.wade/`` is already gitignored
# and flagged-if-tracked, so nothing here appears as untracked or gets committed.
WADE_GITHOOKS_DIR = ".wade/githooks"
# Legacy single-hook chain file written by the #349 install (pre-push only).
# #352 made chain files **per-hook** (``.chain-<hook_name>``) so installing
# multiple hooks can't cross-wire their prior-hook targets; a worktree upgraded
# from #349 still has this file, holding the user's real prior pre-push, and is
# migrated to ``.chain-pre-push`` before any detection runs.
WADE_HOOK_LEGACY_CHAIN_FILE = f"{WADE_GITHOOKS_DIR}/.chain"


def _chain_file(hook_name: str) -> str:
    """Per-hook chain file recording that hook's captured prior (or absence).

    Per-hook (``.chain-<hook_name>``) so pre-push / pre-commit / commit-msg each
    record their own prior independently — a single shared ``.chain`` would let
    the first-installed hook's target be read by every hook's template.
    """
    return f"{WADE_GITHOOKS_DIR}/.chain-{hook_name}"


def _migrate_legacy_chain(worktree_path: Path) -> None:
    """Rename a #349 unsuffixed ``.chain`` to ``.chain-pre-push`` before detection.

    A worktree bootstrapped under old wade already has ``core.hooksPath`` set and
    an unsuffixed ``.chain`` holding the user's real prior ``pre-push``. Renaming
    it up front (only when ``.chain-pre-push`` does not already exist) keeps that
    prior chained: without this the user's prior pre-push is orphaned, or wade's
    own installed script is re-misdetected as the prior and self-chains. Runs
    before any :func:`_capture_prior_hook`, so the migrated file short-circuits
    re-capture for the pre-push hook.
    """
    legacy = worktree_path / WADE_HOOK_LEGACY_CHAIN_FILE
    target = worktree_path / _chain_file("pre-push")
    if legacy.is_file() and not target.exists():
        try:
            legacy.rename(target)
            logger.debug("skills.githook_chain_migrated", path=str(worktree_path))
        except OSError:
            logger.warning("skills.githook_chain_migrate_failed", path=str(worktree_path))


def _capture_prior_hook(worktree_path: Path, hook_name: str) -> None:
    """Persist ``hook_name``'s pre-existing hook to its ``.chain-<hook_name>`` file.

    Idempotent per hook: if the chain file already exists (captured on a prior
    install, or migrated from #349's ``.chain``) this is a no-op, so a bootstrap
    re-run never re-points the target at wade's own just-written script or drops
    a captured original. Must be called for **every** hook being installed
    *before* ``core.hooksPath`` is set — once wade owns the merged
    ``core.hooksPath``, :func:`_detect_prior_hook` can no longer see a user's
    *custom* repo-level hooksPath.
    """
    if (worktree_path / _chain_file(hook_name)).exists():
        return
    chain_target = _detect_prior_hook(worktree_path, hook_name)
    if chain_target:
        try:
            (worktree_path / _chain_file(hook_name)).write_text(
                chain_target + "\n", encoding="utf-8"
            )
        except OSError:
            logger.warning("skills.githook_chain_write_failed", path=str(worktree_path))


def _detect_prior_hook(worktree_path: Path, hook_name: str) -> str | None:
    """Return the absolute path of a pre-existing ``hook_name`` hook, or None.

    ``core.hooksPath`` *replaces* ``.git/hooks``, so before we point it at
    ``.wade/githooks`` we must find whatever git would run today and chain to it.
    Precedence mirrors git's: a prior user-set ``core.hooksPath`` wins over the
    common-dir ``hooks/`` directory. Called only on first install (before we set
    our own worktree ``core.hooksPath``), so the merged ``core.hooksPath`` it
    reads is genuinely the user's, not ours.
    """
    candidates: list[Path] = []

    prior_hooks_path = git_repo.get_config_value(worktree_path, "core.hooksPath")
    if prior_hooks_path and prior_hooks_path != WADE_GITHOOKS_DIR:
        prior_dir = Path(prior_hooks_path)
        if not prior_dir.is_absolute():
            prior_dir = worktree_path / prior_dir
        candidates.append(prior_dir / hook_name)

    common_dir = git_repo.get_git_common_dir(worktree_path)
    if common_dir:
        common_path = Path(common_dir)
        if not common_path.is_absolute():
            common_path = worktree_path / common_path
        candidates.append(common_path / "hooks" / hook_name)

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return None


def install_worktree_git_hooks(worktree_path: Path, hooks: dict[str, str]) -> bool:
    """Install one or more per-worktree git hooks via ``core.hooksPath`` in one batch.

    ``hooks`` maps ``hook_name`` → resolved script body. Installing the whole set
    in a single call is what keeps per-hook chaining correct (#352):

    1. Migrate a #349 unsuffixed ``.chain`` to ``.chain-pre-push`` (once).
    2. Capture **every** prior hook to its ``.chain-<hook_name>`` file *before*
       any wade config write — so :func:`_detect_prior_hook` reads the user's
       config, not a ``core.hooksPath`` wade set for an earlier hook in the set.
    3. Write all hook scripts to ``<worktree>/.wade/githooks/<hook_name>`` (``+x``).
       Always refreshed so a hook body can never drift from the installed wade.
    4. Enable ``extensions.worktreeConfig`` (repo-level, idempotent) and set
       ``core.hooksPath .wade/githooks`` in the ``--worktree`` scope **once**.

    **Graceful degrade**: worktree-scoped config needs git ≥ 2.20. If enabling
    the extension or setting the worktree ``core.hooksPath`` fails for any reason
    (old git, restricted config), the function warns via the logger, leaves the
    session's Python ``done`` gates as the sole enforcement, and returns
    ``False`` — it never raises, so bootstrap can't crash over optional hooks.
    Hook scripts written before a failed hooksPath set are inert (git ignores
    ``.wade/githooks`` without the hooksPath), so they need no cleanup; only the
    repo-wide extension is rolled back.

    Returns True when the worktree hooksPath is active, False when install had to
    be skipped (or ``hooks`` was empty).
    """
    if not hooks:
        return False

    hooks_dir = worktree_path / WADE_GITHOOKS_DIR
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("skills.githook_dir_failed", path=str(hooks_dir))
        return False

    # (1) + (2): migrate the legacy chain, then capture every prior hook BEFORE
    # writing scripts (so detection can't pick up our own just-written file) and
    # BEFORE setting our worktree core.hooksPath (so the read reflects the user's
    # prior config, for the whole set at once).
    _migrate_legacy_chain(worktree_path)
    for hook_name in hooks:
        _capture_prior_hook(worktree_path, hook_name)

    # (3): write all hook scripts.
    for hook_name, script_body in hooks.items():
        hook_path = hooks_dir / hook_name
        try:
            hook_path.write_text(script_body, encoding="utf-8")
            hook_path.chmod(0o755)
        except OSError:
            logger.warning("skills.githook_write_failed", path=str(hook_path))
            return False

    # (4): enable the worktree-config extension (repo-level), then set the
    # worktree hooksPath. Order matters: --worktree writes require the extension
    # first. Capture the prior extension value first so a failed hooksPath write
    # can roll the extension back: leaving it enabled is a persistent, repo-WIDE
    # change (git then reads config.worktree for every worktree) caused by
    # optional hooks that did not install.
    prior_worktree_config = git_repo.get_config_value(worktree_path, "extensions.worktreeConfig")
    if not git_repo.set_config_value(worktree_path, "extensions.worktreeConfig", "true"):
        logger.warning("skills.worktree_config_unsupported", path=str(worktree_path))
        return False
    if not git_repo.set_config_value(
        worktree_path, "core.hooksPath", WADE_GITHOOKS_DIR, worktree=True
    ):
        logger.warning("skills.worktree_hookspath_failed", path=str(worktree_path))
        # Undo the repo-wide extension we just enabled so a failed optional
        # install leaves no persistent config change behind.
        if prior_worktree_config is None:
            git_repo.unset_config_value(worktree_path, "extensions.worktreeConfig")
        else:
            git_repo.set_config_value(
                worktree_path, "extensions.worktreeConfig", prior_worktree_config
            )
        return False

    logger.debug("skills.githooks_installed", hooks=sorted(hooks), path=str(worktree_path))
    return True


# The full namespace of git hooks wade may manage per worktree. Reconciliation
# (:func:`reconcile_worktree_git_hooks`) walks this set so a hook whose config
# gate was turned off since a prior bootstrap is neutralized, not left firing.
_MANAGED_GITHOOK_NAMES: tuple[str, ...] = ("pre-push", "pre-commit", "commit-msg")


def _passthrough_hook_script(hook_name: str) -> str:
    """A wade hook body that only chains to a captured prior (its gate disabled).

    Written in place of a stale gate when its ``.wade.yml`` toggle is turned off
    but ``core.hooksPath`` stays wade-managed for *other* hooks: it drops wade's
    gate yet still runs any pre-existing user hook of the same name (recorded in
    ``.chain-<hook_name>``), so disabling a wade gate never silently shadows a
    user's own hook.
    """
    chain_file = _chain_file(hook_name)
    return (
        "#!/usr/bin/env bash\n"
        f"# wade passthrough for '{hook_name}' — its wade quality gate is disabled in\n"
        "# .wade.yml, but core.hooksPath is wade-managed for other hooks, so this shim\n"
        "# preserves any pre-existing user hook of the same name.\n"
        "set -uo pipefail\n"
        f'chain_file="{chain_file}"\n'
        'if [[ -f "$chain_file" ]]; then\n'
        '  chained="$(cat "$chain_file")"\n'
        '  if [[ -n "$chained" && -x "$chained" ]]; then\n'
        '    "$chained" "$@"\n'
        "    exit $?\n"
        "  fi\n"
        "fi\n"
        "exit 0\n"
    )


def reconcile_worktree_git_hooks(worktree_path: Path, desired: dict[str, str]) -> bool:
    """Install *desired* wade-managed git hooks and neutralize stale ones.

    Idempotent across re-bootstraps of an existing worktree (a supported flow —
    ``wade implement`` re-bootstraps a reused worktree): a hook whose ``.wade.yml``
    gate was turned off since a prior session is not left firing. Managed names
    are :data:`_MANAGED_GITHOOK_NAMES`.

    - When *desired* is non-empty, install those hooks via
      :func:`install_worktree_git_hooks` (captures priors + sets
      ``core.hooksPath``), then replace any *existing* wade script for a
      now-undesired managed hook with a chain-only passthrough so its gate is gone
      but a captured prior still runs. Scripts are only ever *replaced*, never
      created, so a fresh worktree gets exactly the desired set.
    - When *desired* is empty, fully uninstall wade's managed hooks (only if wade
      set ``core.hooksPath``) so the user's own ``.git/hooks`` are restored.

    Returns True when wade's worktree ``core.hooksPath`` is active afterwards.
    """
    if not desired:
        return _uninstall_worktree_git_hooks(worktree_path)

    ok = install_worktree_git_hooks(worktree_path, desired)

    hooks_dir = worktree_path / WADE_GITHOOKS_DIR
    for hook_name in _MANAGED_GITHOOK_NAMES:
        if hook_name in desired:
            continue
        stale = hooks_dir / hook_name
        if not stale.exists():
            continue  # never wade-managed here — don't create an inert file
        try:
            stale.write_text(_passthrough_hook_script(hook_name), encoding="utf-8")
            stale.chmod(0o755)
            logger.debug("skills.githook_neutralized", hook=hook_name, path=str(worktree_path))
        except OSError:
            logger.warning(
                "skills.githook_neutralize_failed", hook=hook_name, path=str(worktree_path)
            )
    return ok


def _uninstall_worktree_git_hooks(worktree_path: Path) -> bool:
    """Remove wade's managed git hooks and unset the worktree hooksPath, if wade set it.

    A no-op when wade does not manage this worktree (so a fresh worktree with no
    configured hooks is untouched). Removes each managed hook script and its chain
    file, then unsets the worktree ``core.hooksPath`` so git falls back to the
    user's own ``.git/hooks``, and rolls back the repo-wide
    ``extensions.worktreeConfig`` when it is safe (see
    :func:`_maybe_disable_worktree_config_extension`). Always returns False (no
    wade hooksPath active).
    """
    current = git_repo.get_config_value(worktree_path, "core.hooksPath", worktree=True)
    if current != WADE_GITHOOKS_DIR:
        return False  # wade does not manage this worktree — nothing to undo

    hooks_dir = worktree_path / WADE_GITHOOKS_DIR
    removals = [worktree_path / WADE_HOOK_LEGACY_CHAIN_FILE]
    for hook_name in _MANAGED_GITHOOK_NAMES:
        removals.append(hooks_dir / hook_name)
        removals.append(worktree_path / _chain_file(hook_name))
    for target in removals:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            logger.warning("skills.githook_remove_failed", path=str(target))

    git_repo.unset_config_value(worktree_path, "core.hooksPath", worktree=True)
    _maybe_disable_worktree_config_extension(worktree_path)
    logger.debug("skills.githooks_uninstalled", path=str(worktree_path))
    return False


def _maybe_disable_worktree_config_extension(worktree_path: Path) -> None:
    """Roll back the repo-wide ``extensions.worktreeConfig`` on uninstall — but only
    when no worktree still relies on a worktree-scoped ``core.hooksPath``.

    :func:`install_worktree_git_hooks` enables this extension repo-wide so it can
    write a per-worktree ``core.hooksPath``; the install-failure path rolls it back
    transactionally. Uninstall would similarly like to "leave no persistent config
    behind", but here the flag is **repo-WIDE and shared**: a sibling worktree (a
    core wade flow — multiple parallel worktrees) may carry its own worktree-scoped
    ``core.hooksPath`` that git would silently stop reading if the extension were
    disabled. So only disable it once *this* uninstall leaves no worktree using a
    worktree-scoped hooksPath. Best-effort — never raises; if the worktree set
    can't be enumerated the (inert-without-an-override) flag is simply left as-is.
    """
    main = git_repo.get_main_worktree_path(worktree_path) or worktree_path
    try:
        worktrees = git_worktree.list_worktrees(main)
    except Exception:
        return  # can't enumerate → leave the inert extension in place
    for wt in worktrees:
        # A worktree-scoped read needs the extension still enabled — it is, since
        # we only unset it below. Any lingering hooksPath override (wade's or the
        # user's) means a sibling still depends on worktree config.
        if git_repo.get_config_value(Path(wt.path), "core.hooksPath", worktree=True):
            return
    git_repo.unset_config_value(worktree_path, "extensions.worktreeConfig")
    logger.debug("skills.worktree_config_extension_disabled", path=str(worktree_path))


def _bake_shell_single_quoted(value: str | None) -> str:
    """Escape *value* for substitution inside a single-quoted shell literal.

    The pre-commit template embeds baked commands as ``'__PLACEHOLDER__'``; a
    command containing a single quote would otherwise break the script literal.
    ``None``/empty becomes an empty string (the "step not configured" sentinel).
    Commands are project-author-configured (trusted), so this guards script
    *validity*, not injection.
    """
    return (value or "").replace("'", "'\\''")


def build_pre_commit_hook_script(lint: str | None, test: str | None) -> str:
    """Load the ``pre-commit`` template and bake in the resolved lint/test commands."""
    script = load_hook_template("pre-commit")
    script = script.replace("__WADE_PRE_COMMIT_LINT__", _bake_shell_single_quoted(lint))
    script = script.replace("__WADE_PRE_COMMIT_TEST__", _bake_shell_single_quoted(test))
    return script


def build_commit_msg_hook_script() -> str:
    """Load the ``commit-msg`` template (pure bash — no command substitution)."""
    return load_hook_template("commit-msg")
