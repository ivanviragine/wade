"""Init service manifest helpers — the ``.wade-managed`` file and init summary.

Reads and writes the ``.wade-managed`` manifest (version breadcrumb) and renders
the pre-write configuration summary. Leaf module — imports nothing from siblings.
"""

from __future__ import annotations

from pathlib import Path

from wade.models.config import AICommandConfig, BotReviewConfig, KnowledgeConfig, ProjectSettings
from wade.models.init_setup import HooksSetup, ImplementationSetup, ProviderSetup
from wade.ui.console import console

MANIFEST_FILENAME = ".wade-managed"

__all__ = [
    "MANIFEST_FILENAME",
    "_read_manifest_version",
    "_show_init_summary",
    "_write_manifest",
]


def _show_init_summary(
    *,
    provider_setup: ProviderSetup,
    project_settings: ProjectSettings,
    selected_tool: str | None,
    default_model: str | None,
    default_effort: str | None,
    default_yolo: bool | None,
    implementation_setup: ImplementationSetup,
    command_overrides: dict[str, AICommandConfig],
    hooks_setup: HooksSetup,
    knowledge_setup: KnowledgeConfig,
    bot_review_setup: BotReviewConfig | None = None,
) -> None:
    """Render a summary of all wizard selections before the write phase."""
    console.rule("Configuration summary")

    # Provider + Project
    console.kv("Provider", provider_setup.name.value)
    console.kv("Main branch", project_settings.main_branch or "main")
    console.kv("Merge strategy", project_settings.merge_strategy.value)
    console.kv("Branch prefix", project_settings.branch_prefix)
    console.kv("Worktrees dir", project_settings.worktrees_dir)

    # AI defaults
    console.kv("AI tool", selected_tool or "(not set)")
    if default_model:
        console.kv("Default model", default_model)
    if default_effort:
        console.kv("Default effort", default_effort)
    if default_yolo is not None:
        console.kv("Default YOLO", str(default_yolo).lower())

    # Implementation tiers
    mapping = implementation_setup.model_mapping
    impl_tool = implementation_setup.tool
    if impl_tool:
        console.kv("Implement tool", impl_tool)
    if mapping:
        for tier, label in (
            ("easy", "Easy"),
            ("medium", "Medium"),
            ("complex", "Complex"),
            ("very_complex", "Very complex"),
        ):
            model = getattr(mapping, tier, None)
            effort = getattr(mapping, f"{tier}_effort", None)
            if model:
                val = f"{model}" + (f" [{effort}]" if effort else "")
                console.kv(f"  {label}", val)

    # Per-command overrides (only non-empty)
    for cmd_name, overrides in command_overrides.items():
        parts = []
        if overrides.tool:
            parts.append(f"tool={overrides.tool}")
        if overrides.model:
            parts.append(f"model={overrides.model}")
        if overrides.effort:
            parts.append(f"effort={overrides.effort}")
        if overrides.yolo:
            parts.append("yolo")
        if overrides.enabled is False:
            parts.append("disabled")
        elif overrides.mode:
            parts.append(f"mode={overrides.mode}")
        if parts:
            console.kv(f"  {cmd_name}", ", ".join(parts))

    # Hooks
    if hooks_setup.post_worktree_create:
        console.kv("Post-worktree script", hooks_setup.post_worktree_create)
    copy_files = hooks_setup.copy_to_worktree
    if copy_files:
        console.kv("Copy to worktrees", ", ".join(copy_files))

    # Knowledge
    if knowledge_setup.enabled:
        console.kv("Knowledge file", knowledge_setup.path)

    # Bot review triggers (#431, #464) — report the resolved mode, not two flags.
    if bot_review_setup is not None:
        if bot_review_setup.auto_trigger:
            mode = "automatic"
        elif bot_review_setup.offer_on_done:
            mode = "offer after done"
        else:
            mode = "manual (wade review trigger)"
        console.kv("Bot review triggers", mode)


def _read_manifest_version(root: Path) -> str | None:
    """Read the WADE version from the .wade-managed manifest.

    Looks for a line like: # Managed by wade 0.1.0
    Checks ``.wade/.wade-managed`` first, then falls back to the legacy
    root-level location for backward compatibility.
    """
    import re

    manifest = root / ".wade" / MANIFEST_FILENAME
    if not manifest.is_file():
        # Fallback to legacy root-level location
        manifest = root / MANIFEST_FILENAME
    if not manifest.is_file():
        return None

    text = manifest.read_text(encoding="utf-8")
    match = re.search(r"# Managed by wade\s+(\S+)", text)
    return match.group(1) if match else None


def _write_manifest(project_root: Path, installed_files: list[str]) -> None:
    """Write the .wade-managed manifest under ``.wade/``.

    The manifest lives inside ``.wade/`` so it is auto-ignored by
    ``.wade/.gitignore`` (which contains ``*``) and never appears as
    an untracked file on main.
    """
    from wade import __version__

    wade_dir = project_root / ".wade"
    wade_dir.mkdir(exist_ok=True)
    manifest = wade_dir / MANIFEST_FILENAME
    lines = [".wade.yml", *installed_files]
    lines.append(f"# Managed by wade {__version__}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Migration: remove legacy root-level manifest if it exists
    legacy_manifest = project_root / MANIFEST_FILENAME
    if legacy_manifest.is_file():
        legacy_manifest.unlink()
