"""Init service — project initialization, update, and removal.

Orchestrates: AI tool detection, config generation, skill installation,
AGENTS.md pointer, .gitignore entries, and .wade-managed manifest.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Final

import structlog
import yaml

from wade.ai_tools.base import AbstractAITool
from wade.config.defaults import get_defaults
from wade.config.loader import ConfigError, ensure_yaml_mapping, find_config_file, load_config
from wade.git import repo
from wade.git.repo import GitError
from wade.models.ai import AIToolID
from wade.models.config import (
    ComplexityModelMapping,
)
from wade.skills import installer, pointer
from wade.skills.installer import get_wade_repo_root
from wade.ui.console import console

logger = structlog.get_logger()

# Marker comments wrapping the managed block in .gitignore
GITIGNORE_MARKER_START: Final = (
    "# wade:start — managed by wade (do not edit — run `wade update` to refresh)"
)
GITIGNORE_MARKER_END: Final = "# wade:end"

# Actual patterns placed between the markers
GITIGNORE_ENTRIES: Final = [
    ".wade/",
    ".wade-managed",
    ".wade.yml",
    "PLAN.md",
    "PR-SUMMARY.md",
]

# Entries added by older versions without markers — used for backward-compat cleanup
_GITIGNORE_LEGACY_ENTRIES: Final = [
    "# wade managed files",
    ".issue-context.md",
]

MANIFEST_FILENAME = ".wade-managed"


def get_wade_root() -> Path:
    """Get the wade package root (for self-init detection).

    Delegates to installer.get_wade_repo_root() — kept as a local alias
    for backward compatibility.
    """
    return get_wade_repo_root()


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def init(
    project_root: Path | None = None,
    ai_tool: str | None = None,
    non_interactive: bool = False,
) -> bool:
    """Initialize WADE in a project.

    Steps:
    1. Validate git repo
    2. Detect/select AI tool
    3. Collect project settings
    4. Generate .wade.yml config
    5. Install skill files
    6. Update .gitignore
    7. Write AGENTS.md pointer
    8. Write .wade-managed manifest

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

    # Compute installed tools once — doesn't depend on any wizard step
    installed_tools = [str(t) for t in AbstractAITool.detect_installed()]

    # Interactive wizard — all prompts before any writes
    project_settings = _prompt_project_settings(root, non_interactive)
    hooks_setup = _prompt_hooks_setup(non_interactive)
    try:
        selected_tool, default_model, default_effort = _prompt_ai_section(ai_tool, non_interactive)
    except ValueError as exc:
        console.error(str(exc))
        return False
    work_setup = _prompt_work_setup(selected_tool, installed_tools, non_interactive)
    command_overrides = _prompt_command_overrides(
        installed_tools, non_interactive, default_model=default_model, default_tool=selected_tool
    )
    # Compute which tools are actually in use (selected + per-command overrides)
    tools_in_use: set[str] = set()
    if selected_tool:
        tools_in_use.add(selected_tool)
    if work_setup.get("tool"):
        tools_in_use.add(work_setup["tool"])
    for cmd_cfg in command_overrides.values():
        if cmd_cfg.get("tool"):
            tools_in_use.add(cmd_cfg["tool"])

    if "claude" in tools_in_use:
        _prompt_claude_code_settings(root, non_interactive)
    if "cursor" in tools_in_use:
        _prompt_cursor_settings(root, non_interactive)
    if "gemini" in tools_in_use:
        _prompt_configure_gemini_experimental(non_interactive)
    _prompt_configure_shell_integration(non_interactive)
    _prompt_configure_completions(non_interactive)

    # Write phase
    if not non_interactive:
        console.rule("Initing")

    # Generate config
    config_path = root / ".wade.yml"
    if config_path.exists():
        console.info("Config .wade.yml already exists — updating with selected values")
        _patch_config(
            config_path,
            selected_tool,
            work_setup["model_mapping"],
            default_model=default_model,
            default_effort=default_effort,
            project_settings=project_settings,
            work_tool=work_setup["tool"],
            command_overrides=command_overrides,
            hooks_setup=hooks_setup,
            force=not non_interactive,
        )
    else:
        _write_config(
            config_path,
            selected_tool,
            work_setup["model_mapping"],
            project_settings=project_settings,
            work_tool=work_setup["tool"],
            default_model=default_model,
            default_effort=default_effort,
            command_overrides=command_overrides,
            hooks_setup=hooks_setup,
        )
        console.success(f"Created {config_path.name}")

    # 5. Update .gitignore
    _ensure_gitignore(root)

    # 6. AGENTS.md pointer
    pointer_path = pointer.ensure_pointer(root)
    if pointer_path:
        console.success(f"Workflow pointer in {Path(pointer_path).name}")

    # 7. Write manifest (no skills on main — skills are installed per-session in worktrees)
    _write_manifest(root, [])
    console.success("Wrote .wade-managed manifest")

    # Validate the config we just wrote
    from wade.services.check_service import validate_config

    check_result = validate_config(root)
    if check_result.is_valid:
        console.success("Config validation passed")
    else:
        console.warn("Config validation issues:")
        for err in check_result.errors:
            console.detail(err)

    # 8. Commit or configure for local-only use
    _prompt_commit_or_local(root, config_path, [], non_interactive)

    console.panel(
        "  Project initialized. Run [bold]wade plan[/] to get started.",
        title="WADE initialized",
    )
    return True


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


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
    7.  Refresh skill files (force overwrite)
    8.  Configure Claude Code allowlist
    9.  Configure Gemini experimental (if applicable)
    10. Refresh .gitignore + AGENTS.md pointer
    11. Rebuild manifest with version

    Never overwrites .wade.yml user values — only patches missing keys
    and refreshes skill files.
    """
    from wade import __version__
    from wade.config.claude_allowlist import configure_allowlist
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

    # Step 7: Migrate old skill files off main (skills now live in worktrees only)
    is_self = root.resolve() == get_wade_root().resolve()
    if not is_self:
        removed = _migrate_skills_off_main(root)
        if removed:
            console.info(f"Migrated {len(removed)} old skill entries off main")

    # Compute which tools are actually configured for this project
    tools_in_use: set[str] = set()
    for cmd in [None, "plan", "deps", "work"]:
        t = config.get_ai_tool(cmd)
        if t:
            tools_in_use.add(t)

    # Step 8-9: Silently configure tool-specific settings (idempotent)
    if "claude" in tools_in_use:
        extra = config.permissions.allowed_commands
        configure_allowlist(root, extra_patterns=extra)
    if "cursor" in tools_in_use:
        from wade.config.cursor_allowlist import (
            configure_allowlist as configure_cursor_allowlist,
        )

        configure_cursor_allowlist(root, extra_patterns=config.permissions.allowed_commands)
    if "gemini" in tools_in_use:
        _configure_gemini_experimental()

    # Step 10: Refresh .gitignore + AGENTS.md pointer
    _ensure_gitignore(root)
    pointer.ensure_pointer(root)

    # Step 11: Rebuild manifest with version (no skills on main)
    _write_manifest(root, [])

    console.panel("  All managed files are up to date.", title="WADE updated")
    return True


def _migrate_skills_off_main(project_root: Path) -> list[str]:
    """Remove old skill files from the main checkout.

    Skills are now installed per-session in worktrees only.  This cleans up
    skill directories and cross-tool symlinks left by previous ``wade init``
    or ``wade update`` runs.

    Returns list of removed paths (relative to project root).
    """
    removed: list[str] = []
    primary_skills_dir = project_root / ".claude" / "skills"

    # Remove known skill directories
    for skill_name in installer.MANAGED_SKILL_NAMES:
        skill_dir = primary_skills_dir / skill_name
        if skill_dir.is_symlink():
            skill_dir.unlink()
            removed.append(f".claude/skills/{skill_name}")
        elif skill_dir.is_dir():
            shutil.rmtree(skill_dir)
            removed.append(f".claude/skills/{skill_name}")

    # Remove cross-tool symlinks (only symlinks — real dirs may contain user data)
    for cross_dir in installer.CROSS_TOOL_DIRS:
        cross_path = project_root / cross_dir
        if cross_path.is_symlink():
            cross_path.unlink()
            removed.append(cross_dir)
        elif cross_path.is_dir():
            logger.warning(
                "init.skip_non_symlink_cross_tool_dir",
                path=str(cross_path),
            )

    # Clean up empty parent directories
    for parent in [
        primary_skills_dir,
        project_root / ".github",
        project_root / ".agents",
        project_root / ".gemini",
        project_root / ".cursor",
    ]:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()

    return removed


def _maybe_self_upgrade() -> bool:
    """Upgrade WADE using the detected package manager, then re-exec.

    Checks PyPI first — only upgrades if a newer version is actually available.
    If upgrade is applied, calls re_exec() which replaces this process.
    Returns True if an upgrade was triggered, False if skipped or up to date.
    """
    from wade import __version__
    from wade.utils.install import InstallMethod, detect_install_method, re_exec, self_upgrade
    from wade.utils.update_check import check_for_update

    method = detect_install_method()

    if method in (InstallMethod.EDITABLE, InstallMethod.UNKNOWN):
        logger.info("_maybe_self_upgrade.skipped", method=str(method))
        return False

    console.step("Checking for WADE updates...")

    latest = check_for_update(__version__)
    if not latest:
        return False  # Already at the latest version

    if self_upgrade():
        console.success("wade upgraded — restarting...")
        re_exec()  # Does not return
        return True  # pragma: no cover

    console.warn("Self-upgrade failed — continuing with current version")
    return False


def _read_manifest_version(root: Path) -> str | None:
    """Read the WADE version from the .wade-managed manifest.

    Looks for a line like: # Managed by wade 0.1.0
    """
    import re

    manifest = root / MANIFEST_FILENAME
    if not manifest.is_file():
        return None

    text = manifest.read_text(encoding="utf-8")
    match = re.search(r"# Managed by wade\s+(\S+)", text)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Deinit
# ---------------------------------------------------------------------------


def deinit(project_root: Path | None = None, force: bool = False) -> bool:
    """Remove WADE from a project.

    Removes: skills, config, manifest, .gitignore entries, AGENTS.md pointer.
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

    # Remove AGENTS.md pointer
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

    # Remove config
    config_path = root / ".wade.yml"
    if config_path.is_file():
        config_path.unlink()
        console.info("Removed .wade.yml")

    # Remove manifest
    manifest = root / MANIFEST_FILENAME
    if manifest.is_file():
        manifest.unlink()
        console.info("Removed .wade-managed")

    # Clean .gitignore
    _clean_gitignore(root)

    # Remove .wade directory (internal state: SQLite DB, audit log)
    wade_dir = root / ".wade"
    if wade_dir.is_dir():
        shutil.rmtree(wade_dir)

    console.panel("  All WADE artifacts removed.", title="WADE removed")
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _configure_gemini_experimental() -> None:
    """Write Gemini experimental settings for plan mode support.

    Writes {"experimental":{"plan":true}} to ~/.gemini/settings.json,
    merging non-destructively with existing content.
    """
    import contextlib
    import json

    settings_path = Path.home() / ".gemini" / "settings.json"

    existing: dict[str, Any] = {}
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw

    experimental: dict[str, Any] = existing.get("experimental", {})
    if not isinstance(experimental, dict):
        experimental = {}
    if experimental.get("plan") is True:
        return  # Already configured

    experimental["plan"] = True
    existing["experimental"] = experimental

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(existing, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("gemini.experimental_configured", path=str(settings_path))


def _prompt_configure_gemini_experimental(non_interactive: bool) -> None:
    """Prompt user to enable Gemini experimental plan mode, skipping if already configured."""
    import contextlib
    import json

    from wade.ui import prompts

    settings_path = Path.home() / ".gemini" / "settings.json"

    existing: dict[str, Any] = {}
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw

    experimental = existing.get("experimental", {})
    if isinstance(experimental, dict) and experimental.get("plan") is True:
        return  # Already configured — skip section entirely

    if not non_interactive:
        console.rule("Gemini")

    if non_interactive:
        return

    if prompts.confirm(
        "Enable Gemini experimental.plan mode (required for planning)?",
        default=True,
    ):
        _configure_gemini_experimental()
        console.success("Enabled experimental.plan in ~/.gemini/settings.json")


def _prompt_configure_allowlist(root: Path, non_interactive: bool) -> None:
    """Prompt user to configure the Claude Code allowlist, skipping if already set."""
    from wade.config.claude_allowlist import configure_allowlist, is_allowlist_configured
    from wade.ui import prompts

    if is_allowlist_configured(root):
        return

    if non_interactive:
        return

    if prompts.confirm(
        "Auto-approve wade commands in Claude Code? (skips Bash approval in work sessions)",
        default=True,
    ):
        extra = _build_permissions_commands(root)
        configure_allowlist(root, extra_patterns=extra)
        console.success("Added Bash([step]wade[/] *) to .claude/settings.json allowlist")


def _prompt_cursor_settings(root: Path, non_interactive: bool) -> None:
    """Prompt for Cursor CLI-specific settings: command allowlist."""
    from wade.config.cursor_allowlist import configure_allowlist, is_allowlist_configured
    from wade.ui import prompts

    if is_allowlist_configured(root):
        return

    if non_interactive:
        return

    console.rule("Cursor CLI")
    if prompts.confirm(
        "Auto-approve wade commands in Cursor CLI? (skips Shell approval in work sessions)",
        default=True,
    ):
        extra = _build_permissions_commands(root)
        configure_allowlist(root, extra_patterns=extra)
        console.success("Added Shell([step]wade[/] *) to .cursor/cli.json allowlist")


def _configure_statusline() -> None:
    """Install Claude Code statusline script and register it in ~/.claude/settings.json.

    Copies templates/statusline-command.sh to ~/.claude/statusline-command.sh and
    writes ``{"statusLine": {"type": "command", ...}}`` into ~/.claude/settings.json,
    merging non-destructively with existing content.
    """
    import contextlib
    import json

    from wade.skills.installer import get_templates_dir

    dest = Path.home() / ".claude" / "statusline-command.sh"
    script_src = get_templates_dir() / "statusline-command.sh"

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(script_src.read_text(encoding="utf-8"), encoding="utf-8")

    settings_path = Path.home() / ".claude" / "settings.json"
    existing: dict[str, Any] = {}
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw

    existing["statusLine"] = {
        "type": "command",
        "command": "bash ~/.claude/statusline-command.sh",
    }
    settings_path.write_text(
        json.dumps(existing, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("claude.statusline_configured", path=str(dest))


def _prompt_configure_statusline(non_interactive: bool) -> None:
    """Prompt user to install Claude Code statusline, skipping if already configured."""
    import contextlib
    import json

    from wade.ui import prompts

    settings_path = Path.home() / ".claude" / "settings.json"
    existing: dict[str, Any] = {}
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw

    if "statusLine" in existing:
        return  # Already configured — skip silently (idempotent)

    if non_interactive:
        return

    if prompts.confirm(
        "Install Claude Code statusline script (token counts + context bar)?",
        default=True,
    ):
        _configure_statusline()
        console.success("Installed ~/.claude/statusline-command.sh")


def _get_shell_profile(shell_env: str) -> Path | None:
    """Detect shell profile path from SHELL env var.

    Returns:
        Path to the shell profile file, or None if shell is unknown.
    """
    import sys

    # Extract shell name from full path (e.g., /bin/zsh -> zsh)
    shell_name = Path(shell_env).name.lower()

    if "zsh" in shell_name:
        return Path.home() / ".zshrc"
    elif "fish" in shell_name:
        return Path.home() / ".config" / "fish" / "config.fish"
    elif "bash" in shell_name:
        # bash uses different profile on macOS vs Linux
        if sys.platform == "darwin":
            return Path.home() / ".bash_profile"
        else:
            return Path.home() / ".bashrc"
    else:
        return None


def _is_shell_integration_configured(profile: Path) -> bool:
    """Check if shell integration is already in the profile file."""
    if not profile.is_file():
        return False
    try:
        content = profile.read_text(encoding="utf-8")
        return "wade shell-init" in content
    except (OSError, UnicodeDecodeError):
        return False


def _configure_shell_integration(profile: Path, is_fish: bool) -> None:
    """Append shell integration to the profile file.

    Creates the profile if it doesn't exist.
    """
    line = "wade shell-init | source" if is_fish else 'eval "$(wade shell-init)"'
    block = f"\n# wade shell integration\n{line}\n"

    profile.parent.mkdir(parents=True, exist_ok=True)

    if profile.is_file():
        existing = profile.read_text(encoding="utf-8")
        profile.write_text(existing.rstrip("\n") + block, encoding="utf-8")
    else:
        profile.write_text(block.lstrip("\n"), encoding="utf-8")

    logger.info("shell.integration_configured", path=str(profile))


def _prompt_configure_shell_integration(non_interactive: bool) -> None:
    """Prompt user to add shell integration, skipping if already configured."""
    from wade.ui import prompts

    shell_env = os.environ.get("SHELL", "/bin/bash")
    profile = _get_shell_profile(shell_env)

    # If we can't detect the shell, show manual instructions
    if profile is None:
        shell_name = Path(shell_env).name
        console.hint(
            f"Shell '{shell_name}' not auto-detected. "
            f"To enable 'wade cd', add to your shell profile:\n"
            f'  eval "$(wade shell-init)"'
        )
        return

    # If already configured, skip section entirely
    if _is_shell_integration_configured(profile):
        return

    # If non-interactive, skip silently
    if non_interactive:
        return

    console.rule("Shell")

    # Prompt the user
    is_fish = "fish" in shell_env.lower()
    if prompts.confirm(
        "Add shell integration for 'wade cd' (changes to shell profile)?",
        default=True,
    ):
        _configure_shell_integration(profile, is_fish=is_fish)
        console.success(f"Added shell integration to {profile}")


def _prompt_configure_completions(non_interactive: bool) -> None:
    """Prompt user to install CLI autocompletions."""
    import subprocess
    import sys

    from wade.ui import prompts

    if non_interactive or not prompts.is_tty():
        return

    if prompts.confirm("Install CLI autocompletion for wade?", default=True):
        try:
            subprocess.run(
                [sys.executable, "-m", "wade", "--install-completion"],
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
            console.success("Installed CLI autocompletion")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.warning("init.completion_failed", error=getattr(exc, "stderr", None))
            console.warn("Could not install CLI autocompletion")


def _prompt_claude_code_settings(root: Path, non_interactive: bool) -> None:
    """Prompt for Claude Code-specific settings: allowlist and statusline."""
    import contextlib
    import json

    from wade.config.claude_allowlist import is_allowlist_configured

    # Pre-check: skip entire section if everything is already configured
    allowlist_done = is_allowlist_configured(root)
    statusline_done = False
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "statusLine" in raw:
                statusline_done = True
    if allowlist_done and statusline_done:
        return

    if not non_interactive:
        console.rule("Claude Code")
    _prompt_configure_allowlist(root, non_interactive)
    _prompt_configure_statusline(non_interactive)


def _select_ai_tool(
    requested: str | None,
    non_interactive: bool,
) -> str | None:
    """Select an AI tool — from argument, detection, or interactive prompt."""
    from wade.ui import prompts

    if requested:
        try:
            AIToolID(requested)
            return requested
        except ValueError:
            valid = ", ".join(e.value for e in AIToolID)
            raise ValueError(f"Unknown AI tool: {requested!r} (valid: {valid})") from None

    # Detect installed tools
    installed = AbstractAITool.detect_installed()

    if not installed:
        console.warn("No AI CLI tools detected. You can set 'ai_tool' in .wade.yml later.")
        return None

    if len(installed) == 1:
        tool = installed[0]
        console.success(f"Detected AI tool: {tool}")
        return tool

    if non_interactive:
        tool = installed[0]
        console.info(f"Using AI tool: {tool}")
        return tool

    # Interactive: show menu with Skip option (rule shown by _prompt_ai_section)
    skip_label = "Skip (configure later)"
    items = [str(t) for t in installed] + [skip_label]
    idx = prompts.select("Select default AI tool", items)

    if items[idx] == skip_label:
        return None

    selected = installed[idx]
    console.success(f"Selected AI tool: {selected}")
    return str(selected)


def _prompt_ai_section(
    ai_tool: str | None,
    non_interactive: bool,
) -> tuple[str | None, str | None, str | None]:
    """Run the AI wizard section: select default tool, model, and effort.

    The rule is shown here (before any detection messages) so both the
    single-tool and multi-tool cases are grouped under the same header.

    Returns ``(selected_tool, default_model, default_effort)``.
    """
    if not non_interactive:
        console.rule("AI")
    selected_tool = _select_ai_tool(ai_tool, non_interactive)
    if non_interactive or not selected_tool:
        return selected_tool, None, None
    mapping = _resolve_models(selected_tool)
    default_model = _prompt_default_model(selected_tool, mapping, non_interactive=False)

    # Prompt for default effort level (only when tool supports it)
    default_effort: str | None = None
    try:
        adapter = AbstractAITool.get(selected_tool)
        if adapter.capabilities().supports_effort:
            from wade.models.ai import EffortLevel
            from wade.ui import prompts as ui_prompts

            effort_choices = ["(none — use tool default)", *[e.value for e in EffortLevel]]
            idx = ui_prompts.select("Default reasoning effort level", effort_choices)
            # "" sentinel signals explicit "none" so _patch_config can clear on force
            default_effort = effort_choices[idx] if idx > 0 else ""
    except (ValueError, KeyError):
        pass

    return selected_tool, default_model, default_effort


def _resolve_models(tool: str | None) -> ComplexityModelMapping:
    """Resolve model mappings for a tool from hardcoded defaults.

    Returns:
        Normalized complexity-to-model mapping for the tool.
    """
    if not tool:
        return ComplexityModelMapping()
    return _normalize_mapping(get_defaults(tool))


def _normalize_model(model_id: str | None) -> str | None:
    """Normalize a single model ID: fix deprecated names and ensure dot notation.

    Config always stores dot notation (claude-haiku-4.5). Tool-specific
    conversion (e.g., dashes for Claude CLI) happens at launch time.
    """
    if not model_id:
        return None
    import re

    # Ensure dot notation for Claude models (claude-haiku-4-5 → claude-haiku-4.5)
    if model_id.startswith("claude-"):
        model_id = re.sub(r"(\d)-(\d)", r"\1.\2", model_id)
    return model_id


def _normalize_mapping(
    mapping: ComplexityModelMapping,
) -> ComplexityModelMapping:
    """Normalize model IDs: fix deprecated names and standardize to dot notation.

    Config always stores dot notation. Tool-specific conversion happens at launch.
    """
    return ComplexityModelMapping(
        easy=_normalize_model(mapping.easy),
        medium=_normalize_model(mapping.medium),
        complex=_normalize_model(mapping.complex),
        very_complex=_normalize_model(mapping.very_complex),
    )


def _prompt_project_settings(
    project_root: Path,
    non_interactive: bool,
) -> dict[str, str]:
    """Collect project settings — interactively or with defaults.

    Auto-detects the main branch via git. Returns a dict with keys:
    main_branch, merge_strategy, branch_prefix, issue_label, worktrees_dir.
    """
    from wade.ui import prompts

    # Auto-detect main branch (works in both modes)
    try:
        main_branch = repo.detect_main_branch(project_root)
    except Exception:
        logger.debug("init.main_branch_detect_failed", exc_info=True)
        main_branch = "main"

    defaults = {
        "main_branch": main_branch,
        "merge_strategy": "PR",
        "branch_prefix": "feat",
        "issue_label": "feature-plan",
        "worktrees_dir": "../.worktrees",
    }

    if non_interactive:
        return defaults

    console.rule("Project")

    merge_options = ["PR", "direct"]
    merge_default = (
        merge_options.index(defaults["merge_strategy"])
        if defaults["merge_strategy"] in merge_options
        else 0
    )
    merge_idx = prompts.select("Merge strategy", merge_options, default=merge_default)
    defaults["merge_strategy"] = merge_options[merge_idx]
    defaults["branch_prefix"] = prompts.input_prompt("Branch prefix", defaults["branch_prefix"])
    defaults["issue_label"] = prompts.input_prompt("Issue label", defaults["issue_label"])
    defaults["worktrees_dir"] = prompts.input_prompt(
        "Worktrees directory", defaults["worktrees_dir"]
    )

    return defaults


def _prompt_hooks_setup(
    non_interactive: bool,
) -> dict[str, Any]:
    """Collect worktree hooks settings — setup script and files to copy.

    Returns a dict with keys: post_worktree_create (str | None), copy_to_worktree (list[str]).
    """
    from wade.ui import prompts

    defaults: dict[str, Any] = {
        "post_worktree_create": None,
        "copy_to_worktree": [],
    }

    if non_interactive:
        return defaults

    console.rule("Worktree hooks")

    script_path = prompts.input_prompt(
        "Setup script for new worktrees (e.g. scripts/setup-worktree.sh)",
        default="",
        allow_empty=True,
    )
    if script_path.strip():
        defaults["post_worktree_create"] = script_path.strip()

    copy_files = prompts.input_prompt(
        "Files to copy into worktrees (comma-separated, e.g. .env)",
        default="",
        allow_empty=True,
    )
    if copy_files.strip():
        defaults["copy_to_worktree"] = [f.strip() for f in copy_files.split(",") if f.strip()]

    return defaults


def _prompt_model_mapping(
    tool: str | None,
    mapping: ComplexityModelMapping,
    non_interactive: bool,
) -> ComplexityModelMapping:
    """Let the user review/edit the complexity-to-model mapping.

    If non_interactive, returns the mapping unchanged.
    Falls back to Claude defaults when the tool has no model suggestions.

    Shows a select menu with available models for each tier, plus a Custom
    option for typing a model name directly.
    """
    from wade.ui import prompts

    if non_interactive:
        return mapping

    # Backfill missing tiers: tool defaults first, then Claude defaults
    tool_defaults = get_defaults(tool) if tool else ComplexityModelMapping()
    claude_defaults = get_defaults(AIToolID.CLAUDE)
    mapping = ComplexityModelMapping(
        easy=mapping.easy or tool_defaults.easy or claude_defaults.easy,
        medium=mapping.medium or tool_defaults.medium or claude_defaults.medium,
        complex=mapping.complex or tool_defaults.complex or claude_defaults.complex,
        very_complex=(
            mapping.very_complex or tool_defaults.very_complex or claude_defaults.very_complex
        ),
    )

    # Collect model IDs from the registry for the select menu
    available = _collect_model_options(tool)

    custom_label = "Custom..."
    tiers = [
        ("Easy tasks", mapping.easy or ""),
        ("Medium tasks", mapping.medium or ""),
        ("Complex tasks", mapping.complex or ""),
        ("Very complex tasks", mapping.very_complex or ""),
    ]

    results: list[str] = []

    for tier_label, tier_default in tiers:
        options = list(available)
        if tier_default and tier_default not in options:
            options.insert(0, tier_default)
        options.append(custom_label)

        default_idx = options.index(tier_default) if tier_default in options else 0
        chosen_idx = prompts.select(tier_label, options, default=default_idx)

        if options[chosen_idx] == custom_label:
            results.append(prompts.input_prompt(f"{tier_label} (model ID)", tier_default))
        else:
            results.append(options[chosen_idx])

    return ComplexityModelMapping(
        easy=results[0] or mapping.easy,
        medium=results[1] or mapping.medium,
        complex=results[2] or mapping.complex,
        very_complex=results[3] or mapping.very_complex,
    )


def _collect_model_options(
    tool: str | None,
) -> list[str]:
    """Return the full flat model list from the registry for the select menu."""
    if not tool:
        return []
    from wade.data import get_models_for_tool

    return get_models_for_tool(tool)


def _prompt_default_model(
    tool: str | None,
    model_mapping: ComplexityModelMapping,
    non_interactive: bool,
) -> str | None:
    """Prompt the user to select a default model for the AI tool.

    This is the fallback model used when no complexity tier is matched and
    no --model flag is passed. It is written to ai.{plan,deps,work}.model
    for any command that does not have an explicit model override.

    The complexity tier mapping (easy/medium/complex/very_complex) is left
    unchanged.

    Returns the chosen model ID, or None if the user skips.
    """
    from wade.ui import prompts

    if non_interactive or not tool:
        return None

    available = _collect_model_options(tool)
    if not available:
        return None

    skip_label = "Skip (configure per-complexity)"
    options = [*available, skip_label]

    # Pre-select the "complex" tier model as a sensible starting default
    default_idx = 0
    if model_mapping.complex and model_mapping.complex in options:
        default_idx = options.index(model_mapping.complex)

    idx = prompts.select(f"Select default model for {tool}", options, default=default_idx)
    if options[idx] == skip_label:
        return None

    selected = options[idx]
    console.success(f"Selected model: {selected}")
    return selected


def _suggest_model_for_tool(tool: str) -> str:
    """Get a suggested model for a tool — uses defaults, skips slow probing.

    During interactive init, probing has already happened in _resolve_models().
    Here we just need a quick suggestion, so we use cached defaults only.
    """
    defaults = get_defaults(tool)
    if defaults.complex:
        return defaults.complex

    # Fallback to Claude defaults for unknown tools
    fallback = get_defaults(AIToolID.CLAUDE)
    return fallback.complex or ""


def _prompt_work_setup(
    default_tool: str | None,
    installed_tools: list[str],
    non_interactive: bool,
) -> dict[str, Any]:
    """Prompt for implementation-work tool and per-complexity model overrides.

    The default tool and default model are set in the AI section. This section
    only handles implementation-specific overrides that fall back to those defaults.

    Returns a dict with keys:
        ``tool``          - work-specific tool override, or None (use default_tool)
        ``model_mapping`` - ComplexityModelMapping for the effective tool
    """
    if non_interactive:
        mapping = _resolve_models(default_tool)
        return {"tool": None, "model_mapping": mapping}

    from wade.ui import prompts

    console.rule("Implementation")

    skip_label = "Skip (use default)"
    tool_options = (installed_tools if installed_tools else ["claude", "copilot", "gemini"]) + [
        skip_label
    ]

    idx = prompts.select(
        "AI tool for implementation work", tool_options, default=len(tool_options) - 1
    )
    work_tool = None if tool_options[idx] == skip_label else tool_options[idx]

    current_effective = work_tool or default_tool
    mapping = _resolve_models(current_effective)
    mapping = _prompt_model_mapping(current_effective, mapping, non_interactive=False)

    return {"tool": work_tool, "model_mapping": mapping}


def _prompt_command_overrides(
    installed_tools: list[str],
    non_interactive: bool,
    default_model: str | None = None,
    default_tool: str | None = None,
) -> dict[str, dict[str, str]]:
    """Prompt for per-command AI tool and model overrides.

    Work configuration is handled separately by ``_prompt_work_setup()``.

    Returns a dict like:
        {"plan": {"tool": "claude", "model": "..."}, "deps": {},
         "review_plan": {"mode": "prompt"}, "review_implementation": {"mode": "headless"}}

    Empty dicts for commands with no overrides.
    """
    from wade.ui import prompts

    if non_interactive:
        return {"plan": {}, "deps": {}, "review_plan": {}, "review_implementation": {}}

    # Build selectable list: installed tools + "Skip (use default)"
    skip_label = "Skip (use default)"
    tool_options = (installed_tools if installed_tools else ["claude", "copilot", "gemini"]) + [
        skip_label
    ]

    cmd_triples = [
        ("plan", "AI tool", "Planning"),
        ("deps", "AI tool", "Dependency analysis"),
        ("review_plan", "AI tool", "Plan review"),
        ("review_implementation", "AI tool", "Implementation review"),
    ]
    result: dict[str, dict[str, str]] = {
        "plan": {},
        "deps": {},
        "review_plan": {},
        "review_implementation": {},
    }
    tool_for_cmd: list[str | None] = [None] * len(cmd_triples)
    for cmd_idx, (cmd_name, prompt_label, section) in enumerate(cmd_triples):
        console.rule(section)
        idx = prompts.select(prompt_label, tool_options, default=len(tool_options) - 1)
        selected_tool = tool_options[idx]
        tool_for_cmd[cmd_idx] = None if selected_tool == skip_label else selected_tool
        result[cmd_name] = {}

        if tool_for_cmd[cmd_idx] is not None:
            result[cmd_name]["tool"] = selected_tool
            maybe_tool = selected_tool

            available = _collect_model_options(maybe_tool)
            suggested = _suggest_model_for_tool(maybe_tool)
            if default_model and default_model in available:
                model_default = default_model
            else:
                model_default = suggested

            custom_label = "Custom..."
            skip_model_label = "Skip (use default)"
            model_options = list(available)
            if model_default and model_default not in model_options:
                model_options.insert(0, model_default)
            model_options += [custom_label, skip_model_label]

            default_idx = (
                model_options.index(model_default) if model_default in model_options else 0
            )
            chosen_idx = prompts.select(
                f"  Model for {section.lower()}", model_options, default=default_idx
            )

            chosen = model_options[chosen_idx]
            if chosen == custom_label:
                chosen = prompts.input_prompt(
                    f"  Model for {section.lower()} (model ID)", model_default
                )
            if chosen and chosen != skip_model_label:
                result[cmd_name]["model"] = chosen

        # For delegation-aware commands, prompt for delegation mode
        if cmd_name.startswith("review_") or cmd_name == "deps":
            effective_tool = tool_for_cmd[cmd_idx] or default_tool
            if effective_tool:
                mode_options = [
                    "headless (AI one-shot)",
                    "interactive (AI session)",
                    "prompt (self-review)",
                ]
                mode_values = ["headless", "interactive", "prompt"]
                # deps defaults to headless; review defaults to prompt
                default_mode_idx = 2 if cmd_name.startswith("review_") else 0
            else:
                mode_options = ["prompt (self-review)"]
                mode_values = ["prompt"]
                default_mode_idx = 0
            mode_idx = prompts.select(
                f"  Delegation mode for {section.lower()}",
                mode_options,
                default=default_mode_idx,
            )
            result[cmd_name]["mode"] = mode_values[mode_idx]

    return result


def _detect_scripts(project_root: Path) -> list[str]:
    """Auto-detect executable scripts in the project's scripts/ directory.

    Returns canonical command patterns (e.g. ``"./scripts/check.sh *"``).
    """
    scripts_dir = project_root / "scripts"
    if not scripts_dir.is_dir():
        return []
    patterns: list[str] = []
    for sh_file in sorted(scripts_dir.glob("*.sh")):
        patterns.append(f"./{sh_file.relative_to(project_root)} *")
    return patterns


def _build_permissions_commands(project_root: Path) -> list[str]:
    """Build the full allowed_commands list: default + detected scripts."""
    commands = ["wade *"]
    commands.extend(_detect_scripts(project_root))
    return commands


def _write_config(
    config_path: Path,
    ai_tool: str | None,
    model_mapping: ComplexityModelMapping,
    project_settings: dict[str, str] | None = None,
    work_tool: str | None = None,
    default_model: str | None = None,
    default_effort: str | None = None,
    command_overrides: dict[str, dict[str, str]] | None = None,
    hooks_setup: dict[str, Any] | None = None,
) -> None:
    """Write a fresh .wade.yml config file."""
    config_dict: dict[str, Any] = {"version": 2}

    if project_settings:
        config_dict["project"] = {
            "main_branch": project_settings.get("main_branch", "main"),
            "issue_label": project_settings.get("issue_label", "feature-plan"),
            "worktrees_dir": project_settings.get("worktrees_dir", "../.worktrees"),
            "branch_prefix": project_settings.get("branch_prefix", "feat"),
            "merge_strategy": project_settings.get("merge_strategy", "PR"),
        }
    else:
        config_dict["project"] = {
            "main_branch": "main",
            "issue_label": "feature-plan",
            "worktrees_dir": "../.worktrees",
            "branch_prefix": "feat",
            "merge_strategy": "PR",
        }

    ai_section: dict[str, Any] = {}
    if ai_tool:
        ai_section["default_tool"] = str(ai_tool)
    if default_model:
        ai_section["default_model"] = default_model
    if default_effort:
        ai_section["effort"] = default_effort

    # Write work tool override (only when different from default_tool)
    if work_tool and work_tool != ai_tool:
        ai_section["work"] = {"tool": work_tool}

    # Write per-command overrides (plan, deps, review_plan, review_implementation)
    if command_overrides:
        for cmd_name in ("plan", "deps", "review_plan", "review_implementation"):
            overrides = command_overrides.get(cmd_name, {})
            if overrides:
                cmd_section: dict[str, str] = {}
                if overrides.get("tool"):
                    cmd_section["tool"] = overrides["tool"]
                if overrides.get("model"):
                    cmd_section["model"] = overrides["model"]
                if overrides.get("mode"):
                    cmd_section["mode"] = overrides["mode"]
                if cmd_section:
                    ai_section[cmd_name] = cmd_section

    config_dict["ai"] = ai_section

    # models section keyed by work tool (or default tool)
    models_key = work_tool or ai_tool
    if models_key and (model_mapping.easy or model_mapping.complex):
        config_dict["models"] = {
            str(models_key): {
                k: v
                for k, v in {
                    "easy": model_mapping.easy,
                    "medium": model_mapping.medium,
                    "complex": model_mapping.complex,
                    "very_complex": model_mapping.very_complex,
                }.items()
                if v
            }
        }

    config_dict["provider"] = {"name": "github"}

    # Build permissions section with auto-detected scripts
    allowed_commands = _build_permissions_commands(config_path.parent)
    if len(allowed_commands) > 1:
        # Only write section when there are extra patterns beyond default
        config_dict["permissions"] = {"allowed_commands": allowed_commands}

    # Build hooks section — always include .wade.yml in copy_to_worktree
    hooks_dict: dict[str, Any] = {}
    if hooks_setup and hooks_setup.get("post_worktree_create"):
        hooks_dict["post_worktree_create"] = hooks_setup["post_worktree_create"]
    copy_files: list[str] = list(hooks_setup.get("copy_to_worktree", [])) if hooks_setup else []
    if ".wade.yml" not in copy_files:
        copy_files.append(".wade.yml")
    hooks_dict["copy_to_worktree"] = copy_files
    config_dict["hooks"] = hooks_dict

    config_path.write_text(
        yaml.dump(config_dict, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _patch_config(
    config_path: Path,
    ai_tool: str | None,
    model_mapping: ComplexityModelMapping,
    default_model: str | None = None,
    default_effort: str | None = None,
    project_settings: dict[str, str] | None = None,
    work_tool: str | None = None,
    command_overrides: dict[str, dict[str, str]] | None = None,
    hooks_setup: dict[str, Any] | None = None,
    force: bool = False,
) -> None:
    """Patch values into an existing config.

    When force=True, user-selected values overwrite existing entries.
    When force=False (default), only missing keys are filled in.
    """
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return

    try:
        validated = ensure_yaml_mapping(raw)
        raw = validated if validated is not None else {}
    except ConfigError:
        return

    changed = False

    # Ensure version
    if "version" not in raw:
        raw["version"] = 2
        changed = True

    # Patch project settings
    if project_settings:
        project = raw.get("project", {}) or {}
        for key in (
            "main_branch",
            "issue_label",
            "worktrees_dir",
            "branch_prefix",
            "merge_strategy",
        ):
            value = project_settings.get(key)
            if value and (force or not project.get(key)):
                project[key] = value
                changed = True
        raw["project"] = project

    # Patch AI tool and default model
    ai = raw.get("ai", {}) or {}
    if ai_tool and (force or not ai.get("default_tool")):
        ai["default_tool"] = str(ai_tool)
        raw["ai"] = ai
        changed = True
    if default_model and (force or not ai.get("default_model")):
        ai["default_model"] = default_model
        raw["ai"] = ai
        changed = True
    if default_effort is not None:
        if default_effort == "":  # Sentinel: user explicitly cleared effort
            if force and "effort" in ai:
                del ai["effort"]
                raw["ai"] = ai
                changed = True
        elif force or not ai.get("effort"):
            ai["effort"] = default_effort
            raw["ai"] = ai
            changed = True

    # Patch work tool override
    if work_tool:
        if force:
            if work_tool != ai_tool:
                ai["work"] = {"tool": work_tool}
                changed = True
            elif "work" in ai:
                del ai["work"]
                changed = True
            raw["ai"] = ai
        elif work_tool != ai_tool and not ai.get("work"):
            ai["work"] = {"tool": work_tool}
            raw["ai"] = ai
            changed = True

    # Patch command overrides (plan, deps)
    if command_overrides is not None:
        for cmd_name in ("plan", "deps"):
            overrides = command_overrides.get(cmd_name, {})
            if force:
                if overrides:
                    cmd_section: dict[str, str] = {}
                    if overrides.get("tool"):
                        cmd_section["tool"] = overrides["tool"]
                    if overrides.get("model"):
                        cmd_section["model"] = overrides["model"]
                    if cmd_section:
                        ai[cmd_name] = cmd_section
                        raw["ai"] = ai
                        changed = True
                elif cmd_name in ai:
                    del ai[cmd_name]
                    raw["ai"] = ai
                    changed = True
            elif overrides and not ai.get(cmd_name):
                cmd_section = {}
                if overrides.get("tool"):
                    cmd_section["tool"] = overrides["tool"]
                if overrides.get("model"):
                    cmd_section["model"] = overrides["model"]
                if cmd_section:
                    ai[cmd_name] = cmd_section
                    raw["ai"] = ai
                    changed = True

    # Patch models — keyed by work_tool when provided (matching _write_config)
    tool_key = str(work_tool or ai_tool) if (work_tool or ai_tool) else None
    has_any_model = any(
        getattr(model_mapping, k, None) for k in ("easy", "medium", "complex", "very_complex")
    )
    if tool_key and has_any_model:
        models = raw.get("models", {}) or {}
        tool_models = models.get(tool_key, {}) or {}

        for key in ("easy", "medium", "complex", "very_complex"):
            value = getattr(model_mapping, key, None)
            if value and (force or not tool_models.get(key)):
                tool_models[key] = value
                changed = True

        if tool_models:
            models[tool_key] = tool_models
            raw["models"] = models

    # Patch hooks — setup script and copy_to_worktree
    hooks = raw.get("hooks") or {}
    if hooks_setup:
        script = hooks_setup.get("post_worktree_create")
        if script and (force or not hooks.get("post_worktree_create")):
            hooks["post_worktree_create"] = script
            changed = True
        elif not script and force and "post_worktree_create" in hooks:
            del hooks["post_worktree_create"]
            changed = True
        user_files: list[str] = hooks_setup.get("copy_to_worktree", [])
        if user_files and (force or not hooks.get("copy_to_worktree")):
            existing_copy: list[str] = hooks.get("copy_to_worktree") or []
            for f in user_files:
                if f not in existing_copy:
                    existing_copy.append(f)
                    changed = True
            hooks["copy_to_worktree"] = existing_copy
    # Always ensure .wade.yml is in copy_to_worktree
    copy_list: list[str] = hooks.get("copy_to_worktree") or []
    if ".wade.yml" not in copy_list:
        copy_list.append(".wade.yml")
        changed = True
    hooks["copy_to_worktree"] = copy_list
    raw["hooks"] = hooks

    if changed:
        config_path.write_text(
            yaml.dump(raw, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("config.patched", path=str(config_path))


def _gitignore_block() -> str:
    """Build the full managed block (markers + entries)."""
    inner = "\n".join(GITIGNORE_ENTRIES)
    return f"{GITIGNORE_MARKER_START}\n{inner}\n{GITIGNORE_MARKER_END}\n"


def _ensure_gitignore(project_root: Path) -> None:
    """Add or refresh the wade-managed block in .gitignore.

    Uses start/end markers so the block can be reliably located on every
    subsequent init or update.  Existing user content is always preserved.

    Migration: if the file already has old-style entries (no markers), they
    are removed and replaced with the marker-wrapped block.
    """
    from wade.utils.markdown import extract_marker_block, has_marker_block, remove_marker_block

    gitignore = project_root / ".gitignore"
    block = _gitignore_block()

    if not gitignore.is_file():
        gitignore.write_text(block, encoding="utf-8")
        return

    existing = gitignore.read_text(encoding="utf-8")

    if has_marker_block(existing, GITIGNORE_MARKER_START, GITIGNORE_MARKER_END):
        # Staleness check — skip file write if content is already current
        inner = extract_marker_block(existing, GITIGNORE_MARKER_START, GITIGNORE_MARKER_END)
        if inner == "\n".join(GITIGNORE_ENTRIES):
            return
        # Stale — replace the block in place
        cleaned = remove_marker_block(existing, GITIGNORE_MARKER_START, GITIGNORE_MARKER_END)
        base = cleaned.rstrip("\n")
        gitignore.write_text((base + "\n\n" + block) if base else block, encoding="utf-8")
        return

    # No markers yet — migrate: strip old-style stray entries, then append block
    all_legacy = set(_GITIGNORE_LEGACY_ENTRIES) | set(GITIGNORE_ENTRIES)
    lines = existing.splitlines()
    filtered = [line for line in lines if line not in all_legacy]

    # Collapse consecutive blank lines left behind by removed entries
    deduped: list[str] = []
    for line in filtered:
        if line.strip() == "" and deduped and deduped[-1].strip() == "":
            continue
        deduped.append(line)

    base = "\n".join(deduped).rstrip("\n")
    gitignore.write_text((base + "\n\n" + block) if base else block, encoding="utf-8")


def _clean_gitignore(project_root: Path) -> None:
    """Remove the wade-managed block from .gitignore.

    Primary: marker-based removal (reliable, preserves user content).
    Fallback: line-by-line removal of known entries (backward compat for
    projects initialized before markers were introduced).
    """
    from wade.utils.markdown import has_marker_block, remove_marker_block

    gitignore = project_root / ".gitignore"
    if not gitignore.is_file():
        return

    existing = gitignore.read_text(encoding="utf-8")

    if has_marker_block(existing, GITIGNORE_MARKER_START, GITIGNORE_MARKER_END):
        new_content = remove_marker_block(existing, GITIGNORE_MARKER_START, GITIGNORE_MARKER_END)
        gitignore.write_text(new_content, encoding="utf-8")
        return

    # Fallback: remove individual known entries line-by-line
    all_known = set(GITIGNORE_ENTRIES) | set(_GITIGNORE_LEGACY_ENTRIES)
    lines = existing.splitlines()
    new_lines = [line for line in lines if line not in all_known]

    # Collapse consecutive blank lines
    cleaned: list[str] = []
    for line in new_lines:
        if line.strip() == "" and cleaned and cleaned[-1].strip() == "":
            continue
        cleaned.append(line)

    content = "\n".join(cleaned).rstrip() + "\n" if cleaned else ""
    gitignore.write_text(content, encoding="utf-8")


def _write_manifest(project_root: Path, installed_files: list[str]) -> None:
    """Write the .wade-managed manifest listing all managed files."""
    from wade import __version__

    manifest = project_root / MANIFEST_FILENAME
    lines = [".wade.yml", *installed_files]
    lines.append(f"# Managed by wade {__version__}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prompt_commit_or_local(
    root: Path,
    config_path: Path,
    installed: list[str],
    non_interactive: bool,
) -> None:
    """Ask whether to commit wade files or keep them local.

    If the user commits, all wade files are added and committed.
    Otherwise the user is reminded to commit ``.gitignore``.
    ``.wade.yml`` is always in ``copy_to_worktree`` (handled during config write).
    """
    from wade.ui import prompts

    if non_interactive or not prompts.is_tty():
        console.hint("Commit at least .gitignore so worktrees stay clean:")
        console.detail('git add .gitignore && git commit -m "chore: add wade gitignore entries"')
        return

    commit = prompts.confirm("Commit wade files now?", default=True)

    if commit:
        _commit_wade_files(root, installed)
    else:
        console.hint("Commit at least .gitignore so worktrees stay clean:")
        console.detail('git add .gitignore && git commit -m "chore: add wade gitignore entries"')


def _commit_wade_files(root: Path, installed: list[str]) -> None:
    """Stage and commit all wade-managed files."""
    import subprocess

    files_to_add = [".wade.yml", ".gitignore", MANIFEST_FILENAME]
    files_to_add.extend(installed)
    if (root / "AGENTS.md").exists():
        files_to_add.append("AGENTS.md")

    try:
        subprocess.run(
            ["git", "add", "--force", "--", *files_to_add],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "chore: initialize wade"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        console.success("Committed wade files")
    except subprocess.CalledProcessError as exc:
        logger.warning("init.commit_failed", error=exc.stderr)
        console.warn("Could not commit wade files — commit them manually")
