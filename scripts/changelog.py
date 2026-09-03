#!/usr/bin/env python3
"""wade changelog generator — builds CHANGELOG.md from conventional commits.

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
    prefix: re.compile(rf"^{prefix}(\([^)]*\))?\!?:\s*(.+)$") for prefix, _ in SECTIONS
}

# A conventional-commit subject marks a breaking change with `!` before the colon
# (`feat!:`, `refactor(config)!:`). Without this the `\!?` above silently swallows
# the marker and a breaking change reads exactly like an ordinary one in the
# released notes — the thing a reader most needs to see first.
BREAKING_PATTERN: re.Pattern[str] = re.compile(r"^[a-z]+(\([^)]*\))?\!:\s*(.+)$")
BREAKING_HEADER = "Breaking Changes"

# Any conventional subject, `!` or not — used to strip the type prefix off a
# footer-only breaking commit so its entry reads like every neighbouring one
# ("drop the legacy loader", not "feat: drop the legacy loader").
CONVENTIONAL_SUBJECT_PATTERN: re.Pattern[str] = re.compile(r"^[a-z]+(\([^)]*\))?\!?:\s*(.+)$")

# The start of a Conventional Commits footer: a token (`Refs:`, `Signed-off-by:`
# — spaces written as `-`) or the `Closes #1` shorthand, plus `BREAKING CHANGE`,
# the one token allowed to keep its space. Used only as a stop condition below.
_FOOTER_TOKEN = r"(?:BREAKING[ -]CHANGE|[A-Za-z][A-Za-z0-9-]*)(?::[ \t]|:$|[ \t]#)"

# The Conventional Commits `BREAKING CHANGE:` footer carries what the subject
# cannot: what broke and how to opt back in. Captured through the end of its
# paragraph so a multi-line footer survives — but stopping at the next footer
# token too, since trailers need no blank line between them. Without that stop,
# `BREAKING CHANGE: removed API\nRefs: #123` ships "removed API Refs: #123" as
# the migration note, dragging every following trailer into the release notes.
BREAKING_FOOTER_PATTERN: re.Pattern[str] = re.compile(
    rf"^BREAKING[ -]CHANGE:\s*(.+?)(?=\n\s*\n|\n{_FOOTER_TOKEN}|\Z)",
    re.MULTILINE | re.DOTALL,
)

# Field/record separators for the bulk `git log` below. ASCII unit/record
# separators rather than `|`/newline because the body is multi-line free text and
# may contain either.
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"


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


def get_commits(commit_range: str) -> list[tuple[str, str, str]]:
    """Get commits in a range as (subject, hash, body) triples.

    The body comes from this one batched call rather than a per-commit lookup:
    a `BREAKING CHANGE:` footer can appear under an ordinary subject, so every
    commit's body has to be inspected, and ``generate()`` re-runs this for every
    tag range in history — one ``git log`` per commit would be thousands of
    subprocesses per regeneration.

    Filters out version-bump and branch-scaffold noise — both are
    wade-generated commits that carry no user-facing release content.
    """
    raw = git(
        "log",
        commit_range,
        f"--pretty=format:%s{_FIELD_SEP}%h{_FIELD_SEP}%B{_RECORD_SEP}",
        "--no-merges",
    )
    if not raw:
        return []
    commits = []
    for record in raw.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(_FIELD_SEP)
        if len(parts) < 3:
            continue
        subject, sha, body = parts[0], parts[1], _FIELD_SEP.join(parts[2:])
        if subject.startswith(("chore: bump version", "chore: scaffold branch")):
            continue
        commits.append((subject.strip(), sha.strip(), body))
    return commits


def breaking_note(body: str) -> str:
    """Return the commit body's ``BREAKING CHANGE:`` footer as one line, or ``""``."""
    match = BREAKING_FOOTER_PATTERN.search(body)
    if not match:
        return ""
    return " ".join(match.group(1).split())


def format_range(commit_range: str) -> str:
    """Format commits in a range as grouped Markdown sections."""
    commits = get_commits(commit_range)
    if not commits:
        return ""

    output_parts: list[str] = []
    matched_subjects: set[str] = set()

    # Breaking changes lead, and are *also* listed under their own type below —
    # duplicated on purpose so a reader skimming "Features" still sees them.
    #
    # Conventional Commits declares a break *either* way: `feat!:` in the subject
    # or a `BREAKING CHANGE:` footer under an ordinary one. Requiring the subject
    # marker would drop every footer-only break from the section that exists to
    # surface it.
    breaking: list[str] = []
    for subject, sha, body in commits:
        match = BREAKING_PATTERN.match(subject)
        note = breaking_note(body)
        if not match and not note:
            continue
        if match:
            description = match.group(2)
        else:
            conventional = CONVENTIONAL_SUBJECT_PATTERN.match(subject)
            description = conventional.group(2) if conventional else subject
        breaking.append(f"- {description} ({sha})")
        if note:
            breaking.append(f"  {note}")
    if breaking:
        output_parts.append(f"\n### {BREAKING_HEADER}\n")
        output_parts.extend(breaking)

    # Group by conventional commit type
    for prefix, header in SECTIONS:
        pattern = PREFIX_PATTERNS[prefix]
        items: list[str] = []
        for subject, sha, _body in commits:
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
        f"- {subject} ({sha})" for subject, sha, _ in commits if subject not in matched_subjects
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
