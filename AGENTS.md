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
| **workflow templates** | Fixed WADE lifecycle files in `templates/workflows/`; rendered to `.wade/session/WORKFLOW.md` and never replaceable by skill configuration |
| **methodology skill templates** | WADE-agnostic replaceable methods in `templates/skills/` (planning, implementation, review, and dependency analysis) |
| **session skill snapshots** | Immutable copies of active and available skills under `.wade/session/skills/`, including project skills discovered from the worktree and main checkout |
| **support skills** | Fixed WADE command-support skills projected into `.claude/skills/` per worktree bootstrap; they are not replaceable methodology |
| **AGENTS.md pointer** | A short `## Git Workflow` block that **worktree bootstrap** injects into an inited project's `AGENTS.md` per session, not by `wade init` |

**This `AGENTS.md` governs development of WADE itself.** Skills, the pointer, and the progressive disclosure architecture are all *outputs* of WADE — artifacts installed into inited projects, not rules for developing WADE.

**WADE uses its own workflow.** This repo is itself an inited project. Follow the `## Git Workflow` pointer at the bottom, then the fixed `.wade/session/WORKFLOW.md` and its selected WORK methodology.

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
uv run python scripts/auto_version.py patch # Version bump (patch/minor/major)

# Version bumps MUST be done with the script above. NEVER bump pyproject.toml
# manually, as the script generates CHANGELOG.md and git tags automatically.
```

> Full commands reference: see `docs/dev/architecture.md`

## Architecture

```
CLI Layer      ->  can import: services, models, config, logging, ui
Service Layer  ->  can import: providers, crossby (AI tool adapters), git, models, config, logging
Provider Layer ->  can import: models, config, logging  (NO service imports)
Git Layer      ->  can import: models, config, logging  (NO service imports)
Models Layer   ->  can import: nothing (leaf dependency)
```

No circular dependencies. Models are pure data. Services orchestrate. **Never import a higher layer from a lower layer.**

> **The `db/` package is unused scaffolding** — intentionally left out of the
> layering rules above. No code path under `services/` or `cli/` writes
> session/worktree/PR rows, so its sole reader
> (`implementation_service/cleanup._preserve_session_data`, a single
> `SessionRepository.get_by_worktree_path` call) always gets an empty result and
> falls back to directory-presence detection. Real persisted state lives in
> GitHub (PR/issue body markers, labels) and worktree files, **not** SQLite. The
> code still exists but is inert; removal is tracked as #357 C5.

> **Deterministic git-hook install/reconcile/build logic lives in `git/hooks.py`**
> (git layer), not `skills/installer.py`. Packaged template-asset loaders
> (prompt/skill/hook templates) live in `utils/templates.py`, a leaf module.
> Leaf `utils/` modules import nothing from wade, so any layer — including the
> git layer — may import them without breaking the rules above.
> `skills` itself is a lower utility/template layer — services may import it for
> **skill-file management** (`install_skills`, the `*_SKILLS` registries,
> `ensure_knowledge_merge_attributes`, gitignore/cross-tool constants), never the
> reverse. Those residual `service -> skills` imports are sanctioned, not
> layering violations.

AI tool adapters are not part of this repo — they live in the external [`crossby`](https://github.com/ivanviragine/crossby) package (`pyproject.toml`). See `docs/dev/architecture.md` for what moved there.

CLI modules are thin dispatch — they parse flags via Typer, then call service methods. Business logic lives in `services/`, not in `cli/`.

> Full package structure, command dispatch, config system, and subsystem details: see `docs/dev/architecture.md`

### Key Design Patterns

- **AI Tool Adapters via crossby**: `AbstractAITool` and every tool-specific adapter (Claude, Cursor, Copilot, Codex, OpenCode, Antigravity, Antigravity CLI, VS Code) live in the external `crossby` dependency, not this repo. Wade's services import `crossby.ai_tools` and `crossby.models.ai` directly. Adding a new AI tool means contributing to crossby, not wade.
- **Provider Abstraction**: `AbstractTaskProvider` ABC with pluggable backends (GitHub via `gh` CLI, ClickUp via REST API, Markdown via a single central file). Non-GitHub providers compose `GitHubPRDelegateMixin` so PR-review APIs still flow through `gh`.
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
| `templates/workflows/<session>.md` | `.wade/session/WORKFLOW.md` |
| `templates/skills/<method>/SKILL.md` | `.wade/session/skills/{builtin,project}/.../SKILL.md` |
| support skills in `templates/skills/` | `.claude/skills/<name>/SKILL.md` |
| `templates/agents-pointer.md` | `## Git Workflow` block in target `AGENTS.md` |
| `AGENTS.md` (this file) | target project's own `AGENTS.md` |

When developing WADE, **only touch the left column**. Put lifecycle steps and WADE commands in `templates/workflows/`; put replaceable methodology in WADE-agnostic `templates/skills/<method>/SKILL.md`. Never edit `.claude/skills/` or `.wade/session/` outputs directly.

The right-column artifacts (workflow, frozen skill bundle, support skills, the `## Git Workflow` pointer, and tool configuration) are produced **per session by worktree bootstrap** (`bootstrap_worktree` in `implementation_service/bootstrap.py`, invoked by `wade implement`/`wade plan`/`wade review`; standalone delegations use operation bundles), *not* by `wade init`. `wade init` writes only `.wade.yml`, optional provider/knowledge files, and the `.wade/` manifest.

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
- [ ] **`templates/skills/`** — updated if agent-facing rules changed (plan-session for planning, implementation-session for implementation, review-pr-comments-session for reviews)
- [ ] **Commit** — uses conventional-commit prefix

Note: for inited projects, the doc update pass is an explicit mandatory step in
the fixed implementation and PR-comment workflows. WADE records an `--updated`
or reasoned `--not-needed` receipt for the current commit; methodology skills do
not own or satisfy that step. This repo's own checklist above is unaffected.

> Full 10-item checklist, documentation rules, feedback loop, and correction-driven docs: see `docs/dev/documentation-policies.md`

## Detailed Reference

Read these on-demand when working in a specific area:

| When you are... | Read |
|-----------------|------|
| Modifying architecture, config, or commands | `docs/dev/architecture.md` |
| Adding an AI tool, provider, or subcommand | `docs/dev/extending.md` |
| Writing or running tests | `docs/dev/testing.md` |
| Working on discovery, snapshots, support skills, pointer system, or `wade init` | `docs/dev/skills-system.md` |
| Working on fixed workflows, dynamic bindings, manifests, or delegation skill contracts | `docs/dev/workflows-and-skills.md` |
| Updating documentation policies | `docs/dev/documentation-policies.md` |
