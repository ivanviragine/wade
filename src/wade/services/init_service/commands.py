"""Init service commands — ``init``, ``update``, and ``deinit`` entry points.

Hosts the three public CLI-facing commands, the self-upgrade re-exec guard, and
the ``get_wade_root`` alias. Setup prompts, config writing, manifest handling,
migrations, and shell integration live in sibling modules and are imported here
by name.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import structlog
from crossby.ai_tools import AbstractAITool

from wade.config.loader import find_config_file, load_config
from wade.git import repo
from wade.git.repo import GitError
from wade.models.config import (
    AICommandConfig,
    ComplexityModelMapping,
    KnowledgeConfig,
    ProjectSettings,
)
from wade.services.init_service.config_io import (
    _COMMAND_OVERRIDE_NAMES,
    _ensure_markdown_file,
    _normalize_knowledge_setup,
    _patch_config,
    _resolve_models,
    _write_config,
)
from wade.services.init_service.manifest import (
    MANIFEST_FILENAME,
    _read_manifest_version,
    _show_init_summary,
    _write_manifest,
)
from wade.services.init_service.migrations import (
    _clean_gitignore,
    _cleanup_gemini_artifacts,
    _ensure_wade_dir_self_ignoring,
    _migrate_ai_artifacts_off_main,
    _migrate_gitignore_block,
    _migrate_skills_off_main,
)
from wade.services.init_service.prompts_ai import _prompt_ai_section
from wade.services.init_service.prompts_setup import (
    _prompt_claude_code_settings,
    _prompt_command_overrides,
    _prompt_configure_completions,
    _prompt_hooks_setup,
    _prompt_implementation_setup,
    _prompt_knowledge_setup,
    _prompt_project_settings,
    _prompt_provider_setup,
)
from wade.services.init_service.shell import (
    _prompt_configure_shell_integration,
)
from wade.skills import installer, pointer
from wade.skills.installer import get_wade_repo_root
from wade.ui.console import console

logger = structlog.get_logger()

__all__ = [
    "_maybe_self_upgrade",
    "deinit",
    "get_wade_root",
    "init",
    "update",
]


def get_wade_root() -> Path:
    """Get the wade package root (for self-init detection).

    Delegates to installer.get_wade_repo_root() — kept as a local alias
    for backward compatibility.
    """
    return get_wade_repo_root()


def init(
    project_root: Path | None = None,
    ai_tool: str | None = None,
    non_interactive: bool = False,
) -> bool:
    """Initialize WADE in a project.

    Steps:
    1. Validate git repo
    2. Detect installed AI tools
    3. Parse existing .wade.yml for re-init pre-fill
    4. Run interactive wizard (loop until confirmed or cancelled)
    5. Write config
    6. Write manifest and make .wade/ self-ignoring

    Returns True on success.
    """
    cwd = project_root or Path.cwd()

    # 1. Validate git repo
    if not repo.is_git_repo(cwd):
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return False

    try:
        root = repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix(
            "Could not determine repository root",
            "Check that you are in a git repository",
        )
        return False

    # 2. Detect installed tools (once — independent of wizard)
    installed_tools = [str(t) for t in AbstractAITool.detect_installed()]

    # 3. Parse existing config for re-init pre-fill
    config_path = root / ".wade.yml"
    existing_config = None
    parse_failed = False
    if config_path.exists():
        try:
            from wade.config.loader import parse_config_file

            existing_config = parse_config_file(config_path)
        except Exception as exc:
            parse_failed = True
            logger.warning(
                "init.existing_config_parse_failed",
                path=str(config_path),
                error=str(exc),
            )
            console.warn(
                f"Could not parse existing {config_path.name} — will overwrite after confirmation"
            )

    # --- Interactive wizard (loop supports Modify) ---
    provider_setup: dict[str, Any] = {}
    project_settings: ProjectSettings = ProjectSettings()
    selected_tool: str | None = None
    default_model: str | None = None
    default_effort: str | None = None
    default_yolo: bool | None = None
    implementation_setup: dict[str, Any] = {}
    command_overrides: dict[str, dict[str, Any]] = {}
    hooks_setup: dict[str, Any] = {}
    knowledge_setup: dict[str, Any] = {}
    tools_in_use: set[str] = set()

    # Current values for pre-fill — derived from existing config on first pass,
    # then updated from in-memory selections before each "Modify" iteration so
    # the second pass shows the values chosen in the first pass, not stale disk state.
    _cur_provider: str | None = existing_config.provider.name.value if existing_config else None
    _cur_provider_api_token_env: str | None = (
        existing_config.provider.api_token_env if existing_config else None
    )
    _cur_provider_settings: dict[str, str] = (
        dict(existing_config.provider.settings) if existing_config else {}
    )
    _cur_main_branch: str | None = existing_config.project.main_branch if existing_config else None
    _cur_branch_prefix: str | None = (
        existing_config.project.branch_prefix if existing_config else None
    )
    _cur_issue_label: str | None = existing_config.project.issue_label if existing_config else None
    _cur_worktrees_dir: str | None = (
        existing_config.project.worktrees_dir if existing_config else None
    )
    _cur_ai_tool: str | None = existing_config.ai.default_tool if existing_config else None
    _cur_ai_model: str | None = existing_config.ai.default_model if existing_config else None
    _cur_ai_effort: str | None = existing_config.ai.effort if existing_config else None
    _cur_ai_yolo: bool | None = existing_config.ai.yolo if existing_config else None
    _cur_impl_tool: str | None = existing_config.ai.implement.tool if existing_config else None
    _cur_model_mapping: ComplexityModelMapping | None = (
        existing_config.models.get(_cur_impl_tool or _cur_ai_tool or "")
        if existing_config
        else None
    )
    _cur_effective_tool: str | None = _cur_impl_tool or _cur_ai_tool
    _cur_cmd_overrides: dict[str, dict[str, Any]] = {}
    if existing_config:
        for _cmd in _COMMAND_OVERRIDE_NAMES:
            _cfg = getattr(existing_config.ai, _cmd, None)
            if isinstance(_cfg, AICommandConfig):
                _entry: dict[str, Any] = {}
                if _cfg.tool:
                    _entry["tool"] = _cfg.tool
                if _cfg.model:
                    _entry["model"] = _cfg.model
                if _cfg.mode:
                    _entry["mode"] = _cfg.mode
                if _cfg.effort:
                    _entry["effort"] = _cfg.effort
                if _cfg.enabled is not None:
                    _entry["enabled"] = "true" if _cfg.enabled else "false"
                if _cfg.yolo is not None:
                    _entry["yolo"] = "true" if _cfg.yolo else "false"
                if _entry:
                    _cur_cmd_overrides[_cmd] = _entry
    _cur_hooks_post: str | None = (
        existing_config.hooks.post_worktree_create if existing_config else None
    )
    _cur_hooks_copy: list[str] | None = (
        list(existing_config.hooks.copy_to_worktree) if existing_config else None
    )
    _cur_knowledge_enabled: bool = existing_config.knowledge.enabled if existing_config else False
    _cur_knowledge_path: str = existing_config.knowledge.path if existing_config else "KNOWLEDGE.md"

    while True:
        # 4a. Provider
        provider_setup = _prompt_provider_setup(
            root,
            non_interactive,
            current_provider=_cur_provider,
            current_api_token_env=_cur_provider_api_token_env,
            current_settings=_cur_provider_settings,
        )

        # 4b. Project settings
        project_settings = _prompt_project_settings(
            root,
            non_interactive,
            current_main_branch=_cur_main_branch,
            current_branch_prefix=_cur_branch_prefix,
            current_issue_label=_cur_issue_label,
            current_worktrees_dir=_cur_worktrees_dir,
        )

        # 4c. AI (tool, model, effort, yolo)
        try:
            selected_tool, default_model, default_effort, default_yolo = _prompt_ai_section(
                ai_tool,
                non_interactive,
                current_tool=_cur_ai_tool,
                current_model=_cur_ai_model,
                current_effort=_cur_ai_effort,
                current_yolo=_cur_ai_yolo,
            )
        except ValueError as exc:
            console.error(str(exc))
            return False

        # 4d. Implementation (per-tier models + effort)
        implementation_setup = _prompt_implementation_setup(
            selected_tool,
            installed_tools,
            non_interactive,
            current_implement_tool=_cur_impl_tool,
            current_model_mapping=_cur_model_mapping,
            current_effective_tool=_cur_effective_tool,
        )

        # 4e. Per-command overrides (tool, model, effort, yolo)
        command_overrides = _prompt_command_overrides(
            installed_tools,
            non_interactive,
            default_model=default_model,
            default_tool=selected_tool,
            current_overrides=_cur_cmd_overrides,
        )

        # 4f. Compute tools_in_use (needed to gate tool-specific side effects)
        tools_in_use = set()
        if selected_tool:
            tools_in_use.add(selected_tool)
        if implementation_setup.get("tool"):
            tools_in_use.add(implementation_setup["tool"])
        for cmd_cfg in command_overrides.values():
            if cmd_cfg.get("tool"):
                tools_in_use.add(cmd_cfg["tool"])

        # 4g. Worktree hooks
        hooks_setup = _prompt_hooks_setup(
            non_interactive,
            current_post_worktree_create=_cur_hooks_post,
            current_copy_to_worktree=_cur_hooks_copy,
        )

        # 4h. Project knowledge
        knowledge_setup = _prompt_knowledge_setup(
            non_interactive,
            current_enabled=_cur_knowledge_enabled,
            current_path=_cur_knowledge_path,
        )

        # 4i. Summary + Yes / Modify / Cancel
        if non_interactive:
            break  # Skip summary in non-interactive mode

        _show_init_summary(
            provider_setup=provider_setup,
            project_settings=project_settings,
            selected_tool=selected_tool,
            default_model=default_model,
            default_effort=default_effort,
            default_yolo=default_yolo,
            implementation_setup=implementation_setup,
            command_overrides=command_overrides,
            hooks_setup=hooks_setup,
            knowledge_setup=knowledge_setup,
        )

        from wade.ui import prompts as _ui_prompts

        confirm_choices = ["Write .wade.yml", "Modify (re-run wizard)", "Cancel"]
        confirm_idx = _ui_prompts.select(
            "Write these values to .wade.yml?", confirm_choices, default=0
        )
        chosen = confirm_choices[confirm_idx]
        if chosen == "Cancel":
            console.info("Initialization cancelled — no files written.")
            return False
        if chosen == "Modify (re-run wizard)":
            # Update current values from this iteration so the next pass pre-fills
            # with the choices just made rather than the stale on-disk state.
            _cur_provider = provider_setup.get("name")
            _cur_provider_api_token_env = provider_setup.get("api_token_env")
            _cur_provider_settings = dict(provider_setup.get("settings") or {})
            _cur_main_branch = project_settings.main_branch
            _cur_branch_prefix = project_settings.branch_prefix
            _cur_issue_label = project_settings.issue_label
            _cur_worktrees_dir = project_settings.worktrees_dir
            _cur_ai_tool = selected_tool
            _cur_ai_model = default_model
            _cur_ai_effort = default_effort if default_effort != "" else None
            _cur_ai_yolo = default_yolo
            _cur_impl_tool = implementation_setup.get("tool")
            _cur_model_mapping = implementation_setup.get("model_mapping")
            _cur_effective_tool = _cur_impl_tool or selected_tool
            _cur_cmd_overrides = command_overrides
            _cur_hooks_post = hooks_setup.get("post_worktree_create")
            _cur_hooks_copy = hooks_setup.get("copy_to_worktree")
            _cur_knowledge_enabled = bool(knowledge_setup.get("enabled"))
            _cur_knowledge_path = str(knowledge_setup.get("path", "KNOWLEDGE.md"))
            continue
        break  # "Write .wade.yml" — proceed to write phase

    # 4j. Tool-specific settings + shell integration — deferred to here so that
    # choosing "Cancel" or iterating through "Modify" never triggers side effects.
    if "claude" in tools_in_use:
        _prompt_claude_code_settings(non_interactive)
    _prompt_configure_shell_integration(non_interactive)
    _prompt_configure_completions(non_interactive)

    # Post-wizard injections (idempotent — safe regardless of loop iterations)
    if provider_setup.get("add_env_to_copy"):
        copy_list: list[str] = hooks_setup.get("copy_to_worktree", [])
        if ".env" not in copy_list:
            copy_list.append(".env")
        hooks_setup["copy_to_worktree"] = copy_list

    normalized_knowledge_setup = _normalize_knowledge_setup(root, knowledge_setup)
    if normalized_knowledge_setup is None:
        return False
    knowledge_setup = normalized_knowledge_setup

    if knowledge_setup.get("enabled"):
        from wade.services.knowledge_service import resolve_ratings_path

        knowledge_path: str = knowledge_setup.get("path", "KNOWLEDGE.md")
        copy_list_k: list[str] = hooks_setup.get("copy_to_worktree", [])
        ratings_path = str(resolve_ratings_path(Path(knowledge_path)))
        for managed_path in (knowledge_path, ratings_path):
            if managed_path not in copy_list_k:
                copy_list_k.append(managed_path)
        hooks_setup["copy_to_worktree"] = copy_list_k

    # Write phase
    if not non_interactive:
        console.rule("Initing")

    _write_config_kwargs: dict[str, Any] = dict(
        project_settings=project_settings,
        implement_tool=implementation_setup["tool"],
        default_model=default_model,
        default_effort=default_effort,
        default_yolo=default_yolo,
        command_overrides=command_overrides,
        hooks_setup=hooks_setup,
        provider_setup=provider_setup,
        knowledge_setup=knowledge_setup,
    )
    if config_path.exists() and not parse_failed:
        console.info("Config .wade.yml already exists — updating with selected values")
        _patch_config(
            config_path,
            selected_tool,
            implementation_setup["model_mapping"],
            force=not non_interactive,
            **_write_config_kwargs,
        )
    else:
        if parse_failed:
            from wade.ui import prompts as _prompts

            if not non_interactive and not _prompts.confirm(
                f"{config_path.name} could not be parsed — overwrite with new config?",
                default=True,
            ):
                console.error("Aborting — cannot patch a corrupted config file")
                return False
            console.warn(f"Overwriting corrupted {config_path.name}")
        _write_config(
            config_path,
            selected_tool,
            implementation_setup["model_mapping"],
            **_write_config_kwargs,
        )
        console.success(f"Created {config_path.name}")

    # Create the markdown issues file if that's the chosen provider.
    # Failures (existing dir at path, write permission, etc.) shouldn't
    # take down the whole init — surface a fix hint and return False.
    if provider_setup.get("name") == "markdown":
        try:
            _ensure_markdown_file(root, provider_setup.get("settings") or {})
        except (ValueError, OSError) as exc:
            console.error_with_fix(
                str(exc),
                "Set provider.settings.path to a writable file path "
                "(default: ISSUES.md at the repo root).",
            )
            return False

    # Create knowledge file if enabled
    if knowledge_setup.get("enabled"):
        from wade.services.knowledge_service import ensure_knowledge_file

        kconfig = KnowledgeConfig(
            enabled=True,
            path=knowledge_setup.get("path", "KNOWLEDGE.md"),
        )
        kpath = root / kconfig.path
        existed = kpath.exists()
        ensure_knowledge_file(root, kconfig)
        if existed:
            console.info(f"Knowledge file {kpath.name} already exists")
        else:
            console.success(f"Created {kpath.name}")

    _write_manifest(root, [])
    console.success("Wrote .wade/.wade-managed manifest")

    _ensure_wade_dir_self_ignoring(root)

    from wade.services.check_service import validate_config

    check_result = validate_config(root)
    if check_result.is_valid:
        console.success("Config validation passed")
    else:
        console.warn("Config validation issues:")
        for err in check_result.errors:
            console.detail(err)

    console.hint("Commit .wade.yml to your repo:")
    console.detail('git add .wade.yml && git commit -m "chore: initialize wade"')

    console.panel(
        "  Project initialized. Run [bold]wade plan[/] to get started.",
        title="WADE initialized",
    )
    return True


def update(
    project_root: Path | None = None,
    skip_self_upgrade: bool = False,
) -> bool:
    """Update managed files to the latest WADE version.

    Steps:
    1.  Self-upgrade check (runs before project validation)
    2.  Validate repo + config existence
    3.  Read old version from manifest
    4.  Show version transition
    5.  Run config migration pipeline
    6.  Reload config + backfill probed models
    7.  Warn about removed AI tools still referenced in config
    8.  Migrate old skill files off main
    9.  Migrate — remove stale committed gitignore block
    10. Make .wade/ self-ignoring
    11. Clean up AI tool + Gemini artifacts from main checkout (migration)
    12. Rebuild manifest with version

    Never overwrites .wade.yml user values — only patches missing keys
    and refreshes skill files.
    """

    from wade import __version__
    from wade.config.migrations import run_all_migrations

    cwd = project_root or Path.cwd()

    # Step 1: Self-upgrade check — runs before project validation so `wade update` works standalone
    if not skip_self_upgrade and _maybe_self_upgrade():
        # re_exec() was called — this line is never reached
        pass  # pragma: no cover

    # Step 2: Validate repo + config
    try:
        root = repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return False

    config_path = find_config_file(root)
    if config_path is None:
        console.error_with_fix("No .wade.yml found", "Initialize your project first", "wade init")
        return False

    console.rule("wade update")

    # Step 3: Read old version from manifest
    old_version = _read_manifest_version(root)

    # Step 4: Show version transition
    if old_version and old_version != __version__:
        console.info(f"Updating from WADE {old_version} → {__version__}")
    else:
        console.info(f"wade {__version__}")

    # Step 5: Run config migration pipeline
    if run_all_migrations(config_path):
        console.success("Config migrations applied")
    else:
        console.detail("Config already up to date")

    # Step 6: Reload config + backfill probed models
    config = load_config(root)
    ai_tool = config.get_ai_tool()
    if ai_tool:
        model_mapping = _resolve_models(ai_tool)
        _patch_config(config_path, ai_tool, model_mapping)

    # Step 7: Warn about removed AI tools still referenced in config (e.g. Gemini
    # CLI). The tool fields are plain strings, so a stale value loads fine here —
    # surface an actionable message before it breaks a later launch.
    from wade.services.check_service import detect_removed_ai_tools

    for location, replacement in detect_removed_ai_tools(config).items():
        console.warn(
            f"{location} references a removed AI tool — "
            f"switch to '{replacement}' or another supported tool in .wade.yml"
        )

    # Step 8: Migrate old skill files off main (skills now live in worktrees only)
    is_self = root.resolve() == get_wade_root().resolve()
    if not is_self:
        removed = _migrate_skills_off_main(root)
        if removed:
            console.info(f"Migrated {len(removed)} old skill entries off main")

    # Step 9: Migrate — remove stale committed gitignore block (if present)
    _migrate_gitignore_block(root)

    # Step 10: Make .wade/ self-ignoring (idempotent)
    _ensure_wade_dir_self_ignoring(root)

    # Step 11: Clean up AI tool + Gemini artifacts from main checkout (migration)
    removed_artifacts = _migrate_ai_artifacts_off_main(root)
    if removed_artifacts:
        console.info(f"Removed {len(removed_artifacts)} AI tool artifact(s) from main checkout")
    removed_gemini = _cleanup_gemini_artifacts(root)
    if removed_gemini:
        console.info(f"Removed {len(removed_gemini)} leftover Gemini file(s)")

    # Step 12: Rebuild manifest with version (no skills on main)
    _write_manifest(root, [])

    console.panel("  All managed files are up to date.", title="WADE updated")
    return True


def _maybe_self_upgrade() -> bool:
    """Upgrade WADE using the detected package manager, then re-exec.

    Checks PyPI first — only upgrades if a newer version is actually available.
    If upgrade is applied, calls re_exec() which replaces this process.
    Returns True if an upgrade was triggered, False if skipped or up to date.

    Guards against an infinite re-exec loop: immediately before re-exec'ing,
    the pre-upgrade version is recorded in WADE_SELF_UPGRADE_FROM, which
    os.execv inherits into the replacement process. On entry, if that
    breadcrumb is present this call is a post-re-exec retry, so it is always
    cleared and the function returns False without attempting another
    upgrade — regardless of whether the version actually advanced.
    """
    from wade import __version__
    from wade.utils.install import InstallMethod, detect_install_method, re_exec, self_upgrade
    from wade.utils.update_check import check_for_update

    breadcrumb = os.environ.pop("WADE_SELF_UPGRADE_FROM", None)
    if breadcrumb is not None:
        if breadcrumb == __version__:
            console.warn(
                f"self-upgrade did not change the installed version ({__version__}); "
                "the reported latest may be unavailable — continuing with "
                f"{__version__}. Use --skip-self-upgrade to skip."
            )
        else:
            logger.info(
                "_maybe_self_upgrade.upgraded", from_version=breadcrumb, to_version=__version__
            )
        return False

    method = detect_install_method()

    if method in (InstallMethod.EDITABLE, InstallMethod.UNKNOWN):
        logger.info("_maybe_self_upgrade.skipped", method=str(method))
        return False

    console.step("Checking for WADE updates...")

    latest = check_for_update(__version__, force=True)
    if not latest:
        return False  # Already at the latest version

    if self_upgrade():
        console.success("wade upgraded — restarting...")
        os.environ["WADE_SELF_UPGRADE_FROM"] = __version__
        re_exec()  # Does not return
        return True  # pragma: no cover

    console.warn("Self-upgrade failed — continuing with current version")
    return False


def deinit(project_root: Path | None = None, force: bool = False) -> bool:
    """Remove WADE from a project.

    Removes: skills, config, manifest, AGENTS.md pointer (if present).
    Also cleans stale ``.gitignore`` block for backward compat with projects
    that still have the old ``# wade:start`` committed block.
    """
    cwd = project_root or Path.cwd()

    try:
        root = repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return False

    if not force:
        from wade.ui import prompts

        if not prompts.confirm("Remove WADE from this project?"):
            console.info("Aborted.")
            return False

    console.rule("wade deinit")

    # Remove skills
    removed = installer.remove_skills(root)
    console.info(f"Removed {len(removed)} skill entries")

    # Remove AGENTS.md pointer and wade-created CLAUDE.md symlink.
    # Also cleans up .claude/settings.json and .cursor/cli.json (handles repos
    # that reach deinit without ever running wade update).
    for name in ("AGENTS.md", "CLAUDE.md"):
        target = root / name
        if target.is_symlink() and name == "CLAUDE.md":
            # Only remove if WADE created it (points to AGENTS.md)
            link_target = target.resolve()
            if link_target == (root / "AGENTS.md").resolve():
                target.unlink()
                console.info("Removed CLAUDE.md symlink")
        elif target.is_file() and pointer.remove_pointer(target):
            console.info(f"Removed workflow pointer from {name}")

    removed_ai = _migrate_ai_artifacts_off_main(root)
    for artifact in removed_ai:
        if artifact not in ("AGENTS.md", "CLAUDE.md"):
            console.info(f"Removed {artifact}")

    # Remove config
    config_path = root / ".wade.yml"
    if config_path.is_file():
        config_path.unlink()
        console.info("Removed .wade.yml")

    # Remove manifest (check both new .wade/ and legacy root locations)
    for manifest in (root / ".wade" / MANIFEST_FILENAME, root / MANIFEST_FILENAME):
        if manifest.is_file():
            manifest.unlink()
            console.info(f"Removed {manifest.relative_to(root)}")

    # Clean .gitignore
    _clean_gitignore(root)

    # Remove .wade directory (internal state: SQLite DB, audit log)
    wade_dir = root / ".wade"
    if wade_dir.is_dir():
        shutil.rmtree(wade_dir)

    console.panel("  All WADE artifacts removed.", title="WADE removed")
    return True
