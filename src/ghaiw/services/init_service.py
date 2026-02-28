"""Init service — project initialization, update, and removal.

Orchestrates: AI tool detection, config generation, skill installation,
AGENTS.md pointer, .gitignore entries, and .ghaiw-managed manifest.

Behavioral reference: lib/init.sh (ghaiw_init, ghaiw_update, ghaiw_deinit)
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Final

import structlog
import yaml

from ghaiw.ai_tools.base import AbstractAITool
from ghaiw.config.defaults import get_defaults
from ghaiw.config.loader import find_config_file, load_config
from ghaiw.git import repo
from ghaiw.git.repo import GitError
from ghaiw.models.ai import AIToolID
from ghaiw.models.config import (
    ComplexityModelMapping,
)
from ghaiw.skills import installer, pointer
from ghaiw.ui.console import console

logger = structlog.get_logger()

# Marker comments wrapping the managed block in .gitignore
GITIGNORE_MARKER_START: Final = (
    "# ghaiw:start — managed by ghaiw (do not edit — run `ghaiw update` to refresh)"
)
GITIGNORE_MARKER_END: Final = "# ghaiw:end"

# Actual patterns placed between the markers
GITIGNORE_ENTRIES: Final = [
    ".ghaiw/",
    ".ghaiw-managed",
    "PLAN.md",
    "PR-SUMMARY.md",
    "PR-SUMMARY-*.md",
]

# Entries added by older versions without markers — used for backward-compat cleanup
_GITIGNORE_LEGACY_ENTRIES: Final = [
    "# ghaiw managed files",
    ".issue-context.md",
]

MANIFEST_FILENAME = ".ghaiw-managed"


def get_ghaiw_root() -> Path:
    """Get the ghaiw package root (for self-init detection)."""
    return Path(__file__).parent.parent.parent.parent


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def init(
    project_root: Path | None = None,
    ai_tool: str | None = None,
    non_interactive: bool = False,
) -> bool:
    """Initialize ghaiw in a project.

    Steps:
    1. Validate git repo
    2. Detect/select AI tool
    3. Collect project settings
    4. Generate .ghaiw.yml config
    5. Install skill files
    6. Update .gitignore
    7. Write AGENTS.md pointer
    8. Write .ghaiw-managed manifest

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

    from ghaiw import __version__

    console.rule("ghaiw init")
    console.info(f"ghaiw {__version__}")

    # Compute installed tools once — doesn't depend on any wizard step
    installed_tools = [str(t) for t in AbstractAITool.detect_installed()]

    # Interactive wizard
    selected_tool = _select_ai_tool(ai_tool, non_interactive)
    project_settings = _prompt_project_settings(root, non_interactive)
    work_setup = _prompt_work_setup(selected_tool, installed_tools, non_interactive)
    command_overrides = _prompt_command_overrides(
        installed_tools, non_interactive, default_model=work_setup["default_model"]
    )

    # Generate config
    config_path = root / ".ghaiw.yml"
    if config_path.exists():
        console.info("Config .ghaiw.yml already exists — patching missing values")
        _patch_config(config_path, selected_tool, work_setup["model_mapping"])
    else:
        _write_config(
            config_path,
            selected_tool,
            work_setup["model_mapping"],
            project_settings=project_settings,
            work_tool=work_setup["tool"],
            default_model=work_setup["default_model"],
            command_overrides=command_overrides,
        )
        console.success(f"Created {config_path.name}")

    # Configure Gemini experimental settings if needed
    if "gemini" in installed_tools:
        _maybe_configure_gemini_experimental(non_interactive)

    # 5. Install skills
    is_self = root.resolve() == get_ghaiw_root().resolve()
    installed = installer.install_skills(root, is_self_init=is_self)
    console.info(f"Installed {len(installed)} skill entries")

    # 6. Update .gitignore
    _ensure_gitignore(root)

    # 7. AGENTS.md pointer
    pointer_path = pointer.ensure_pointer(root)
    if pointer_path:
        console.success(f"Workflow pointer in {Path(pointer_path).name}")

    # Optional: configure Claude Code allowlist (project-level .claude/settings.json)
    _maybe_configure_allowlist(root, non_interactive)

    # Optional: configure Claude Code statusline (global ~/.claude/ setting)
    _maybe_configure_statusline(non_interactive)

    # 8. Write manifest
    _write_manifest(root, installed)
    console.success("Wrote .ghaiw-managed manifest")

    # Validate the config we just wrote
    from ghaiw.services.check_service import validate_config

    check_result = validate_config(root)
    if check_result.is_valid:
        console.success("Config validation passed")
    else:
        console.warn("Config validation issues:")
        for err in check_result.errors:
            console.detail(err)

    console.panel(
        "  Project initialized. Run [bold]ghaiw plan-task[/] to get started.",
        title="ghaiw initialized",
    )
    return True


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def update(
    project_root: Path | None = None,
    skip_self_upgrade: bool = False,
) -> bool:
    """Update managed files to the latest ghaiw version.

    Steps:
    1.  Validate repo + config existence
    2.  Self-upgrade if source version differs
    3.  Read old version from manifest
    4.  Show version transition
    5.  Run config migration pipeline
    6.  Reload config + backfill probed models
    7.  Refresh skill files (force overwrite)
    8.  Configure Claude Code allowlist
    9.  Configure Gemini experimental (if applicable)
    10. Refresh .gitignore + AGENTS.md pointer
    11. Rebuild manifest with version

    Never overwrites .ghaiw.yml user values — only patches missing keys
    and refreshes skill files.
    """
    from ghaiw import __version__
    from ghaiw.config.claude_allowlist import configure_allowlist
    from ghaiw.config.migrations import run_all_migrations

    cwd = project_root or Path.cwd()

    # Step 1: Validate repo + config
    try:
        root = repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return False

    config_path = find_config_file(root)
    if config_path is None:
        console.error_with_fix("No .ghaiw.yml found", "Initialize your project first", "ghaiw init")
        return False

    # Step 2: Self-upgrade if source version differs
    if not skip_self_upgrade and _maybe_self_upgrade():
        # re_exec() was called — this line is never reached
        pass  # pragma: no cover

    console.rule("ghaiw update")

    # Step 3: Read old version from manifest
    old_version = _read_manifest_version(root)

    # Step 4: Show version transition
    if old_version and old_version != __version__:
        console.info(f"Updating from ghaiw {old_version} → {__version__}")
    else:
        console.info(f"ghaiw {__version__}")

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

    # Step 7: Refresh skill files (force overwrite)
    is_self = root.resolve() == get_ghaiw_root().resolve()
    installed = installer.install_skills(root, is_self_init=is_self, force=True)
    console.info(f"Updated {len(installed)} skill entries")

    # Step 8: Configure Claude Code allowlist
    configure_allowlist(root)

    # Step 9: Configure Gemini experimental (if applicable)
    installed_tools = [str(t) for t in AbstractAITool.detect_installed()]
    if "gemini" in installed_tools:
        _maybe_configure_gemini_experimental(False)

    # Step 10: Refresh .gitignore + AGENTS.md pointer
    _ensure_gitignore(root)
    pointer.ensure_pointer(root)

    # Step 11: Rebuild manifest with version
    _write_manifest(root, installed)

    console.panel("  All managed files are up to date.", title="ghaiw updated")
    return True


def _maybe_self_upgrade() -> bool:
    """Check if a source-version upgrade is available and apply it.

    If upgrade is applied, calls re_exec() which replaces this process.
    Returns True if an upgrade was detected (but re_exec already ran),
    False if no upgrade was needed.
    """
    from ghaiw.utils.install import (
        get_installed_version,
        get_source_repo_path,
        get_source_version,
        re_exec,
        self_upgrade,
    )

    source = get_source_repo_path()
    if not source:
        return False  # Editable install or no source marker

    source_ver = get_source_version(source)
    if not source_ver:
        return False

    installed_ver = get_installed_version()
    if source_ver == installed_ver:
        return False  # Already up to date

    console.info(f"Source version {source_ver} differs from installed {installed_ver}")
    console.step("Self-upgrading from source...")

    if self_upgrade(source):
        console.success(f"Upgraded to {source_ver} — restarting...")
        re_exec()  # Does not return
        return True  # pragma: no cover

    console.warn("Self-upgrade failed — continuing with current version")
    return False


def _read_manifest_version(root: Path) -> str | None:
    """Read the ghaiw version from the .ghaiw-managed manifest.

    Looks for a line like: # Managed by ghaiw 0.1.0
    or: # Managed by ghaiw 0.1.0
    """
    import re

    manifest = root / MANIFEST_FILENAME
    if not manifest.is_file():
        return None

    text = manifest.read_text(encoding="utf-8")
    match = re.search(r"# Managed by (?:ghaiw(?:py)?)\s+(\S+)", text)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Deinit
# ---------------------------------------------------------------------------


def deinit(project_root: Path | None = None, force: bool = False) -> bool:
    """Remove ghaiw from a project.

    Removes: skills, config, manifest, .gitignore entries, AGENTS.md pointer.
    """
    cwd = project_root or Path.cwd()

    try:
        root = repo.get_repo_root(cwd)
    except GitError:
        console.error_with_fix("Not inside a git repository", "Navigate to your project directory")
        return False

    if not force:
        from ghaiw.ui import prompts

        if not prompts.confirm("Remove ghaiw from this project?"):
            console.info("Aborted.")
            return False

    console.rule("ghaiw deinit")

    # Remove skills
    removed = installer.remove_skills(root)
    console.info(f"Removed {len(removed)} skill entries")

    # Remove AGENTS.md pointer
    for name in ("AGENTS.md", "CLAUDE.md"):
        target = root / name
        if target.is_symlink() and name == "CLAUDE.md":
            # Only remove if ghaiw created it (points to AGENTS.md)
            link_target = target.resolve()
            if link_target == (root / "AGENTS.md").resolve():
                target.unlink()
                console.info("Removed CLAUDE.md symlink")
        elif target.is_file() and pointer.remove_pointer(target):
            console.info(f"Removed workflow pointer from {name}")

    # Remove config
    config_path = root / ".ghaiw.yml"
    if config_path.is_file():
        config_path.unlink()
        console.info("Removed .ghaiw.yml")

    # Remove manifest
    manifest = root / MANIFEST_FILENAME
    if manifest.is_file():
        manifest.unlink()
        console.info("Removed .ghaiw-managed")

    # Clean .gitignore
    _clean_gitignore(root)

    # Remove .ghaiw directory (internal state: SQLite DB, audit log)
    ghaiw_dir = root / ".ghaiw"
    if ghaiw_dir.is_dir():
        shutil.rmtree(ghaiw_dir)

    console.panel("  All ghaiw artifacts removed.", title="ghaiw removed")
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _configure_gemini_experimental() -> None:
    """Write Gemini experimental settings for plan mode support.

    Writes {"experimental":{"plan":true}} to ~/.gemini/settings.json,
    merging non-destructively with existing content.

    Behavioral ref: lib/init.sh:_init_configure_gemini_experimental()
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


def _maybe_configure_gemini_experimental(non_interactive: bool) -> None:
    """Prompt user to enable Gemini experimental plan mode, skipping if already configured."""
    import contextlib
    import json

    from ghaiw.ui import prompts

    settings_path = Path.home() / ".gemini" / "settings.json"

    existing: dict[str, Any] = {}
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw

    experimental = existing.get("experimental", {})
    if isinstance(experimental, dict) and experimental.get("plan") is True:
        console.detail("Gemini experimental.plan already enabled")
        return  # Already configured

    if non_interactive:
        return

    if prompts.confirm(
        "Enable Gemini experimental.plan mode (required for planning)?",
        default=True,
    ):
        _configure_gemini_experimental()
        console.success("Enabled experimental.plan in ~/.gemini/settings.json")


def _maybe_configure_allowlist(root: Path, non_interactive: bool) -> None:
    """Prompt user to configure the Claude Code allowlist, skipping if already set."""
    from ghaiw.config.claude_allowlist import configure_allowlist, is_allowlist_configured
    from ghaiw.ui import prompts

    if is_allowlist_configured(root):
        console.detail("Claude Code allowlist already configured")
        return

    if non_interactive:
        return

    if prompts.confirm(
        "Auto-approve ghaiw commands in Claude Code? (skips Bash approval in work sessions)",
        default=True,
    ):
        configure_allowlist(root)
        console.success("Added Bash(ghaiw *) to .claude/settings.json allowlist")


def _configure_statusline() -> None:
    """Install Claude Code statusline script and register it in ~/.claude/settings.json.

    Copies templates/statusline-command.sh to ~/.claude/statusline-command.sh and
    writes ``{"statusLine": {"type": "command", ...}}`` into ~/.claude/settings.json,
    merging non-destructively with existing content.
    """
    import contextlib
    import json

    from ghaiw.skills.installer import get_templates_dir

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


def _maybe_configure_statusline(non_interactive: bool) -> None:
    """Prompt user to install Claude Code statusline, skipping if already configured."""
    import contextlib
    import json

    from ghaiw.ui import prompts

    settings_path = Path.home() / ".claude" / "settings.json"
    existing: dict[str, Any] = {}
    if settings_path.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw

    if "statusLine" in existing:
        console.detail("Claude Code statusline already configured")
        return  # Already configured — skip silently (idempotent)

    if non_interactive:
        return

    if prompts.confirm(
        "Install Claude Code statusline script (token counts + context bar)?",
        default=True,
    ):
        _configure_statusline()
        console.success("Installed ~/.claude/statusline-command.sh")


def _select_ai_tool(
    requested: str | None,
    non_interactive: bool,
) -> str | None:
    """Select an AI tool — from argument, detection, or interactive prompt."""
    from ghaiw.ui import prompts

    if requested:
        try:
            AIToolID(requested)
            return requested
        except ValueError:
            console.warn(f"Unknown AI tool: {requested}")
            return requested

    # Detect installed tools
    installed = AbstractAITool.detect_installed()

    if not installed:
        console.warn("No AI CLI tools detected. You can set 'ai_tool' in .ghaiw.yml later.")
        return None

    if len(installed) == 1:
        tool = installed[0]
        console.success(f"Detected AI tool: {tool}")
        return tool

    if non_interactive:
        tool = installed[0]
        console.info(f"Using AI tool: {tool}")
        return tool

    # Interactive: show menu with Skip option
    skip_label = "Skip (configure later)"
    items = [str(t) for t in installed] + [skip_label]
    idx = prompts.select("Select default AI tool", items)

    if items[idx] == skip_label:
        return None

    selected = installed[idx]
    console.success(f"Selected AI tool: {selected}")
    return str(selected)


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
    from ghaiw.ui import prompts

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
    from ghaiw.ui import prompts

    if non_interactive:
        return mapping

    console.rule("Models")

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
    from ghaiw.data import get_models_for_tool

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
    from ghaiw.ui import prompts

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
    """Prompt for all implementation-work configuration as a single cohesive unit.

    Covers: work tool selection, complexity model mapping for that tool, and
    the default fallback model.  The default model is prompted AFTER the
    complexity mapping so the pre-selected value reflects the user's just-chosen
    ``mapping.complex`` tier rather than a hardcoded default.

    Returns a dict with keys:
        ``tool``          - work-specific tool override, or None (use default_tool)
        ``model_mapping`` - ComplexityModelMapping for the effective tool
        ``default_model`` - fallback model ID, or None if skipped
    """
    if non_interactive:
        mapping = _resolve_models(default_tool)
        return {"tool": None, "model_mapping": mapping, "default_model": None}

    from ghaiw.ui import prompts

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

    default_model = _prompt_default_model(current_effective, mapping, non_interactive=False)

    return {"tool": work_tool, "model_mapping": mapping, "default_model": default_model}


def _prompt_command_overrides(
    installed_tools: list[str],
    non_interactive: bool,
    default_model: str | None = None,
) -> dict[str, dict[str, str]]:
    """Prompt for per-command AI tool and model overrides for plan and deps.

    Work configuration is handled separately by ``_prompt_work_setup()``.

    Returns a dict like:
        {"plan": {"tool": "claude", "model": "claude-sonnet-4-6"}, "deps": {}}

    Empty dicts for commands with no overrides.
    """
    from ghaiw.ui import prompts

    if non_interactive:
        return {"plan": {}, "deps": {}}

    console.rule("Command overrides")

    # Build selectable list: installed tools + "Skip (use default)"
    skip_label = "Skip (use default)"
    tool_options = (installed_tools if installed_tools else ["claude", "copilot", "gemini"]) + [
        skip_label
    ]

    cmd_pairs = [("plan", "Planning tasks"), ("deps", "Dependency analysis")]
    result: dict[str, dict[str, str]] = {"plan": {}, "deps": {}}
    tool_for_cmd: list[str | None] = [None] * len(cmd_pairs)
    for cmd_idx, (cmd_name, label) in enumerate(cmd_pairs):
        idx = prompts.select(
            f"AI tool for {label.lower()}", tool_options, default=len(tool_options) - 1
        )
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
                f"  Model for {label.lower()}", model_options, default=default_idx
            )

            chosen = model_options[chosen_idx]
            if chosen == custom_label:
                chosen = prompts.input_prompt(
                    f"  Model for {label.lower()} (model ID)", model_default
                )
            if chosen and chosen != skip_model_label:
                result[cmd_name]["model"] = chosen

    return result


def _write_config(
    config_path: Path,
    ai_tool: str | None,
    model_mapping: ComplexityModelMapping,
    project_settings: dict[str, str] | None = None,
    work_tool: str | None = None,
    default_model: str | None = None,
    command_overrides: dict[str, dict[str, str]] | None = None,
) -> None:
    """Write a fresh .ghaiw.yml config file."""
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

    # Write work tool override (only when different from default_tool)
    if work_tool and work_tool != ai_tool:
        ai_section["work"] = {"tool": work_tool}

    # Write plan/deps per-command overrides
    if command_overrides:
        for cmd_name in ("plan", "deps"):
            overrides = command_overrides.get(cmd_name, {})
            if overrides:
                cmd_section: dict[str, str] = {}
                if overrides.get("tool"):
                    cmd_section["tool"] = overrides["tool"]
                if overrides.get("model"):
                    cmd_section["model"] = overrides["model"]
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

    config_path.write_text(
        yaml.dump(config_dict, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _patch_config(
    config_path: Path,
    ai_tool: str | None,
    model_mapping: ComplexityModelMapping,
) -> None:
    """Patch missing values into an existing config without overwriting."""
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return

    if not isinstance(raw, dict):
        raw = {}

    changed = False

    # Ensure version
    if "version" not in raw:
        raw["version"] = 2
        changed = True

    # Patch AI tool if missing
    ai = raw.get("ai", {}) or {}
    if ai_tool and not ai.get("default_tool"):
        ai["default_tool"] = str(ai_tool)
        raw["ai"] = ai
        changed = True

    # Patch models if missing
    tool_key = str(ai_tool) if ai_tool else None
    if tool_key and model_mapping.easy:
        models = raw.get("models", {}) or {}
        tool_models = models.get(tool_key, {}) or {}

        for key in ("easy", "medium", "complex", "very_complex"):
            value = getattr(model_mapping, key, None)
            if value and not tool_models.get(key):
                tool_models[key] = value
                changed = True

        if tool_models:
            models[tool_key] = tool_models
            raw["models"] = models

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
    """Add or refresh the ghaiw-managed block in .gitignore.

    Uses start/end markers so the block can be reliably located on every
    subsequent init or update.  Existing user content is always preserved.

    Migration: if the file already has old-style entries (no markers), they
    are removed and replaced with the marker-wrapped block.
    """
    from ghaiw.utils.markdown import extract_marker_block, has_marker_block, remove_marker_block

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
    """Remove the ghaiw-managed block from .gitignore.

    Primary: marker-based removal (reliable, preserves user content).
    Fallback: line-by-line removal of known entries (backward compat for
    projects initialized before markers were introduced).
    """
    from ghaiw.utils.markdown import has_marker_block, remove_marker_block

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
    """Write the .ghaiw-managed manifest listing all managed files."""
    from ghaiw import __version__

    manifest = project_root / MANIFEST_FILENAME
    lines = [".ghaiw.yml", *installed_files]
    lines.append(f"# Managed by ghaiw {__version__}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
