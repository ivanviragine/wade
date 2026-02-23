# AGENTS.md

This file provides guidance to AI agents when working with code in this repository.

## Project Overview

**ghaiw** (Git + AI Workflow) is a Python CLI toolkit for AI-agent-driven git workflow management. It wraps `gh` CLI and native git to manage GitHub Issues as tasks, git worktrees for isolated development, branch safety checks, and installable Agent Skill files.

This is the Python reimplementation of the original Bash-based ghaiw. It uses Typer for CLI, Pydantic for data models, SQLModel for SQLite persistence, Rich for terminal UI, and structlog for structured logging.

## Terminology

| Term | Meaning |
|------|---------|
| **this project** | This Python source repository — `src/ghaiw/`, `templates/`, `tests/`, `scripts/` |
| **inited project** | Any third-party repo that has run `ghaiw init` to adopt the workflow |
| **skill templates** | Markdown files in `templates/skills/` — installed into inited projects |
| **installed skills** | Copies of skill templates placed in a project's `.claude/skills/` by `ghaiw init` |
| **AGENTS.md pointer** | A short `## Git Workflow` block that `ghaiw init` injects into an inited project's `AGENTS.md` |

## Commands

```bash
# Install (development)
uv pip install -e ".[dev]"

# Run tests
uv run pytest tests/ -v                       # all tests
uv run pytest tests/unit/ -v                  # unit tests only
uv run pytest tests/integration/ -v           # integration tests only
uv run pytest tests/live/ -v                  # live GitHub tests (needs RUN_LIVE_GH_TESTS=1)

# Type check
uv run mypy src/ --strict

# Lint
uv run ruff check src/
uv run ruff format --check src/

# Version bump
python scripts/auto_version.py patch          # bug fixes (0.1.0 → 0.1.1)
python scripts/auto_version.py minor          # new features (0.1.0 → 0.2.0)
python scripts/auto_version.py major          # breaking changes (0.1.0 → 1.0.0)
python scripts/auto_version.py minor --dry-run  # preview only

# Generate changelog
python scripts/changelog.py                   # write CHANGELOG.md
python scripts/changelog.py --stdout          # print to stdout
python scripts/changelog.py --tag v1.0.0      # label unreleased as v1.0.0
```

## Architecture

### Package Structure

```
src/ghaiw/
├── __init__.py          # __version__
├── __main__.py          # python -m ghaiw
├── cli/                 # Typer commands (thin dispatch)
│   ├── main.py          # Root app + interactive menu
│   ├── admin.py         # init, update, deinit, check, check-config
│   ├── task.py          # task plan/create/list/read/update/close/deps
│   └── work.py          # work start/done/sync/list/batch/remove/cd
├── models/              # Pydantic domain models (pure data, no I/O)
│   ├── config.py        # ProjectConfig
│   ├── task.py          # Task, PlanFile, Complexity, Label, TaskState
│   ├── work.py          # WorkSession, WorktreeState, SyncResult, SyncEvent
│   ├── ai.py            # AIToolID, AIModel, ModelTier, TokenUsage
│   ├── deps.py          # DependencyEdge, DependencyGraph
│   └── events.py        # Typed event models
├── db/                  # SQLite via SQLModel
│   ├── engine.py        # Engine creation, WAL mode
│   ├── tables.py        # SQLModel table definitions
│   └── repositories.py  # Repository classes
├── services/            # Business logic (orchestration)
│   ├── task_service.py  # Task CRUD, plan parsing, labels
│   ├── work_service.py  # Work session lifecycle
│   ├── plan_service.py  # AI planning sessions
│   ├── deps_service.py  # Dependency analysis
│   ├── init_service.py  # Project initialization
│   └── check_service.py # Safety checks, config validation
├── providers/           # Task backend providers (ABC)
│   ├── base.py          # AbstractTaskProvider
│   ├── github.py        # GitHubProvider (gh CLI subprocess)
│   └── registry.py      # Provider discovery
├── ai_tools/            # AI tool adapters (ABC, self-registering)
│   ├── base.py          # AbstractAITool with __init_subclass__ registry
│   ├── claude.py        # ClaudeAdapter
│   ├── copilot.py       # CopilotAdapter
│   ├── gemini.py        # GeminiAdapter
│   ├── codex.py         # CodexAdapter
│   ├── antigravity.py   # AntigravityAdapter
│   ├── transcript.py    # Shared transcript parsing
│   └── model_utils.py   # pick_best_for_tier, has_date_suffix
├── git/                 # Git operations (all subprocess)
│   ├── repo.py          # Repo introspection
│   ├── worktree.py      # Worktree create/remove/list
│   ├── branch.py        # Branch naming, creation, deletion
│   ├── sync.py          # Fetch + merge, conflict detection
│   └── pr.py            # PR creation, merge
├── skills/              # Skill file management
│   ├── installer.py     # Install/update/remove skill files
│   └── pointer.py       # AGENTS.md pointer insertion/detection
├── config/              # Configuration management
│   ├── loader.py        # Find + parse config
│   ├── schema.py        # Pydantic Settings model
│   └── defaults.py      # Hardcoded defaults per AI tool
├── ui/                  # Terminal UI (Rich)
│   ├── console.py       # Console class
│   ├── prompts.py       # confirm, input, select, menu
│   └── formatters.py    # OutputFormatter (human + JSON)
├── logging/             # Structured logging
│   └── setup.py         # structlog configuration
└── utils/               # Shared utilities
    ├── clipboard.py     # Cross-platform clipboard
    ├── terminal.py      # Tab title, TTY detection
    ├── slug.py          # Title → URL-safe slug
    ├── markdown.py      # Plan file parsing
    └── process.py       # Subprocess helpers
```

### Layered Architecture

```
CLI Layer      →  can import: services, models, config, logging, ui
Service Layer  →  can import: providers, ai_tools, git, db, models, config, logging
Provider Layer →  can import: models, config, logging  (NO service imports)
AI Tool Layer  →  can import: models, config, logging  (NO service imports)
Git Layer      →  can import: models, config, logging  (NO service imports)
DB Layer       →  can import: models, logging           (NO config imports)
Models Layer   →  can import: nothing (leaf dependency)
```

No circular dependencies. Models are pure data. Services orchestrate.

### Key Design Patterns

- **AI Tool Self-Registration**: `AbstractAITool.__init_subclass__` auto-registers adapters. Adding a new tool = one file with one class.
- **Provider Abstraction**: `AbstractTaskProvider` ABC with GitHub as the only implementation. Future: Linear, Asana, etc.
- **Prompts as .md Templates**: All prompts live in `templates/prompts/`, not inline strings.
- **Synchronous Only**: No asyncio. Process-level parallelism via multiple terminals. SQLite WAL for concurrent access.
- **Structured Logging**: `structlog` with key=value events, written to audit_log table.

### Two Worlds: ghaiw repo vs inited projects

| ghaiw repo (source) | Inited project (output) |
|---------------------|------------------------|
| `src/ghaiw/` | installed `ghaiw` binary (via pip/uv) |
| `templates/skills/*/SKILL.md` | `.claude/skills/*/SKILL.md` |
| `templates/agents-pointer.md` | `## Git Workflow` block in target `AGENTS.md` |
| `AGENTS.md` (this file) | target project's own `AGENTS.md` |

When developing ghaiw, **only touch the left column**.

## Dependencies

- **Python** 3.11+
- **git** 2.20+
- **gh CLI** — must be authenticated
- **uv** — recommended for development

## Testing

### Test Organization

```
tests/
├── conftest.py              # Shared fixtures (tmp_git_repo, tmp_ghaiw_project)
├── test_cli_basics.py       # Basic CLI command tests
├── unit/                    # Pure logic, no subprocess, no git
│   ├── test_models/
│   ├── test_services/
│   ├── test_config/
│   ├── test_transcript/
│   └── test_utils/
├── integration/             # Needs git repos, mock gh
│   ├── test_check.py
│   ├── test_init.py
│   ├── test_work_lifecycle.py
│   └── test_skill_install.py
└── live/                    # Needs gh auth + network (gated)
    └── test_gh_integration.py
```

### Running Tests

```bash
# All tests (excluding live)
uv run pytest tests/ -v --ignore=tests/live

# Specific test file
uv run pytest tests/unit/test_services/test_work_done_sync.py -v

# With coverage
uv run pytest tests/ --cov=ghaiw --cov-report=term-missing
```

### Test Fixtures

Key fixtures in `conftest.py`:
- `tmp_git_repo` — Fresh git repo with initial commit and `main` branch
- `tmp_ghaiw_project` — Git repo with `.ghaiw.yml` config file

### Test Rules

- Every bug fix must include a regression test
- Use mocks for `gh` CLI and AI tool calls
- Gate live GitHub tests with `RUN_LIVE_GH_TESTS=1`
- Prefer exact assertions over loose substring checks

## Version Bumping

Version lives in `src/ghaiw/__init__.py` and `pyproject.toml`. Use `scripts/auto_version.py`:

```bash
python scripts/auto_version.py patch   # bug fixes, docs
python scripts/auto_version.py minor   # new features
python scripts/auto_version.py major   # breaking changes
```

The script updates both files, generates CHANGELOG.md, commits, and creates an annotated git tag.

## Commit Conventions

Use [Conventional Commits](https://www.conventionalcommits.org/) format:
- `feat:` — new features → minor bump
- `fix:` — bug fixes → patch bump
- `docs:` — documentation → patch bump
- `refactor:` — code restructuring → patch bump
- `test:` — test changes → patch bump
- `chore:` — maintenance → patch bump
- Breaking changes (`feat!:` or `BREAKING CHANGE`) → major bump

## Documentation Rules

Every change must include documentation updates:
1. **AGENTS.md** — if architecture or conventions changed
2. **README.md** — if user-facing behavior changed
3. **templates/skills/** — if agent-facing workflows changed
