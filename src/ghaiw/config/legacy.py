"""Legacy artifact cleanup — remove pre-v1/v2/v3 files and directories.

Cleans up files from older ghaiw versions that are no longer used in the
current skill/config structure. Runs as part of `ghaiwpy update`.

Behavioral reference: lib/init.sh ghaiw_update() cleanup steps
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Skill directories that were removed or renamed across versions
_LEGACY_SKILL_DIRS = [
    "plan-issues",
    "prepare-merge",
    "plan",
]

# Skill host directories that may contain legacy entries
_SKILL_HOST_DIRS = [
    ".claude/skills",
    ".github/skills",
    ".agents/skills",
    ".gemini/skills",
]

# Other legacy files
_LEGACY_FILES = [
    ".commit-msg",
]


def cleanup_legacy_artifacts(project_root: Path) -> int:
    """Remove legacy ghaiw artifacts from a project.

    Returns the number of items removed.
    """
    removed = 0

    # Remove legacy skill directories
    for host_dir in _SKILL_HOST_DIRS:
        host_path = project_root / host_dir
        if not host_path.is_dir():
            continue

        for legacy_name in _LEGACY_SKILL_DIRS:
            legacy_path = host_path / legacy_name
            if legacy_path.exists():
                if legacy_path.is_symlink():
                    legacy_path.unlink()
                elif legacy_path.is_dir():
                    shutil.rmtree(legacy_path)
                else:
                    legacy_path.unlink()
                logger.info("legacy.removed", path=str(legacy_path))
                removed += 1

    # Remove legacy .githooks/ directory if it has ghaiw markers
    githooks_dir = project_root / ".githooks"
    if githooks_dir.is_dir():
        _has_ghaiw_marker = False
        for hook_file in githooks_dir.iterdir():
            if hook_file.is_file():
                try:
                    content = hook_file.read_text(encoding="utf-8", errors="ignore")
                    if "ghaiw" in content.lower():
                        _has_ghaiw_marker = True
                        break
                except OSError:
                    continue

        if _has_ghaiw_marker:
            shutil.rmtree(githooks_dir)
            logger.info("legacy.removed", path=str(githooks_dir))
            removed += 1

    # Remove legacy individual files
    for filename in _LEGACY_FILES:
        filepath = project_root / filename
        if filepath.is_file():
            filepath.unlink()
            logger.info("legacy.removed", path=str(filepath))
            removed += 1

    # Remove .github/skills/ if it's a real directory (not a symlink)
    # Legacy versions used real directories; current uses symlinks
    github_skills = project_root / ".github" / "skills"
    if github_skills.is_dir() and not github_skills.is_symlink():
        # Only remove if it contains old skill dirs, not if it has current skills
        has_only_legacy = True
        for child in github_skills.iterdir():
            if child.name not in _LEGACY_SKILL_DIRS:
                has_only_legacy = False
                break

        if has_only_legacy and any(github_skills.iterdir()):
            shutil.rmtree(github_skills)
            logger.info("legacy.removed_github_skills", path=str(github_skills))
            removed += 1

    if removed:
        logger.info("legacy.cleanup_complete", removed=removed)

    return removed
