"""Skill file installer — copy/symlink skill templates to target projects."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import structlog

from wade.models.config import ProjectConfig
from wade.skills.doc_targets import detect_doc_targets, format_doc_targets
from wade.utils.markdown import has_marker_block, remove_marker_block

logger = structlog.get_logger()

# Placeholder substitutions applied when skill files are copied to a project.
# Maps placeholder string → relative path inside templates/skills/_partials/.
_SKILL_PARTIALS: dict[str, str] = {
    "{user_interaction_prompt}": "_partials/user-interaction.md",
    "{review_enforcement_rule}": "_partials/review-enforcement-rule.md",
    "{review_plan_step}": "_partials/review-plan-step.md",
    "{review_implementation_closing_step}": "_partials/review-implementation-closing-step.md",
    "{doc_update_step}": "_partials/doc-update-step.md",
    "{knowledge_step}": "_partials/knowledge-step.md",
}


def get_wade_repo_root() -> Path:
    """Get the wade package repository root (for self-init detection).

    Works in both editable installs (dev) and regular installs.
    """
    return Path(__file__).parent.parent.parent.parent


def get_templates_dir() -> Path:
    """Get the path to the templates directory.

    Looks in two places:
    1. Repo root (development / editable install) — templates/ next to src/
    2. Inside the installed package (pip install) — wade/templates/
    """
    # 1. Dev mode: walk up from src/wade/skills/installer.py → repo root
    repo_root = Path(__file__).parent.parent.parent.parent
    dev_templates = repo_root / "templates"
    if dev_templates.is_dir() and (dev_templates / "skills").is_dir():
        return dev_templates

    # 2. Installed package: templates are force-included as wade/templates/
    import importlib.resources

    pkg_templates = importlib.resources.files("wade").joinpath("templates")
    pkg_path = Path(str(pkg_templates))
    if pkg_path.is_dir():
        return pkg_path

    # Last resort — return the dev path (will trigger "not found" warning)
    return dev_templates


def get_skills_templates_dir() -> Path:
    """Get the path to the skill templates directory."""
    return get_templates_dir() / "skills"


def load_prompt_template(name: str) -> str:
    """Load a prompt template by name from templates/prompts/.

    Args:
        name: Template filename (e.g. "review-plan.md").

    Raises:
        FileNotFoundError: If the template does not exist.
    """
    template = get_templates_dir() / "prompts" / name
    if not template.is_file():
        raise FileNotFoundError(f"Prompt template not found: {template}")
    return template.read_text(encoding="utf-8").strip()


def load_hook_template(name: str) -> str:
    """Load a git-hook script template by name from templates/hooks/.

    Unlike :func:`load_prompt_template`, the content is returned verbatim (no
    ``.strip()``) — a shell script's leading shebang and trailing newline matter.

    Raises:
        FileNotFoundError: If the template does not exist.
    """
    template = get_templates_dir() / "hooks" / name
    if not template.is_file():
        raise FileNotFoundError(f"Hook template not found: {template}")
    return template.read_text(encoding="utf-8")


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
    from wade.git import repo as git_repo

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
    from wade.git import repo as git_repo

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
    from wade.git import repo as git_repo

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
    from wade.git import repo as git_repo
    from wade.git import worktree as git_worktree

    main = git_repo.get_main_worktree_path(worktree_path) or worktree_path
    try:
        worktrees = git_worktree.list_worktrees(main)
    except Exception:
        return  # can't enumerate → leave the inert extension in place
    for wt in worktrees:
        # A worktree-scoped read needs the extension still enabled — it is, since
        # we only unset it below. Any lingering hooksPath override (wade's or the
        # user's) means a sibling still depends on worktree config.
        if git_repo.get_config_value(Path(wt["path"]), "core.hooksPath", worktree=True):
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


# --- Skill registry: name → list of files ---

SKILL_FILES: dict[str, list[str]] = {
    "task": ["SKILL.md", "plan-format.md", "examples.md"],
    "plan-session": ["SKILL.md", "reference/plan-format.md"],
    "implementation-session": [
        "SKILL.md",
        "reference/recovery.md",
        "reference/pr-summary-format.md",
        "reference/doc-update.md",
        "reference/tracking-issues.md",
        "reference/new-plan.md",
    ],
    "review-pr-comments-session": [
        "SKILL.md",
        "reference/recovery.md",
        "reference/doc-update.md",
    ],
    "deps": ["SKILL.md"],
    "knowledge": ["SKILL.md"],
}

# Skills that should always be overwritten on update
ALWAYS_OVERWRITE = {
    "plan-session",
    "implementation-session",
    "review-pr-comments-session",
    "knowledge",
}

# Skills whose SKILL.md files contain placeholder strings (see _SKILL_PARTIALS) that
# must be expanded at install time.  These cannot be installed as plain directory
# symlinks in self-init mode because the agent would see unexpanded placeholders.
# Currently the same set as ALWAYS_OVERWRITE — kept separate because the concerns
# are distinct and may diverge if new skills are added.
INJECT_SKILLS = {"plan-session", "implementation-session", "review-pr-comments-session"}

# Old skill names removed in the phase-skill refactor — cleaned up during update
_LEGACY_SKILLS = {
    "workflow",
    "sync",
    "pr-summary",
    "work-session",
    "review-session",
    "address-reviews-session",
}

# All skill names Wade manages (current + legacy) — used for safe pruning
MANAGED_SKILL_NAMES: set[str] = set(SKILL_FILES) | _LEGACY_SKILLS

# Cross-tool directories that get symlinked to .claude/skills
CROSS_TOOL_DIRS = [".github/skills", ".agents/skills", ".cursor/skills"]

# LEGACY: standalone guard scripts that older wade versions copied into each
# worktree's ``.{tool}/hooks/`` dir. Guards are now the versioned ``wade hook``
# entry point (no copied scripts), but these paths are still gitignored and
# flagged-if-tracked so worktrees created by an older wade get cleaned up.
PLAN_GUARD_HOOK_FILES = [
    ".claude/hooks/plan_write_guard.py",
    ".cursor/hooks/plan_write_guard.py",
    ".copilot/hooks/plan_write_guard.py",
]
WORKTREE_GUARD_HOOK_FILES = [
    ".claude/hooks/worktree_guard.py",
    ".cursor/hooks/worktree_guard.py",
    ".copilot/hooks/worktree_guard.py",
]

# Wade-managed hook config files written per-session by crossby's hook writers
# (one per supported tool) — never committed. ``.agents/hooks.json`` is
# Antigravity CLI's Stop-hook config (crossby's AntigravityCLIHooksWriter).
HOOK_CONFIG_FILES = [
    ".cursor/hooks.json",
    ".agents/hooks.json",
    ".github/hooks/hooks.json",
    ".codex/hooks.json",
]

# --- Command-to-skill mapping: which skills each session type needs ---

PLAN_SKILLS: list[str] = ["plan-session", "task", "deps", "knowledge"]
DEPS_SKILLS: list[str] = ["deps"]
IMPLEMENT_SKILLS: list[str] = ["implementation-session", "task", "knowledge"]
REVIEW_SKILLS: list[str] = ["review-pr-comments-session", "task", "knowledge"]


def get_worktree_gitignore_entries() -> list[str]:
    """Compute gitignore entries for wade artifacts in worktrees.

    Returns **specific file paths** (never directories, except ``.wade/``)
    to ensure user-owned files in the same parent directories are never hidden.

    Cross-tool symlinks and untracked pointer files are not included here —
    they are added conditionally by ``write_worktree_gitignore()``.
    """
    entries: list[str] = []

    # Skill files (specific files, not directories — user may have their own skills)
    for name, files in sorted(SKILL_FILES.items()):
        for filename in files:
            entries.append(f".claude/skills/{name}/{filename}")

    # Guard hook scripts
    entries.extend(PLAN_GUARD_HOOK_FILES)
    entries.extend(WORKTREE_GUARD_HOOK_FILES)

    # Hook config files
    entries.extend(HOOK_CONFIG_FILES)

    # AI tool settings (written per-session to worktrees only)
    entries.append(".claude/settings.json")
    entries.append(".cursor/cli.json")
    # Codex config — crossby's hook writer sets [features].codex_hooks = true here
    # per-session so Codex loads the installed hooks; not user content in a worktree.
    entries.append(".codex/config.toml")

    # Session artifacts
    entries.extend(
        [
            "PLAN.md",
            "PR-SUMMARY.md",
            ".commit-msg",
            ".wade/",
            ".wade-managed",
        ]
    )

    return entries


# --- Knowledge .gitattributes union-merge block (#358) ---

KNOWLEDGE_ATTRIBUTES_MARKER_START = "# wade:knowledge:start"
KNOWLEDGE_ATTRIBUTES_MARKER_END = "# wade:knowledge:end"


def _gitattributes_pattern(rel_path: Path) -> str:
    """Render a repo-relative path as a *literal* ``.gitattributes`` pattern.

    Escapes gitattributes glob metacharacters (``\\ * ? [``) so the path matches only
    itself, then C-quotes the whole token when it contains whitespace, a double quote, a
    control character, or a leading ``#``/``!`` — any of which would otherwise let git's
    line parser mis-split the pattern from its attributes (a path with a space would read
    only its first whitespace-delimited token as the pattern, orphaning ``merge=union``).
    A plain path — the common case, e.g. ``KNOWLEDGE.md`` — is returned unchanged. git
    C-unquotes a double-quoted pattern *before* glob-matching, so the two escapes compose.
    """
    posix = rel_path.as_posix()
    escaped = re.sub(r"([\\*?\[])", r"\\\1", posix)
    needs_quote = (
        any(ch.isspace() or ord(ch) < 0x20 for ch in posix)
        or '"' in posix
        or posix.startswith(("#", "!"))
    )
    if not needs_quote:
        return escaped
    body = escaped.replace("\\", "\\\\").replace('"', '\\"')
    body = body.replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
    return f'"{body}"'


def ensure_knowledge_merge_attributes(root: Path, config: ProjectConfig) -> None:
    """Ensure a wade-managed ``merge=union`` block for the knowledge files in ``.gitattributes``.

    The knowledge file (append-only entries) and its JSONL vote log are append-only
    logs; two sessions appending is a textbook conflict, and ``merge=union`` keeps
    **both** sides — exactly right here. Union does the real work locally, where
    ``catchup``/``sync`` merge the base branch into the worktree and the worktree's
    ``.gitattributes`` applies.

    ``.gitattributes`` is a **normal tracked repo file** (not gitignored). Bootstrap
    ensures the block in the worktree so even the first session has it before its
    first catchup/sync; the session commits it alongside its knowledge edit, so it
    merges to main and becomes the server-side backstop. Once main has it, later
    worktrees inherit it and this call is a no-op.

    Idempotent via the shared marker-block helpers. The write is skipped entirely
    when the on-disk content already matches, so a project that already has the block
    is never marked dirty. Paths derive from ``config.knowledge.path`` and its
    ``.ratings.jsonl`` sibling, validated and escaped as literal gitattributes patterns.
    """
    from wade.utils.knowledge_file import resolve_knowledge_path, resolve_ratings_path

    # Resolve + validate the knowledge path the same way knowledge resolution does — an
    # absolute or root-escaping path is rejected, and we write no block rather than a
    # bogus attributes line. Both patterns are rendered relative to root and escaped so a
    # path with a space or a glob metacharacter can't alter what ``merge=union`` matches.
    try:
        knowledge_abs = resolve_knowledge_path(root, config.knowledge)
    except ValueError:
        logger.debug("skills.knowledge_merge_attributes_invalid_path", path=str(root))
        return
    root_abs = root.resolve()
    ratings_abs = resolve_ratings_path(knowledge_abs)
    knowledge_pattern = _gitattributes_pattern(knowledge_abs.relative_to(root_abs))
    ratings_pattern = _gitattributes_pattern(ratings_abs.relative_to(root_abs))
    block = (
        f"{KNOWLEDGE_ATTRIBUTES_MARKER_START}\n"
        f"{knowledge_pattern} merge=union\n"
        f"{ratings_pattern} merge=union\n"
        f"{KNOWLEDGE_ATTRIBUTES_MARKER_END}\n"
    )

    gitattributes = root / ".gitattributes"
    existing = ""
    if gitattributes.is_file():
        existing = gitattributes.read_text(encoding="utf-8")
        if has_marker_block(
            existing, KNOWLEDGE_ATTRIBUTES_MARKER_START, KNOWLEDGE_ATTRIBUTES_MARKER_END
        ):
            existing = remove_marker_block(
                existing, KNOWLEDGE_ATTRIBUTES_MARKER_START, KNOWLEDGE_ATTRIBUTES_MARKER_END
            )

    new_content = existing.rstrip("\n") + "\n\n" + block if existing.strip() else block

    current = gitattributes.read_text(encoding="utf-8") if gitattributes.is_file() else ""
    if new_content == current:
        return  # already correct — don't touch mtime / make the file dirty
    gitattributes.write_text(new_content, encoding="utf-8")
    logger.debug("skills.knowledge_merge_attributes_written", path=str(root))


def install_skills(
    project_root: Path,
    is_self_init: bool = False,
    force: bool = False,
    templates_dir: Path | None = None,
    skills: list[str] | None = None,
    extra_partials: dict[str, str] | None = None,
) -> list[str]:
    """Install skill files to a project.

    Args:
        project_root: Root of the target project.
        is_self_init: If True, symlink skill directories instead of copying files.
            Skills in ``INJECT_SKILLS`` are always processed copies even in this mode,
            because their templates contain placeholders that must be expanded.
        force: If True, overwrite existing files.
        templates_dir: Override the skills templates directory.
            Useful for worktrees where templates live in the worktree itself.
        skills: If provided, install only the listed skills instead of all
            ``SKILL_FILES``.  When ``None`` (default), all skills are installed.
        extra_partials: Placeholder overrides. Caller-supplied values win over
            the ``{doc_targets}`` value computed here from ``project_root``.

    Returns:
        List of installed paths (relative to project root).  Symlinked skill
        directories are reported at directory level; copied skills at file level.
    """
    installed: list[str] = []
    if templates_dir is None:
        templates_dir = get_skills_templates_dir()

    if not templates_dir.is_dir():
        logger.warning("skills.templates_not_found", path=str(templates_dir))
        return installed

    computed_partials = {"{doc_targets}": format_doc_targets(detect_doc_targets(project_root))}
    extra_partials = {**computed_partials, **(extra_partials or {})}

    primary_skills_dir = project_root / ".claude" / "skills"

    # Clean up legacy skill directories from previous versions
    for legacy_name in _LEGACY_SKILLS:
        legacy_dir = primary_skills_dir / legacy_name
        if legacy_dir.is_symlink():
            legacy_dir.unlink()
            logger.debug("skills.removed_legacy", name=legacy_name)
        elif legacy_dir.is_dir():
            shutil.rmtree(legacy_dir)
            logger.debug("skills.removed_legacy", name=legacy_name)

    # Determine which skills to install
    if skills is not None:
        invalid = set(skills) - set(SKILL_FILES.keys())
        if invalid:
            logger.warning("skills.unknown_skill_names", names=sorted(invalid))
        skill_items = {name: SKILL_FILES[name] for name in skills if name in SKILL_FILES}

        # Prune stale skills: remove Wade-managed skills not in the requested
        # set (ensures clean per-command isolation on worktree reuse).
        # Only remove known Wade-managed names — leave user-owned dirs untouched.
        if primary_skills_dir.is_dir():
            stale_managed = MANAGED_SKILL_NAMES - set(skill_items)
            for entry in primary_skills_dir.iterdir():
                if entry.name in stale_managed and (entry.is_symlink() or entry.is_dir()):
                    if entry.is_symlink():
                        entry.unlink()
                    else:
                        shutil.rmtree(entry)
                    logger.debug("skills.pruned_stale", name=entry.name)
    else:
        skill_items = SKILL_FILES

    for skill_name, files in skill_items.items():
        if is_self_init and skill_name not in INJECT_SKILLS:
            # Symlink the whole directory (no partials expansion needed)
            _link_skill_dir(project_root, skill_name, templates_dir)
            installed.append(f".claude/skills/{skill_name}")
        else:
            # Copy individual files, expanding partials if present.
            # In self-init mode, INJECT_SKILLS must be processed copies so agents
            # see expanded content rather than raw placeholder strings.
            overwrite = is_self_init or force or skill_name in ALWAYS_OVERWRITE

            # Remove existing symlink for inject skills in self-init before creating dir
            if is_self_init and skill_name in INJECT_SKILLS:
                link = primary_skills_dir / skill_name
                if link.is_symlink():
                    link.unlink()

            for filename in files:
                src = templates_dir / skill_name / filename
                if not src.is_file():
                    continue
                dest = primary_skills_dir / skill_name / filename
                if _copy_skill_file(
                    src,
                    dest,
                    overwrite=overwrite,
                    skills_templates_dir=templates_dir,
                    extra_partials=extra_partials,
                ):
                    installed.append(f".claude/skills/{skill_name}/{filename}")

    # Ensure primary skills dir exists before cross-tool symlinks
    primary_skills_dir.mkdir(parents=True, exist_ok=True)

    # Cross-tool symlinks — skip real user-owned directories
    for cross_dir in CROSS_TOOL_DIRS:
        cross_path = project_root / cross_dir
        if cross_path.exists() and not cross_path.is_symlink():
            logger.debug("skills.skip_cross_tool_user_dir", path=str(cross_path))
            continue
        _link_cross_tool(project_root, cross_dir, primary_skills_dir)
        installed.append(cross_dir)

    return installed


def remove_skills(project_root: Path) -> list[str]:
    """Remove all installed skill files and directories.

    Returns list of removed paths.
    """
    removed: list[str] = []

    # Remove cross-tool symlinks first
    for cross_dir in CROSS_TOOL_DIRS:
        cross_path = project_root / cross_dir
        if cross_path.is_symlink():
            cross_path.unlink()
            removed.append(cross_dir)
        # Real user-owned directories are not removed

    # Remove skill directories (current + legacy)
    primary_skills_dir = project_root / ".claude" / "skills"
    for skill_name in {*SKILL_FILES, *_LEGACY_SKILLS}:
        skill_dir = primary_skills_dir / skill_name
        if skill_dir.is_symlink():
            skill_dir.unlink()
            removed.append(f".claude/skills/{skill_name}")
        elif skill_dir.is_dir():
            shutil.rmtree(skill_dir)
            removed.append(f".claude/skills/{skill_name}")

    # Clean up empty parent directories
    for parent in [
        primary_skills_dir,
        project_root / ".claude",
        project_root / ".github",
        project_root / ".agents",
        project_root / ".cursor",
    ]:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()

    return removed


def _expand_partials(
    content: str,
    skills_templates_dir: Path,
    extra_partials: dict[str, str] | None = None,
) -> str:
    """Expand placeholder strings in *content* using partial template files.

    ``extra_partials`` (placeholder → replacement string) are applied first, so
    callers can override or suppress any entry in ``_SKILL_PARTIALS`` by passing
    an empty string.  File-based partials in ``_SKILL_PARTIALS`` are applied
    afterwards for any placeholders still present, then ``extra_partials`` is
    re-applied so placeholders nested inside a just-expanded file partial (e.g.
    ``{doc_targets}`` inside ``doc-update-step.md``) also resolve.  Unknown
    partial paths are left unchanged with a warning.
    """
    if extra_partials:
        for placeholder, replacement in extra_partials.items():
            content = content.replace(placeholder, replacement)
    for placeholder, rel_path in _SKILL_PARTIALS.items():
        if placeholder not in content:
            continue
        partial = skills_templates_dir / rel_path
        if not partial.is_file():
            logger.warning("skills.partial_not_found", path=str(partial))
            continue
        content = content.replace(placeholder, partial.read_text(encoding="utf-8").rstrip())
    if extra_partials:
        for placeholder, replacement in extra_partials.items():
            content = content.replace(placeholder, replacement)
    return content


def _copy_skill_file(
    src: Path,
    dest: Path,
    overwrite: bool = False,
    skills_templates_dir: Path | None = None,
    extra_partials: dict[str, str] | None = None,
) -> bool:
    """Copy a single skill file, creating parent dirs as needed.

    If ``skills_templates_dir`` is provided, placeholder strings in the file
    content (see ``_SKILL_PARTIALS``) are expanded before writing.  Any
    ``extra_partials`` overrides are applied first (see ``_expand_partials``).

    Returns True if file was installed, False if skipped.
    """
    if dest.exists() and not overwrite:
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    content = src.read_text(encoding="utf-8")
    needs_expansion = skills_templates_dir is not None and any(
        p in content for p in _SKILL_PARTIALS
    )
    if needs_expansion or extra_partials:
        base_templates_dir = skills_templates_dir or src.parent.parent
        content = _expand_partials(
            content,
            base_templates_dir,
            extra_partials=extra_partials,
        )
    dest.write_text(content, encoding="utf-8")
    logger.debug("skills.copied", src=str(src), dest=str(dest))
    return True


def _link_skill_dir(project_root: Path, skill_name: str, templates_dir: Path) -> None:
    """Create a symlink from .claude/skills/<name> → ../../templates/skills/<name>.

    For self-init mode: edits to templates are immediately reflected.
    """
    link = project_root / ".claude" / "skills" / skill_name
    target = templates_dir / skill_name

    if not target.is_dir():
        return

    # Calculate relative path from link location (.claude/skills/<name>/) to target
    # Link lives 2 levels deep from project root, so go up 2 levels then into rel_target.
    try:
        rel_target = target.resolve().relative_to(project_root.resolve())
        rel_path = Path("../..") / rel_target
    except ValueError:
        # Target is outside project root — use absolute
        rel_path = target.resolve()

    link.parent.mkdir(parents=True, exist_ok=True)

    if link.is_symlink():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)

    link.symlink_to(rel_path)
    logger.debug("skills.linked", link=str(link), target=str(rel_path))


def _link_cross_tool(
    project_root: Path,
    cross_dir: str,
    primary_skills_dir: Path,
) -> None:
    """Create cross-tool symlink: .github/skills → .claude/skills etc."""
    link = project_root / cross_dir

    if not primary_skills_dir.is_dir():
        return

    # Calculate relative path from link parent to primary_skills_dir.
    # e.g. .github/skills → ../.claude/skills (matches bash behavior)
    rel_target = Path(os.path.relpath(primary_skills_dir.resolve(), link.parent.resolve()))

    link.parent.mkdir(parents=True, exist_ok=True)

    if link.is_symlink():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)

    link.symlink_to(rel_target)
    logger.debug("skills.cross_linked", link=str(link), target=str(rel_target))
