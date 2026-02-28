#!/usr/bin/env python3
"""ghaiw version bump — deterministic semver version management.

Usage: python scripts/auto_version.py <patch|minor|major> [--dry-run]

Updates __version__ in src/ghaiw/__init__.py and version in pyproject.toml
to the next semver value. Optionally commits and tags the release.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
INIT_FILE = ROOT_DIR / "src" / "ghaiw" / "__init__.py"
PYPROJECT_FILE = ROOT_DIR / "pyproject.toml"


def read_version() -> str:
    """Read the current version from __init__.py."""
    content = INIT_FILE.read_text()
    match = re.search(r'__version__\s*=\s*"(\d+\.\d+\.\d+)"', content)
    if not match:
        print(f"Could not read __version__ from {INIT_FILE}", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def bump_version(current: str, bump_type: str) -> str:
    """Compute the next version based on bump type."""
    major, minor, patch = (int(x) for x in current.split("."))
    if bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump_type == "minor":
        minor += 1
        patch = 0
    elif bump_type == "patch":
        patch += 1
    return f"{major}.{minor}.{patch}"


def update_init_file(current: str, new: str) -> None:
    """Update __version__ in __init__.py."""
    content = INIT_FILE.read_text()
    updated = content.replace(f'__version__ = "{current}"', f'__version__ = "{new}"')
    INIT_FILE.write_text(updated)


def update_pyproject(current: str, new: str) -> None:
    """Update version in pyproject.toml."""
    content = PYPROJECT_FILE.read_text()
    updated = content.replace(f'version = "{current}"', f'version = "{new}"', 1)
    PYPROJECT_FILE.write_text(updated)


def is_git_repo() -> bool:
    """Check if we're inside a git repo."""
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def main() -> None:
    # Parse args
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    push = "--push" in args
    args = [a for a in args if a not in ("--dry-run", "--push")]

    if not args or args[0] not in ("patch", "minor", "major"):
        print("Usage: python scripts/auto_version.py <patch|minor|major> [--dry-run] [--push]")
        print()
        print("Bump types (semver):")
        print("  patch  — bug fixes, docs, minor tweaks          (0.1.0 → 0.1.1)")
        print("  minor  — new features, non-breaking changes     (0.1.0 → 0.2.0)")
        print("  major  — breaking changes, major rework         (0.1.0 → 1.0.0)")
        print()
        print("Options:")
        print("  --dry-run  Preview the version bump without making any changes")
        print("  --push     Push commit and tag to origin after bumping")
        sys.exit(1)

    bump_type = args[0]
    current = read_version()
    new = bump_version(current, bump_type)

    print(f"Version: {current} → {new} ({bump_type})")

    if dry_run:
        print("(dry run — no changes made)")
        return

    # Update version in source files
    update_init_file(current, new)
    print(f"Updated __version__ in {INIT_FILE.relative_to(ROOT_DIR)}")

    update_pyproject(current, new)
    print(f"Updated version in {PYPROJECT_FILE.relative_to(ROOT_DIR)}")

    # Commit and tag if in a git repo
    if is_git_repo():
        subprocess.run(
            ["git", "add", str(INIT_FILE), str(PYPROJECT_FILE)],
            cwd=ROOT_DIR,
            check=True,
        )

        # Generate changelog if the script exists
        changelog_script = ROOT_DIR / "scripts" / "changelog.py"
        if changelog_script.exists():
            subprocess.run(
                [sys.executable, str(changelog_script), "--tag", f"v{new}"],
                cwd=ROOT_DIR,
                check=True,
            )
            changelog_file = ROOT_DIR / "CHANGELOG.md"
            if changelog_file.exists():
                subprocess.run(
                    ["git", "add", str(changelog_file)],
                    cwd=ROOT_DIR,
                    check=True,
                )

        subprocess.run(
            ["git", "commit", "-m", f"chore: bump version to v{new}"],
            cwd=ROOT_DIR,
            check=True,
        )
        subprocess.run(
            ["git", "tag", "-a", f"v{new}", "-m", f"Release v{new}"],
            cwd=ROOT_DIR,
            check=True,
        )
        print(f"Committed and tagged v{new}")

        if push:
            subprocess.run(["git", "push"], cwd=ROOT_DIR, check=True)
            subprocess.run(["git", "push", "--tags"], cwd=ROOT_DIR, check=True)
            print()
            print(f"Pushed v{new} — release.yml will create a draft GitHub Release.")
            print("Go to GitHub → Releases, review the draft, and click Publish to trigger PyPI.")
        else:
            print()
            print("To publish:")
            print("  git push && git push --tags")
    else:
        print("Not in a git repo — skipped commit/tag")


if __name__ == "__main__":
    main()
