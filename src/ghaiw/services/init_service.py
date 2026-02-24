"""Init service — project initialization, update, and removal.

Orchestrates: AI tool detection, config generation, skill installation,
AGENTS.md pointer, .gitignore entries, and .ghaiw-managed manifest.

Behavioral reference: lib/init.sh (ghaiw_init, ghaiw_update, ghaiw_deinit)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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

# .gitignore entries managed by ghaiw
GITIGNORE_ENTRIES = [
    "# ghaiw managed files",
    ".ghaiw-managed",
    ".issue-context.md",
    "PR_SUMMARY.md",
    "PR-SUMMARY-*.md",
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
        console.error("Not inside a git repository")
        return False

    try:
        root = repo.get_repo_root(cwd)
    except GitError:
        console.error("Could not determine repository root")
        return False

    console.header("ghaiwpy init")

    # 2. Detect/select AI tool
    selected_tool = _select_ai_tool(ai_tool, non_interactive)

    # 3. Resolve models (probing or defaults)
    model_mapping, models_probed = _resolve_models(selected_tool)

    # 4. Collect project settings
    project_settings = _prompt_project_settings(root, non_interactive)

    # 5. Let user review/edit model mapping
    model_mapping = _prompt_model_mapping(
        selected_tool, model_mapping, models_probed, non_interactive
    )

    # 6. Per-command AI tool overrides
    installed_tools = [str(t) for t in AbstractAITool.detect_installed()]
    command_overrides = _prompt_command_overrides(installed_tools, non_interactive)

    # 7. Generate config
    config_path = root / ".ghaiw.yml"
    if config_path.exists():
        console.info("Config .ghaiw.yml already exists — patching missing values")
        _patch_config(config_path, selected_tool, model_mapping)
    else:
        _write_config(
            config_path,
            selected_tool,
            model_mapping,
            project_settings=project_settings,
            command_overrides=command_overrides,
        )
        console.success(f"Created {config_path.name}")

    # Configure Gemini experimental settings if needed
    if selected_tool and (selected_tool == AIToolID.GEMINI or selected_tool == "gemini"):
        _configure_gemini_experimental()

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

    console.banner("ghaiwpy initialized")
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
    8.  Clean legacy artifacts
    9.  Configure Claude Code allowlist
    10. Configure Gemini experimental (if applicable)
    11. Refresh .gitignore + AGENTS.md pointer
    12. Rebuild manifest with version

    Never overwrites .ghaiw.yml user values — only patches missing keys
    and refreshes skill files.
    """
    from ghaiw import __version__
    from ghaiw.config.claude_allowlist import configure_allowlist
    from ghaiw.config.legacy import cleanup_legacy_artifacts
    from ghaiw.config.migrations import run_all_migrations

    cwd = project_root or Path.cwd()

    # Step 1: Validate repo + config
    try:
        root = repo.get_repo_root(cwd)
    except GitError:
        console.error("Not inside a git repository")
        return False

    config_path = find_config_file(root)
    if config_path is None:
        console.error("No .ghaiw.yml found — run 'ghaiwpy init' first")
        return False

    # Step 2: Self-upgrade if source version differs
    if not skip_self_upgrade and _maybe_self_upgrade():
        # re_exec() was called — this line is never reached
        pass  # pragma: no cover

    console.header("ghaiwpy update")

    # Step 3: Read old version from manifest
    old_version = _read_manifest_version(root)

    # Step 4: Show version transition
    if old_version and old_version != __version__:
        console.info(f"Updating from ghaiwpy {old_version} → {__version__}")
    else:
        console.info(f"ghaiwpy {__version__}")

    # Step 5: Run config migration pipeline
    if run_all_migrations(config_path):
        console.success("Config migrations applied")
    else:
        console.detail("Config already up to date")

    # Step 6: Reload config + backfill probed models
    config = load_config(root)
    ai_tool = config.get_ai_tool()
    if ai_tool:
        model_mapping, _ = _resolve_models(ai_tool)
        _patch_config(config_path, ai_tool, model_mapping)

    # Step 7: Refresh skill files (force overwrite)
    is_self = root.resolve() == get_ghaiw_root().resolve()
    installed = installer.install_skills(root, is_self_init=is_self, force=True)
    console.info(f"Updated {len(installed)} skill entries")

    # Step 8: Clean legacy artifacts
    removed = cleanup_legacy_artifacts(root)
    if removed:
        console.info(f"Removed {removed} legacy artifact(s)")

    # Step 9: Configure Claude Code allowlist
    configure_allowlist(root)

    # Step 10: Configure Gemini experimental (if applicable)
    if ai_tool and (ai_tool == AIToolID.GEMINI or ai_tool == "gemini"):
        _configure_gemini_experimental()

    # Step 11: Refresh .gitignore + AGENTS.md pointer
    _ensure_gitignore(root)
    pointer.ensure_pointer(root)

    # Step 12: Rebuild manifest with version
    _write_manifest(root, installed)

    console.banner("ghaiwpy updated")
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
    """Read the ghaiwpy version from the .ghaiw-managed manifest.

    Looks for a line like: # Managed by ghaiw 0.1.0
    or: # Managed by ghaiwpy 0.1.0
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
        console.error("Not inside a git repository")
        return False

    console.header("ghaiwpy deinit")

    # Remove skills
    removed = installer.remove_skills(root)
    console.info(f"Removed {len(removed)} skill entries")

    # Remove AGENTS.md pointer
    for name in ("AGENTS.md", "CLAUDE.md"):
        target = root / name
        if target.is_file() and pointer.remove_pointer(target):
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

    # Remove .ghaiw directory if empty
    ghaiw_dir = root / ".ghaiw"
    if ghaiw_dir.is_dir() and not any(ghaiw_dir.iterdir()):
        ghaiw_dir.rmdir()

    console.banner("ghaiwpy removed")
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


def _resolve_models(tool: str | None) -> tuple[ComplexityModelMapping, bool]:
    """Resolve model mappings for a tool via probing or defaults.

    Returns:
        Tuple of (mapping, probed) where probed is True if live model
        discovery succeeded, False if we fell back to hardcoded defaults.
    """
    if not tool:
        return ComplexityModelMapping(), False

    # Try to probe the tool for available models
    try:
        adapter = AbstractAITool.get(AIToolID(tool))
        mapping = adapter.get_recommended_mapping()
        if mapping.easy or mapping.complex:
            return _normalize_mapping(adapter, mapping), True
    except (ValueError, Exception):
        pass

    # Fallback to hardcoded defaults
    mapping = get_defaults(tool)

    # Normalize format for the tool
    try:
        adapter = AbstractAITool.get(AIToolID(tool))
        mapping = _normalize_mapping(adapter, mapping)
    except (ValueError, Exception):
        pass

    return mapping, False


def _normalize_mapping(
    adapter: AbstractAITool,
    mapping: ComplexityModelMapping,
) -> ComplexityModelMapping:
    """Normalize model IDs in a mapping to the tool's expected format."""
    return ComplexityModelMapping(
        easy=adapter.normalize_model_format(mapping.easy) if mapping.easy else None,
        medium=adapter.normalize_model_format(mapping.medium) if mapping.medium else None,
        complex=adapter.normalize_model_format(mapping.complex) if mapping.complex else None,
        very_complex=(
            adapter.normalize_model_format(mapping.very_complex) if mapping.very_complex else None
        ),
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

    console.header("Project settings (press Enter to accept defaults)")

    defaults["merge_strategy"] = prompts.input_prompt(
        "Merge strategy — PR or direct", defaults["merge_strategy"]
    )
    defaults["branch_prefix"] = prompts.input_prompt("Branch prefix", defaults["branch_prefix"])
    defaults["issue_label"] = prompts.input_prompt("Issue label", defaults["issue_label"])
    defaults["worktrees_dir"] = prompts.input_prompt(
        "Worktrees directory", defaults["worktrees_dir"]
    )

    return defaults


def _prompt_model_mapping(
    tool: str | None,
    mapping: ComplexityModelMapping,
    models_probed: bool,
    non_interactive: bool,
) -> ComplexityModelMapping:
    """Let the user review/edit the complexity-to-model mapping.

    If non_interactive, returns the mapping unchanged.
    If probing failed, shows a warning before prompting.
    Falls back to Claude defaults when the tool has no model suggestions.
    """
    from ghaiw.ui import prompts

    if non_interactive:
        return mapping

    console.header("Model complexity mapping")

    # Ensure we always have defaults to show — fall back to Claude defaults
    if not (mapping.easy or mapping.complex):
        fallback = get_defaults(AIToolID.CLAUDE)
        mapping = ComplexityModelMapping(
            easy=mapping.easy or fallback.easy,
            medium=mapping.medium or fallback.medium,
            complex=mapping.complex or fallback.complex,
            very_complex=mapping.very_complex or fallback.very_complex,
        )

    if tool and not models_probed:
        console.warn(
            f"Could not auto-detect models for {tool} — "
            "showing Claude defaults. Press Enter to accept or type a model name."
        )

    easy = prompts.input_prompt("Easy tasks", mapping.easy or "")
    medium = prompts.input_prompt("Medium tasks", mapping.medium or "")
    complex_ = prompts.input_prompt("Complex tasks", mapping.complex or "")
    very_complex = prompts.input_prompt("Very complex tasks", mapping.very_complex or "")

    return ComplexityModelMapping(
        easy=easy or mapping.easy,
        medium=medium or mapping.medium,
        complex=complex_ or mapping.complex,
        very_complex=very_complex or mapping.very_complex,
    )


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


def _prompt_command_overrides(
    installed_tools: list[str],
    non_interactive: bool,
) -> dict[str, dict[str, str]]:
    """Prompt for per-command AI tool and model overrides.

    Returns a dict like:
        {"plan": {"tool": "claude", "model": "claude-sonnet-4-6"}, ...}

    Empty dicts for commands with no overrides.
    """
    from ghaiw.ui import prompts

    if non_interactive:
        return {"plan": {}, "deps": {}, "work": {}}

    console.header("Per-command AI tool overrides")

    # Build selectable list: installed tools + "Skip (use default)"
    skip_label = "Skip (use default)"
    tool_options = (installed_tools if installed_tools else ["claude", "copilot", "gemini"]) + [
        skip_label
    ]

    result: dict[str, dict[str, str]] = {}
    for cmd_name, label in [
        ("plan", "Planning tasks"),
        ("deps", "Dependency analysis"),
        ("work", "Implementation work"),
    ]:
        idx = prompts.select(f"AI tool for {label.lower()}", tool_options)
        tool = tool_options[idx]
        if tool == skip_label:
            result[cmd_name] = {}
        else:
            model_default = _suggest_model_for_tool(tool)
            model = prompts.input_prompt(f"  Model for {label.lower()}", model_default)
            result[cmd_name] = {"tool": tool}
            if model:
                result[cmd_name]["model"] = model

    return result


def _write_config(
    config_path: Path,
    ai_tool: str | None,
    model_mapping: ComplexityModelMapping,
    project_settings: dict[str, str] | None = None,
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

    # Write per-command overrides
    if command_overrides:
        for cmd_name in ("plan", "deps", "work"):
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

    if ai_tool and (model_mapping.easy or model_mapping.complex):
        config_dict["models"] = {
            str(ai_tool): {
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


def _ensure_gitignore(project_root: Path) -> None:
    """Add ghaiw entries to .gitignore if not already present."""
    gitignore = project_root / ".gitignore"

    existing = ""
    if gitignore.is_file():
        existing = gitignore.read_text(encoding="utf-8")

    additions: list[str] = []
    for entry in GITIGNORE_ENTRIES:
        if entry not in existing:
            additions.append(entry)

    if additions:
        with open(gitignore, "a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n".join(additions) + "\n")


def _clean_gitignore(project_root: Path) -> None:
    """Remove ghaiw entries from .gitignore."""
    gitignore = project_root / ".gitignore"
    if not gitignore.is_file():
        return

    lines = gitignore.read_text(encoding="utf-8").splitlines()
    new_lines = [line for line in lines if line not in GITIGNORE_ENTRIES]

    # Remove consecutive blank lines
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
