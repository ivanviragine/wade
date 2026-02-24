# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Conventional Commits](https://conventionalcommits.org/).

## [v0.5.0] — 2026-02-24

### Features

- wire changelog command to admin CLI (5b745e4)
- address Bash parity gaps in work sessions (77d7791)
- add get_dirty_status for detailed worktree diagnostics (492a223)

### Bug Fixes

- replace bare except handlers with structured logging (f35fc9a)
- match terminal title format to Bash convention (fe77417)

### Refactoring

- use NotImplementedError in abstract method bodies (d069ca4)

## [v0.4.0] — 2026-02-24

### Features

- add --plan flag to work done for worktree resolution by plan title (feef24e)
- add _resolve_worktree_from_plan() for --plan flag support (a908fec)

### Bug Fixes

- implement post-work lifecycle prompt after AI tool exits (ea129b7)
- use AppleScript for Ghostty macOS launch (3fa02c8)
- add gnome-terminal detection and launch support (6e50cb6)
- add iTerm2 AppleScript launch for detach mode (c0f3b12)
- accept multiple --ai flags with interactive selection (d041023)
- check worktree first for PR-SUMMARY.md before /tmp fallback (222ccbe)
- implement connected-component partitioning in DependencyGraph (2f024b5)
- correct copilot model probing regex to use non-capturing group (57c5ddf)
- parameterize remove_worktree force flag (80ccb7a)
- warn on duplicate AI tool TOOL_ID registration (5db94a8)
- set SQLite busy_timeout to 30s for parallel safety (c45aa31)
- make config migration pipeline atomic and validate AI tools (69e704a)
- add timeout and cleanup prompt to work service (335a7dc)
- log specific exceptions in get_pr_for_branch (5174d96)
- raise GitError on subprocess failure in get_conflicted_files (b8d715c)

### Tests

- add branch slugify tests (696ed73)

## [v0.3.4] — 2026-02-24

### Bug Fixes

- create CLAUDE.md symlink and correct skill/cross-tool symlink paths (65412f2)

## [v0.3.3] — 2026-02-24

### Bug Fixes

- resolve 5 mypy strict errors and add mypy to pre-commit (ce7813b)

## [v0.3.2] — 2026-02-24

### Bug Fixes

- normalize model format in build_launch_command for all tools (7c58685)

### Tests

- fix tautological assertions and add command assembly tests (a2733d0)

## [v0.3.1] — 2026-02-23

### Bug Fixes

- add --add-dir and --permission-mode to plan AI launch (e56fc49)

## [v0.3.0] — 2026-02-23

### Features

- add interactive main menu when ghaiwpy is called with no args (005af4c)

## [v0.2.2] — 2026-02-23

### Bug Fixes

- backfill every missing model tier individually, not all-or-nothing (3f64ee5)

## [v0.2.1] — 2026-02-23

### Bug Fixes

- resolve init UX issues — model defaults, command override hang, venv prompt (6212dda)

## [v0.2.0] — 2026-02-23

### Features

- add pre-push hook for auto version bumping on push to main (29f6cd4)
- close Bash parity gaps — transcript capture, AI guard, interactive menus, pickers (bddd4d4)
- implement Bash feature parity — model discovery, safety guard, plan mode, terminal title (3ac1392)

### Bug Fixes

- package templates in wheel and suppress structlog noise (3901580)
- let uv manage Python in install.sh instead of requiring system python3 (246a6f5)
- rename all user-facing CLI references from ghaiw to ghaiwpy (5ec1147)
- resolve all ruff lint and format errors, update pre-commit (86af0f3)
- resolve all ruff lint and mypy strict errors (e34d587)

### Refactoring

- rewrite E2E tests as subprocess-based CLI tests (11175e4)

### Tests

- add E2E test suite and smoke scripts (45e5485)
