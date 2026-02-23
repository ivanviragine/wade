#!/usr/bin/env python3
"""ghaiw changelog generator — builds CHANGELOG.md from conventional commits.

Usage: python scripts/changelog.py [--stdout] [--tag vX.Y.Z]

Parses git history between tags, groups commits by conventional-commit type,
and writes a Markdown changelog.

Options:
  --stdout       Print to stdout instead of writing CHANGELOG.md
  --tag vX.Y.Z   Label unreleased commits as this version (used by auto_version.py)
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT_DIR / "CHANGELOG.md"

# Section mapping: prefix → display header (order = display order)
SECTIONS: list[tuple[str, str]] = [
    ("feat", "Features"),
    ("update", "Updates"),
    ("fix", "Bug Fixes"),
    ("docs", "Documentation"),
    ("refactor", "Refactoring"),
    ("perf", "Performance"),
    ("test", "Tests"),
    ("ci", "CI/CD"),
    ("build", "Build"),
    ("style", "Style"),
    ("revert", "Reverts"),
    ("chore", "Chores"),
]

# Pre-compiled regex patterns for each prefix
PREFIX_PATTERNS: dict[str, re.Pattern[str]] = {
    prefix: re.compile(rf"^{prefix}(\([^)]*\))?\!?:\s*(.+)$")
    for prefix, _ in SECTIONS
}


def git(*args: str) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=ROOT_DIR,
    )
    return result.stdout.strip()


def get_tags() -> list[str]:
    """Get version tags sorted newest-first."""
    raw = git("tag", "--sort=-v:refname")
    if not raw:
        return []
    return [t for t in raw.splitlines() if re.match(r"^v\d+\.\d+\.\d+$", t)]


def get_commits(commit_range: str) -> list[tuple[str, str]]:
    """Get commits in a range as (subject, hash) pairs.

    Filters out version-bump noise.
    """
    raw = git("log", commit_range, "--pretty=format:%s|%h", "--no-merges")
    if not raw:
        return []
    commits = []
    for line in raw.splitlines():
        if "|" not in line:
            continue
        subject, sha = line.rsplit("|", 1)
        if subject.startswith("chore: bump version"):
            continue
        commits.append((subject.strip(), sha.strip()))
    return commits


def format_range(commit_range: str) -> str:
    """Format commits in a range as grouped Markdown sections."""
    commits = get_commits(commit_range)
    if not commits:
        return ""

    output_parts: list[str] = []
    matched_subjects: set[str] = set()

    # Group by conventional commit type
    for prefix, header in SECTIONS:
        pattern = PREFIX_PATTERNS[prefix]
        items: list[str] = []
        for subject, sha in commits:
            match = pattern.match(subject)
            if match:
                msg = match.group(2)
                items.append(f"- {msg} ({sha})")
                matched_subjects.add(subject)

        if items:
            output_parts.append(f"\n### {header}\n")
            output_parts.extend(items)

    # Unmatched commits → "Other Changes"
    other = [
        f"- {subject} ({sha})"
        for subject, sha in commits
        if subject not in matched_subjects
    ]
    if other:
        output_parts.append("\n### Other Changes\n")
        output_parts.extend(other)

    return "\n".join(output_parts) if output_parts else ""


def generate(next_tag: str = "") -> str:
    """Generate the full changelog content."""
    lines = [
        "# Changelog",
        "",
        "All notable changes to this project will be documented in this file.",
        "",
        "The format is based on [Conventional Commits](https://conventionalcommits.org/).",
    ]

    tags = get_tags()

    if tags:
        # Unreleased: HEAD → latest tag
        unreleased_commits = get_commits(f"{tags[0]}..HEAD")
        if unreleased_commits:
            section = format_range(f"{tags[0]}..HEAD")
            if section:
                if next_tag:
                    lines.append(f"\n## [{next_tag}] — {date.today().isoformat()}")
                else:
                    lines.append("\n## [Unreleased]")
                lines.append(section)

        # Each tag → previous tag
        for i, tag in enumerate(tags):
            tag_date = git("log", "-1", "--format=%as", tag) or "unknown"
            if i + 1 < len(tags):
                commit_range = f"{tags[i + 1]}..{tag}"
            else:
                root = git("rev-list", "--max-parents=0", "HEAD").splitlines()[0]
                commit_range = f"{root}..{tag}"

            section = format_range(commit_range)
            if section:
                lines.append(f"\n## [{tag}] — {tag_date}")
                lines.append(section)
    else:
        # No tags — show everything as Unreleased
        root = git("rev-list", "--max-parents=0", "HEAD")
        if root:
            root_sha = root.splitlines()[0]
            section = format_range(f"{root_sha}..HEAD")
            if section:
                if next_tag:
                    lines.append(f"\n## [{next_tag}] — {date.today().isoformat()}")
                else:
                    lines.append("\n## [Unreleased]")
                lines.append(section)

    return "\n".join(lines) + "\n"


def main() -> None:
    stdout_only = False
    next_tag = ""

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--stdout":
            stdout_only = True
        elif args[i] == "--tag" and i + 1 < len(args):
            next_tag = args[i + 1]
            i += 1
        i += 1

    content = generate(next_tag)

    if stdout_only:
        print(content, end="")
    else:
        CHANGELOG.write_text(content)
        print(f"Generated {CHANGELOG.relative_to(ROOT_DIR)}")


if __name__ == "__main__":
    main()
