# Contributing to WADE

## Development Setup

**Prerequisites:** [gh CLI](https://cli.github.com/) must be installed and authenticated — WADE shells out to it for all GitHub operations.

```bash
gh auth login     # first-time setup
gh auth status    # verify it worked
```

Then clone and install:

```bash
git clone https://github.com/ivanviragine/wade.git
cd wade
uv pip install -e ".[dev]"
```

## Running Checks

> Always use the scripts — never invoke `pytest`, `mypy`, or `ruff` directly.

| Command | What it does |
|---------|-------------|
| `./scripts/test.sh` | Run all tests (excludes live) |
| `./scripts/test.sh tests/unit/` | Unit tests only |
| `./scripts/check.sh` | Lint + type-check |
| `./scripts/check.sh --lint` | Lint only |
| `./scripts/check.sh --types` | mypy strict only |
| `./scripts/fmt.sh` | Auto-format in-place |
| `./scripts/check-all.sh` | Full suite (tests + lint + types) |

## Architecture

```
CLI Layer      →  services, models, config, logging, ui
Service Layer  →  providers, ai_tools, git, db, models, config, logging
Provider Layer →  models, config, logging  (no service imports)
AI Tool Layer  →  models, config, logging  (no service imports)
Git Layer      →  models, config, logging  (no service imports)
DB Layer       →  models, logging          (no config imports)
Models Layer   →  nothing (leaf)
```

CLI modules are thin dispatch — parse flags with Typer, call service methods. Business logic lives in `services/`, not `cli/`.

See `docs/dev/architecture.md` for the full reference.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | Semver bump |
|--------|------------|
| `feat:` | minor |
| `feat!:` | major (breaking) |
| `fix:`, `docs:`, `refactor:`, `chore:`, `test:` | patch |

## Releasing

### Every release

```bash
uv run python scripts/auto_version.py patch --push   # or minor / major
```

This bumps the version, generates `CHANGELOG.md`, commits, tags, and pushes. CI then:

1. Creates a **draft GitHub Release** with the changelog notes
2. You review it on GitHub and click **Publish Release**
3. CI publishes the wheel to PyPI automatically

### Version bump types

```bash
uv run python scripts/auto_version.py patch   # bug fixes     0.1.0 → 0.1.1
uv run python scripts/auto_version.py minor   # new features  0.1.0 → 0.2.0
uv run python scripts/auto_version.py major   # breaking      0.1.0 → 1.0.0
```

Add `--dry-run` to preview without making changes.

## Detailed Reference

| Topic | File |
|-------|------|
| Architecture, config, commands | `docs/dev/architecture.md` |
| Adding AI tools, providers, subcommands | `docs/dev/extending.md` |
| Writing and running tests | `docs/dev/testing.md` |
| Skills system and `wade init` | `docs/dev/skills-system.md` |
| Documentation policies | `docs/dev/documentation-policies.md` |
