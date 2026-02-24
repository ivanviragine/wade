# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Conventional Commits](https://conventionalcommits.org/).

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
