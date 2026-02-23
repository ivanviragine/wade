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

    # 3. Collect settings (defaults for now; interactive prompts in future)
    model_mapping = _resolve_models(selected_tool)

    # 4. Generate config
    config_path = root / ".ghaiw.yml"
    if config_path.exists():
        console.info("Config .ghaiw.yml already exists — patching missing values")
        _patch_config(config_path, selected_tool, model_mapping)
    else:
        _write_config(config_path, selected_tool, model_mapping)
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


def update(project_root: Path | None = None) -> bool:
    """Update managed files to the latest ghaiw version.

    Never overwrites .ghaiw.yml user values — only patches missing keys
    and refreshes skill files.
    """
    cwd = project_root or Path.cwd()

    try:
        root = repo.get_repo_root(cwd)
    except GitError:
        console.error("Not inside a git repository")
        return False

    config_path = find_config_file(root)
    if config_path is None:
        console.error("No .ghaiw.yml found — run 'ghaiwpy init' first")
        return False

    console.header("ghaiwpy update")

    # Load current config
    config = load_config(root)

    # Patch missing model keys
    ai_tool = config.get_ai_tool()
    if ai_tool:
        model_mapping = _resolve_models(ai_tool)
        _patch_config(config_path, ai_tool, model_mapping)

    # Update skills (always overwrite)
    is_self = root.resolve() == get_ghaiw_root().resolve()
    installed = installer.install_skills(root, is_self_init=is_self, force=True)
    console.info(f"Updated {len(installed)} skill entries")

    # Configure Gemini if needed
    if ai_tool and (ai_tool == AIToolID.GEMINI or ai_tool == "gemini"):
        _configure_gemini_experimental()

    # Refresh .gitignore
    _ensure_gitignore(root)

    # Refresh pointer
    pointer.ensure_pointer(root)

    # Update manifest
    _write_manifest(root, installed)

    console.banner("ghaiwpy updated")
    return True


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
        console.warn("No AI tools detected")
        return None

    if len(installed) == 1 or non_interactive:
        tool = installed[0]
        console.info(f"Using AI tool: {tool}")
        return tool

    # Interactive: show menu
    console.info(f"Detected AI tools: {', '.join(installed)}")
    # For now, default to first detected
    # Full interactive prompt will be added when UI prompts are wired
    return installed[0]


def _resolve_models(tool: str | None) -> ComplexityModelMapping:
    """Resolve model mappings for a tool via probing or defaults."""
    if not tool:
        return ComplexityModelMapping()

    # Try to probe the tool for available models
    try:
        adapter = AbstractAITool.get(AIToolID(tool))
        mapping = adapter.get_recommended_mapping()
        if mapping.easy or mapping.complex:
            return _normalize_mapping(adapter, mapping)
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

    return mapping


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


def _write_config(
    config_path: Path,
    ai_tool: str | None,
    model_mapping: ComplexityModelMapping,
) -> None:
    """Write a fresh .ghaiw.yml config file."""
    config_dict: dict[str, Any] = {"version": 2}

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
