# AGENTS.md

This file provides guidance to AI agents working on the WADE codebase.
For detailed reference on specific topics, see `docs/dev/`.

## Project Overview

**WADE** (Workflow for AI-Driven Engineering) is a Python CLI toolkit for AI-agent-driven git workflow management. It wraps `gh` CLI and native git to manage GitHub Issues as tasks, git worktrees for isolated development, branch safety checks, and installable Agent Skill files. CLI entry point: **`wade`**.

## Terminology

Two distinct worlds interact in this codebase. Always be clear which one you are working in:

| Term | Meaning |
|------|---------|
| **the WADE repo** / **this project** | This source repository — `src/wade/`, `templates/`, `tests/`, `scripts/` |
| **inited project** / **target project** | Any third-party repo that has run `wade init` to adopt the workflow |
| **skill templates** | Markdown files in `templates/skills/` — the source of truth, part of the WADE repo |
| **installed skills** | Copies (or symlinks) of skill templates placed in a project's `.claude/skills/` by `wade init` |
| **AGENTS.md pointer** | A short `## Git Workflow` block that `wade init` injects into an inited project's `AGENTS.md` |

**This `AGENTS.md` governs development of WADE itself.** Skills, the pointer, and the progressive disclosure architecture are all *outputs* of WADE — artifacts installed into inited projects, not rules for developing WADE.

**WADE uses its own workflow.** This repo is itself an inited project. Follow the `## Git Workflow` pointer at the bottom and the phase-specific skill referenced in your clipboard prompt.

## Commands

> **AI agents: always run the scripts below — never improvise raw `uv run pytest` / `mypy` / `ruff` calls.**

| Script | Purpose |
|--------|---------|
| `./scripts/test.sh` | Run all tests (excludes live) |
| `./scripts/test.sh tests/unit/` | Unit tests only |
| `./scripts/test-e2e.sh` | Deterministic E2E contract tests (host lane) |
| `./scripts/test-e2e-docker.sh` | Deterministic E2E contract tests in Docker (CI-equivalent) |
| `./scripts/check.sh` | Lint + type-check (both) |
| `./scripts/check.sh --lint` | Lint + format check only |
| `./scripts/check.sh --types` | Type check (strict mypy) only |
| `./scripts/fmt.sh` | Auto-format source in-place |
| `./scripts/check-all.sh` | Full checklist (test + check) |

```bash
uv pip install -e ".[dev]"           # Install for development
python scripts/auto_version.py patch # Version bump (patch/minor/major)

# Version bumps MUST be done with the script above. NEVER bump pyproject.toml
# manually, as the script generates CHANGELOG.md and git tags automatically.
```

> Full commands reference: see `docs/dev/architecture.md`

## Architecture

```
CLI Layer      ->  can import: services, models, config, logging, ui
Service Layer  ->  can import: providers, ai_tools, git, db, models, config, logging
Provider Layer ->  can import: models, config, logging  (NO service imports)
AI Tool Layer  ->  can import: models, config, logging  (NO service imports)
Git Layer      ->  can import: models, config, logging  (NO service imports)
DB Layer       ->  can import: models, logging           (NO config imports)
Models Layer   ->  can import: nothing (leaf dependency)
```

No circular dependencies. Models are pure data. Services orchestrate. **Never import a higher layer from a lower layer.**

CLI modules are thin dispatch — they parse flags via Typer, then call service methods. Business logic lives in `services/`, not in `cli/`.

> Full package structure, command dispatch, config system, and subsystem details: see `docs/dev/architecture.md`

### Key Design Patterns

- **AI Tool Self-Registration**: `AbstractAITool.__init_subclass__` auto-registers adapters. Adding a new AI tool = one file, one class.
- **Provider Abstraction**: `AbstractTaskProvider` ABC with pluggable backends (currently GitHub via `gh` CLI).
- **Prompts as .md Templates**: All AI prompts live in `templates/prompts/`, not inline strings.
- **Synchronous Only**: No asyncio. Process-level parallelism via multiple terminals.
- **Pydantic Everywhere**: All data structures are Pydantic `BaseModel` subclasses, not dicts.

## Design Principles

### Determinism via Services

All deterministic operations — git commands, state transitions, file manipulation, API calls — **must live in service/utility code**, never in AI agent reasoning.

- **Code decides and executes** — fetch, merge, branch creation, worktree lifecycle, issue state changes. Codified in `services/`, `git/`, `providers/`.
- **Agents interpret and decide** — reading conflict diffs, choosing resolution strategies, composing commit messages. Guided by skills.

**Test**: "Can an AI agent get this wrong by reasoning about it?" If yes, put it in code.

### Two Worlds

Everything in this repo exists in one of two worlds:

| WADE repo (source) | Inited project (output) |
|---------------------|------------------------|
| `src/wade/` | installed `wade` binary |
| `templates/skills/<name>/SKILL.md` | `.claude/skills/<name>/SKILL.md` |
| `templates/agents-pointer.md` | `## Git Workflow` block in target `AGENTS.md` |
| `AGENTS.md` (this file) | target project's own `AGENTS.md` |

When developing WADE, **only touch the left column**. Always edit `templates/skills/<name>/SKILL.md` directly — never edit files inside `.claude/skills/` (those are symlinks in this repo, copies in inited projects).

> Skills system deep dive (symlinks, pointer markers, installation lifecycle): see `docs/dev/skills-system.md`

## Conventions

### Naming

- **Modules**: `snake_case.py` — one module per concern
- **Classes**: `PascalCase` — Pydantic models, ABCs, adapters
- **Functions**: `snake_case` — `_` prefix for private helpers
- **Constants**: `UPPER_SNAKE_CASE`
- **Enums**: `StrEnum` for string-valued enums
- **CLI commands**: top-level commands (`wade plan`, `wade implement`)

### Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):
`feat:` (minor), `fix:` (patch), `docs:` (patch), `refactor:` (patch),
`test:` (patch), `chore:` (patch). Breaking changes (`feat!:`) -> major.

## Change Checklist

Before considering any work complete:

- [ ] **Code** — `./scripts/test.sh` passes
- [ ] **Types + Lint** — `./scripts/check.sh` passes (or run both at once: `./scripts/check-all.sh`)
- [ ] **`AGENTS.md`** — updated if architecture, conventions, or workflow changed
- [ ] **`README.md`** — updated if user-facing behavior changed
- [ ] **`templates/skills/`** — updated if agent-facing rules changed (plan-session for planning, implementation-session for implementation, address-reviews-session for reviews)
- [ ] **Commit** — uses conventional-commit prefix

> Full 10-item checklist, documentation rules, feedback loop, and correction-driven docs: see `docs/dev/documentation-policies.md`

## Detailed Reference

Read these on-demand when working in a specific area:

| When you are... | Read |
|-----------------|------|
| Modifying architecture, config, or commands | `docs/dev/architecture.md` |
| Adding an AI tool, provider, or subcommand | `docs/dev/extending.md` |
| Writing or running tests | `docs/dev/testing.md` |
| Working on skills, pointer system, or `wade init` | `docs/dev/skills-system.md` |
| Updating documentation policies | `docs/dev/documentation-policies.md` |

<!-- wade:pointer:start -->
## Git Workflow

**First action every session** — read the skill referenced in your clipboard
prompt for full session rules.

Critical rules you must always follow:

1. Never create GitHub Issues via `gh issue create` — use `wade task create`
   or read @.claude/skills/task/SKILL.md
2. Never create PRs manually (`gh pr create`) or push branches directly — use
   `wade implementation-session done`
<!-- wade:pointer:end -->
