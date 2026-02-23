# AGENTS.md

This file provides guidance to AI agents when working with code in this repository.


## Project Overview

**ghaiw** (Git + AI Workflow) is a Python CLI toolkit for AI-agent-driven git workflow management. It wraps `gh` CLI and native git to manage GitHub Issues as tasks, git worktrees for isolated development, branch safety checks, and installable Agent Skill files.

This is the Python reimplementation of the original Bash-based ghaiw. It uses Typer for CLI dispatch, Pydantic for data models, SQLModel for SQLite persistence, Rich for terminal UI, and structlog for structured logging. The CLI entry point is **`ghaiwpy`** (not `ghaiw`).

## Terminology

Two distinct worlds interact in this codebase. Always be clear which one you are working in:

| Term | Meaning |
|------|---------|
| **the ghaiw-py repo** / **this project** | This source repository — `src/ghaiw/`, `templates/`, `tests/`, `scripts/` |
| **inited project** / **target project** | Any third-party repo that has run `ghaiwpy init` to adopt the workflow |
| **skill templates** | Markdown files in `templates/skills/` — the source of truth, part of the ghaiw-py repo |
| **installed skills** | Copies (or symlinks) of skill templates placed in a project's `.claude/skills/` by `ghaiwpy init` |
| **AGENTS.md pointer** | A short `## Git Workflow` block that `ghaiwpy init` injects into an inited project's `AGENTS.md` |

**This `AGENTS.md` governs development of ghaiw-py itself.** The skills, the AGENTS.md pointer, and the progressive disclosure architecture described below are all *outputs* of ghaiw — artifacts installed into inited projects, not rules for developing ghaiw-py.

**ghaiw-py uses its own workflow.** This repo is itself an inited project — it has `.ghaiw.yml`, uses `ghaiwpy task` / `ghaiwpy work` for its own development, and the `## Git Workflow` section at the bottom of this file is the self-installed pointer. When developing ghaiw-py, follow that pointer (read and follow `@.claude/skills/workflow/SKILL.md`).

## Commands

```bash
# Install (development)
uv pip install -e ".[dev]"

# Run tests
uv run pytest tests/ -v                       # all tests
uv run pytest tests/unit/ -v                  # unit tests only
uv run pytest tests/integration/ -v           # integration tests only
uv run pytest tests/live/ -v                  # live GitHub tests (needs RUN_LIVE_GH_TESTS=1)

# Run a single test file
uv run pytest tests/unit/test_models/test_config.py -v

# Run tests matching a pattern
uv run pytest tests/ -v -k "test_pattern"

# Type check
uv run mypy src/ --strict

# Lint + format check
uv run ruff check src/
uv run ruff format --check src/

# Version bump
python scripts/auto_version.py patch           # bug fixes, docs (0.1.0 -> 0.1.1)
python scripts/auto_version.py minor           # new features, flags (0.1.0 -> 0.2.0)
python scripts/auto_version.py major           # breaking changes (0.1.0 -> 1.0.0)
python scripts/auto_version.py minor --dry-run # preview only

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
│   ├── main.py          # Root app + interactive menu, subcommand registration
│   ├── admin.py         # init, update, deinit, check, check-config, shell-init
│   ├── task.py          # task plan/create/list/read/update/close/deps
│   └── work.py          # work start/done/sync/list/batch/remove/cd (interactive menu)
├── models/              # Pydantic domain models (pure data, no I/O)
│   ├── config.py        # ProjectConfig, ProjectSettings, AIConfig, ComplexityModelMapping
│   ├── task.py          # Task, PlanFile, Complexity, Label, TaskState
│   ├── work.py          # WorkSession, WorktreeState, SyncResult, SyncEvent, MergeStrategy
│   ├── ai.py            # AIToolID, AIModel, ModelTier, TokenUsage, AIToolCapabilities
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
│   ├── loader.py        # Find + parse .ghaiw.yml (walk up from CWD)
│   ├── schema.py        # Re-exports from models (Pydantic Settings)
│   ├── defaults.py      # Hardcoded defaults per AI tool
│   ├── migrations.py    # Config migration pipeline (v1→v2, model normalization)
│   ├── claude_allowlist.py  # .claude/settings.json allowlist management
│   └── legacy.py        # Legacy artifact cleanup
├── ui/                  # Terminal UI (Rich)
│   ├── console.py       # Console class
│   ├── prompts.py       # confirm, input, select, menu
│   └── formatters.py    # OutputFormatter (human + JSON)
├── logging/             # Structured logging
│   └── setup.py         # structlog configuration
└── utils/               # Shared utilities
    ├── clipboard.py     # Cross-platform clipboard
    ├── terminal.py      # Tab title, TTY detection, launch_in_new_terminal
    ├── slug.py          # Title -> URL-safe slug
    ├── markdown.py      # Plan file parsing
    ├── process.py       # Subprocess helpers
    └── install.py       # Self-upgrade helpers (venv/source detection, re-exec)
```

### Layered Architecture

```
CLI Layer      ->  can import: services, models, config, logging, ui
Service Layer  ->  can import: providers, ai_tools, git, db, models, config, logging
Provider Layer ->  can import: models, config, logging  (NO service imports)
AI Tool Layer  ->  can import: models, config, logging  (NO service imports)
Git Layer      ->  can import: models, config, logging  (NO service imports)
DB Layer       ->  can import: models, logging           (NO config imports)
Models Layer   ->  can import: nothing (leaf dependency)
```

No circular dependencies. Models are pure data. Services orchestrate. **Never import a higher layer from a lower layer** — e.g., a provider must not import a service, and a model must not import anything.

### Command Dispatch

`src/ghaiw/cli/main.py` is the root Typer application. It registers subcommand groups (`task`, `work`) and admin commands (`init`, `update`, `deinit`, `check`, `check-config`, `shell-init`). The `tasks` alias is registered as a hidden Typer group pointing to the same `task_app`. The `ghaiwpy` entry point (defined in `pyproject.toml` as `ghaiw.cli.main:app`) invokes the root app.

CLI modules are **thin dispatch layers** — they parse flags via Typer, then call service methods. Business logic lives in `services/`, not in `cli/`.

**Interactive menus**: `ghaiwpy work` with no subcommand shows an interactive menu (start/done/sync/list/batch/remove). `ghaiwpy task create` without `--plan-file` prompts interactively for title and body.

**Shell integration**: `ghaiwpy shell-init` outputs a shell function wrapper for `eval "$(ghaiwpy shell-init)"` that intercepts `ghaiwpy work cd <n>` to perform a real `cd` in the caller's shell.

### Key Design Patterns

- **AI Tool Self-Registration**: `AbstractAITool.__init_subclass__` auto-registers concrete adapters into `_registry`. Adding a new AI tool means creating one file with one class that sets `TOOL_ID` — no other files need modification. Discovery is automatic.
- **Provider Abstraction**: `AbstractTaskProvider` ABC with `GitHubProvider` as the current implementation (wraps `gh` CLI via subprocess). Future providers (Linear, Asana, Jira) can be added as separate classes.
- **Prompts as .md Templates**: All AI prompts live in `templates/prompts/`, not inline strings. This keeps prompts editable and version-controlled separately from code.
- **Synchronous Only**: No asyncio. Process-level parallelism via multiple terminals for `work batch`. SQLite WAL mode for concurrent access from multiple worktrees.
- **Structured Logging**: `structlog` with key=value events. Logs are written to an audit_log SQLite table for traceability.
- **Pydantic Everywhere**: All data structures are Pydantic `BaseModel` subclasses. Config is parsed into `ProjectConfig`. Domain objects (`Task`, `WorkSession`, `SyncResult`) are typed models, not dicts.

### Config System

`config/loader.py` walks up from CWD to find `.ghaiw.yml` and parses it via PyYAML into a `ProjectConfig` Pydantic model. The v2 config format has nested sections:

```yaml
version: 2
project:
  main_branch: main
  issue_label: feature-plan
  worktrees_dir: ../.worktrees
  branch_prefix: feat
  merge_strategy: PR
ai:
  default_tool: claude
  plan:
    tool: claude
    model: ""
  deps:
    tool: claude
  work:
    tool: claude
models:
  claude:
    easy: claude-haiku-4-5
    medium: claude-haiku-4-5
    complex: claude-sonnet-4
    very_complex: claude-opus-4
provider:
  name: github
hooks:
  post_worktree_create: scripts/setup-worktree.sh
  copy_to_worktree:
    - .env
```

**Model complexity mapping**: The `models` section maps AI tool names to complexity-tiered model IDs (`easy`, `medium`, `complex`, `very_complex`). When `work start` is invoked, the service reads the `## Complexity` section from the issue body, maps it to the appropriate configured model, and passes it as `--model` to the AI tool — unless the user explicitly passed `--model` themselves.

**Per-command AI tool and model overrides**: The `ai` section supports `plan`, `deps`, and `work` sub-sections, each with optional `tool` and `model` keys. The fallback chain is: CLI `--ai`/`--model` flag -> command-specific config -> global `default_tool`. This is implemented in `ProjectConfig.get_ai_tool(command)` and `ProjectConfig.get_model(command)`.

### Config Migration Pipeline

`config/migrations.py` provides 7 idempotent migrations run during `ghaiwpy update`:

| # | Function | What it does |
|---|----------|-------------|
| 1 | `ensure_version(raw)` | Set `version: 2` if missing |
| 2 | `migrate_deprecated_model_values(raw, ai_tool)` | Replace deprecated model names (e.g., `gemini-3-flash` -> `claude-haiku-4-5`) |
| 3 | `migrate_flat_to_nested_models(raw, ai_tool)` | Move v1 flat `model_easy` keys to v2 nested `models.<tool>.easy` |
| 4 | `normalize_model_format(raw, ai_tool)` | Dashes/dots based on tool (Claude=dashes, Copilot=dots) |
| 5 | `backfill_missing_model_keys(raw, ai_tool)` | Fill missing tier keys with probed/default models |
| 6 | `backfill_per_command_keys(raw)` | Ensure `ai.plan`, `ai.deps`, `ai.work` sections exist |
| 7 | `normalize_merge_strategy(raw)` | Lowercase `pr` -> uppercase `PR` |

`run_all_migrations(config_path)` loads YAML, runs all 7 in order, writes back only if changed. Returns `True` if the file was modified.

### Update Flow

`ghaiwpy update` performs 12 steps:

1. Validate repo + config existence
2. Self-upgrade if source version differs (see below)
3. Read old version from manifest
4. Show version transition message
5. Run config migration pipeline
6. Reload config + backfill probed models
7. Refresh skill files
8. Clean legacy artifacts (`config/legacy.py`)
9. Configure Claude Code allowlist (`config/claude_allowlist.py`)
10. Configure Gemini experimental (if applicable)
11. Refresh .gitignore + AGENTS.md pointer
12. Rebuild manifest with version

**Self-upgrade mechanism**: When `ghaiwpy` is installed via `install.sh` (frozen venv at `~/.local/share/ghaiw/venv/`), the installer records the source repo path in `ghaiw-source.txt`. On `ghaiwpy update`, if the installed version differs from the source version, `utils/install.py:self_upgrade()` reinstalls from source and `re_exec()` restarts the process with the new code. Editable installs (`uv pip install -e .`) skip this naturally. Pass `--skip-self-upgrade` to bypass.

### AI Interaction Pattern

All AI-interactive commands follow the same pattern:

1. **Tool selection** — If no `--ai` flag is given, use the tool from config (via `ProjectConfig.get_ai_tool()`). If that's empty, prompt the user interactively via `ui/prompts.py`.
2. **Clipboard prompt** — Copy a starter prompt to the clipboard via `utils/clipboard.py`, then print a message telling the user to paste it.
3. **Launch AI CLI** — Execute the AI tool binary via `AbstractAITool.launch()`. The tool runs interactively in the terminal.
4. **Post-AI processing** — After the AI CLI exits, the service picks up where it left off (e.g., detecting new issues, parsing output files, capturing token usage from transcripts).

Each AI tool adapter implements `capabilities()` (binary name, model flag syntax, headless flag), `launch()`, `parse_transcript()`, `is_model_compatible()`, and `build_launch_command()`. The `launch()` method accepts an optional `transcript_path: Path | None` parameter — when provided, the adapter captures session output to that file for post-session token usage extraction. When adding a new AI-interactive command, follow this existing pattern.

**Deps interactive fallback**: When headless analysis fails (tool doesn't support `--print`/`--prompt`), `deps_service.py` falls back to interactive mode: copies the dependency prompt to clipboard, launches the AI tool interactively, then reads the output from `{plan_dir}/deps-output.txt` after exit.

### Issue Detection (Snapshot/Diff Pattern)

`task plan` uses a snapshot/diff pattern to detect issues created during an AI session (Path A — backward-compatible fallback):

1. **Before AI** — Snapshot all open issue numbers with the configured label
2. **AI runs** — The agent creates issues via `ghaiwpy task create` from within the AI CLI
3. **After AI** — Compare current issue numbers against the pre-snapshot, returning only newly created ones

This avoids requiring the AI to report back which issues it created — the service detects them deterministically. When no issues are detected (Path B), the service reads plan files from the session temp dir instead.

### Merge Strategy

`MergeStrategy` (config key `project.merge_strategy`) controls how feature branches are merged into main:
- **`PR`** (default) — The agent runs `ghaiwpy work done` during its session to push the branch and create a PR via `gh pr create`. The worktree is **not** cleaned up by `work done` — it is cleaned up automatically by `work start` after the human merges the PR. When the tool exits, `work start`'s post-work prompt detects the PR and asks "Do you want to merge this PR?" — if yes, squash-merges via `gh pr merge --squash --delete-branch`.
- **`direct`** — Merge locally into main, push, and clean up the worktree. Useful for solo projects or repos without branch protection.

`ghaiwpy work done` handles PR creation / direct merge. The post-work lifecycle prompt handles the merge decision (PR strategy) or local merge options (direct strategy).

### Two Worlds: ghaiw-py repo vs inited projects

This boundary is critical. Everything in this repo exists in one of two worlds:

| ghaiw-py repo (source) | Inited project (output) |
|---------------------|------------------------|
| `src/ghaiw/` | installed `ghaiwpy` binary (via pip/uv) |
| `templates/skills/<name>/SKILL.md` | `.claude/skills/<name>/SKILL.md` |
| `templates/agents-pointer.md` | `## Git Workflow` block in target `AGENTS.md` |
| `AGENTS.md` (this file) | target project's own `AGENTS.md` (different content) |
| `.ghaiw.yml` (this repo's config) | target project's own `.ghaiw.yml` |

When developing ghaiw-py, **only touch the left column**. The right column is what users get after running `ghaiwpy init` in their own projects.

### AGENTS.md and CLAUDE.md

`AGENTS.md` is the canonical agent guidance file for this repo. `CLAUDE.md` is a committed symlink -> `AGENTS.md`, providing Claude Code discovery without duplicating content. **Always edit `AGENTS.md` directly** — changes reflect in `CLAUDE.md` automatically via the symlink.

In inited projects, `ghaiwpy init` writes the workflow pointer to whichever of `AGENTS.md` / `CLAUDE.md` already exists (preferring `AGENTS.md`), or creates `AGENTS.md` if neither exists.

### Skill File Symlink Structure

In this repo (self-init), the skill directories should be symlinks rather than file copies, so edits to skill templates are reflected immediately without re-running `ghaiwpy init`:

```
.claude/skills/workflow  ->  ../../templates/skills/workflow  (symlink)
.claude/skills/task      ->  ../../templates/skills/task      (symlink)
.claude/skills/sync      ->  ../../templates/skills/sync      (symlink)

.github/skills/          ->  (same targets, separate symlinks)
.agents/skills/          ->  (same targets, separate symlinks)
.gemini/skills/          ->  (same targets, separate symlinks)
```

**Always edit `templates/skills/<name>/SKILL.md`** — never edit files inside `.claude/skills/`, `.github/skills/`, `.agents/skills/`, or `.gemini/skills/` directly. In this repo those are all symlinks; in inited projects they are copies that would be overwritten by `ghaiwpy update`.

In inited projects (normal init), `ghaiwpy init` copies skill files (not symlinks), so agents in those projects read standalone files that don't change unless `ghaiwpy update` is run.

### Skill Installation Lifecycle

`ghaiwpy init` installs skills file-by-file via the `skills/installer.py` module. When adding a new skill:

1. Create the skill template in `templates/skills/<name>/SKILL.md`
2. Register the skill in `skills/installer.py` — add it to the install, self-init (symlink), and update paths
3. Add the skill directory to the cleanup logic in `init_service.py` (deinit path)
4. Add the skill reference to `templates/skills/workflow/SKILL.md`

The self-init path creates symlinks from `.claude/skills/<name>` -> `../../templates/skills/<name>` to avoid file duplication when working on ghaiw-py itself.

### Agent Skills (templates/skills/)

> **Scope: inited projects.** The skill templates in `templates/skills/` are installed into inited projects by `ghaiwpy init`. They are *not* guidance for developing ghaiw-py itself — they teach AI agents in target projects how to use the ghaiw workflow. When you are developing ghaiw-py, treat these files as **output artifacts** you are authoring, not as rules you follow.

Skill templates are Markdown files installed to an inited project's `.claude/skills/` by `ghaiwpy init`, with symlinks from `.github/skills/`, `.agents/skills/`, and `.gemini/skills/` for cross-tool discovery. They teach AI agents the ghaiw workflow (task management, sync, PR summaries, session rules). The `work sync` command emits structured JSON events on stdout (with `--json`) for machine consumption.

#### Progressive Disclosure Architecture (for inited projects)

> **Scope: inited projects.** The following describes how ghaiw structures agent-facing documentation *in the projects it is installed into* — not how this repo's own documentation is organized.

The agent-facing documentation in inited projects follows a **progressive disclosure** pattern:

1. **AGENTS.md pointer** — `ghaiwpy init` reads `templates/agents-pointer.md` and inserts its content into the target project's `AGENTS.md`. It says "read and follow @.claude/skills/workflow/SKILL.md before doing anything else" plus a handful of critical inline rules as a safety net. **To change what gets injected into inited projects, edit `templates/agents-pointer.md`** — not this repo's own `## Git Workflow` section, which is only the self-installed copy for this repo.
2. **`templates/skills/workflow/SKILL.md`** — Always-on session rules: worktree safety, commit conventions, planning lifecycle, sync workflow, and pointers to task-specific skills. Agents read this at session start.
3. **`templates/skills/*/SKILL.md`** (task, sync, deps, pr-summary) — Self-contained task skills with full workflows, commands, flags, examples, and decision trees. Agents read these on-demand when performing a specific task.

This keeps the project's `AGENTS.md` minimal (one pointer) while the workflow skill handles "what rules to follow" and task skills handle "how to do it". When adding new agent-facing commands or workflows:
- **Do NOT add rules or commands to the AGENTS.md pointer** — put session rules in the workflow skill and task workflows in task skills.
- **Create or update a skill** in `templates/skills/` with the full command reference and workflow steps.
- If a command doesn't fit an existing skill, consider whether it needs a new skill or is simple enough to be discovered via `ghaiwpy <command> --help`.

#### Pointer Placement & Precedence

The workflow pointer in `AGENTS.md` is strictly secondary to the project's own documentation.
- **Placement**: Append to the end of `AGENTS.md` (or intelligent insertion after existing sections), but **never** force it to the top. The project's own context and rules must come first.
- **Style**: Avoid overly aggressive alerts (e.g., `[!IMPORTANT]`) in the pointer itself, as it can distract from project-specific high-priority rules.
- **Precedence**: If a project's `AGENTS.md` defines rules that conflict with the workflow skill, the project's rules win. The workflow skill handles the *mechanics* of ghaiw; the project handles the *policy*.

#### Pointer Marker System

The AGENTS.md workflow pointer uses HTML comment markers to enable robust detection and refresh:

```
<!-- ghaiw:pointer:start -->
## Git Workflow
...
<!-- ghaiw:pointer:end -->
```

**Functions in `skills/pointer.py`:**
- `has_pointer(file_path)` — Checks if file has marker-delimited pointer block
- `extract_pointer_content(file_path)` — Extracts text between markers (for staleness comparison)
- `remove_pointer(file_path)` — Removes marker-wrapped block; falls back to old-style `## Git Workflow` section removal for backward compatibility
- `write_pointer(file_path)` — Appends marker-wrapped pointer to file
- `ensure_pointer(project_root)` — High-level: find AGENTS.md/CLAUDE.md, detect staleness, refresh if needed

**`ensure_pointer()` logic:**
- Markers present -> Extract inner content and compare to current template
  - Match -> No-op (already current)
  - Different -> Remove old block and write new one (refresh)
- Old-style (no markers) -> Remove via line-based fallback and write with markers (migrate)
- Not present -> Create new file or append (new install)

**`remove_pointer()` removal:**
- Uses marker-based detection (primary), old-style `## Git Workflow` section scanning (fallback)
- Deletes file if it would be empty after removal
- Gracefully preserves project content in AGENTS.md

### Naming Conventions

- **Modules**: `snake_case.py` — one module per concern (e.g., `task_service.py`, `work_service.py`)
- **Classes**: `PascalCase` — Pydantic models (`ProjectConfig`, `SyncResult`), ABCs (`AbstractAITool`, `AbstractTaskProvider`), adapters (`ClaudeAdapter`, `GitHubProvider`)
- **Functions**: `snake_case` — public API functions are unadorned, private helpers use `_` prefix
- **Constants**: `UPPER_SNAKE_CASE` — module-level constants (`MARKER_START`, `CONFIG_FILENAME`)
- **Enums**: `StrEnum` for string-valued enums (`AIToolID`, `MergeStrategy`, `ProviderID`, `ModelTier`)
- **CLI commands**: Match the Bash ghaiw commands — `ghaiwpy task plan`, `ghaiwpy work start`, etc.

### CLI Flag Reference

Key flags added for Bash parity:

**`ghaiwpy work start`:**
- `--detach` — Launch AI in a new terminal tab/window (non-blocking). Uses `build_launch_command()` + `launch_in_new_terminal()`.
- `--cd` — Create worktree, print its path to stdout, and exit (no AI launch). Used internally by `ghaiwpy work cd`.

**`ghaiwpy work done`:**
- `target` (positional) — Optional issue number, worktree name, or plan file path. If a file path, creates the issue first. If a number/name, finds the worktree. If omitted, detects from current branch.
- `--no-cleanup` — Keep the worktree after PR creation / direct merge.

**`ghaiwpy work batch`:**
- `--model` — Pass a specific AI model to all parallel sessions.

**`ghaiwpy work remove`:**
- `--all` — Hidden alias for `--stale` (removes all stale worktrees).

**`ghaiwpy update`:**
- `--skip-self-upgrade` — Skip the source-version self-upgrade check.

**`ghaiwpy task create`:**
- No flags required — when `--plan-file` is omitted, prompts interactively for title and body.

**`ghaiwpy shell-init`:**
- No flags. Outputs a shell function for `eval "$(ghaiwpy shell-init)"`.

### Adding a New Subcommand to `task`

The task CLI is in `src/ghaiw/cli/task.py`, business logic in `src/ghaiw/services/task_service.py`. When adding a new subcommand:

1. **Implement the service method** — Add the business logic in `task_service.py` (or a new service if warranted)
2. **Wire the CLI** — Add a Typer command function in `cli/task.py` with appropriate options/arguments
3. **Add models** — If the subcommand introduces new data types, add them to `models/task.py`
4. **Update help** — Typer generates help automatically from docstrings and `help=` parameters
5. **Update docs** — README.md (user-facing), skill files (agent-facing)

### Adding a New AI Tool

Thanks to `__init_subclass__` auto-registration, adding a new AI tool requires only one file:

1. Create `src/ghaiw/ai_tools/<tool_name>.py`
2. Define a class that inherits from `AbstractAITool` and sets `TOOL_ID`
3. Implement the required abstract methods: `capabilities()`, `get_models()`, `launch()`, `parse_transcript()`
4. Optionally override `is_model_compatible()`, `plan_mode_args()`, `normalize_model_format()`
5. Add the tool ID to `AIToolID` enum in `models/ai.py`
6. Import the module in `ai_tools/__init__.py` to trigger registration

No modification to `base.py`, services, or CLI is needed.

## Dependencies

- **Python** 3.11+ (uses `StrEnum`, `|` union syntax, `from __future__ import annotations`)
- **git** 2.20+ (worktree commands)
- **gh CLI** — must be authenticated; needs `project` scope for board moves
- **uv** — recommended for development (manages virtualenv and dependencies)

### Python Package Dependencies

Runtime:
- `typer>=0.12` — CLI framework
- `pydantic>=2.0` — Data validation and settings
- `pydantic-settings>=2.0` — Env var overrides
- `sqlmodel>=0.0.16` — SQLite ORM (SQLAlchemy + Pydantic)
- `pyyaml>=6.0` — YAML config parsing
- `rich>=13.0` — Terminal UI (tables, prompts, panels)
- `structlog>=24.0` — Structured logging

Dev:
- `pytest>=8.0` — Test framework
- `pytest-cov>=5.0` — Coverage reporting
- `mypy>=1.10` — Static type checking (strict mode)
- `ruff>=0.4` — Linting and formatting
- `pre-commit>=3.7` — Git hook management
- `types-PyYAML>=6.0` — Type stubs for PyYAML

## Design Principles

### Determinism via Services

All deterministic operations — git commands, state transitions, file manipulation, API calls — **must live in service/utility code**, never in AI agent reasoning. Agents are non-deterministic; code is deterministic. The boundary is:

- **Code decides and executes** — fetch, merge, branch creation, worktree lifecycle, issue state changes. These are codified in `services/`, `git/`, `providers/` and exposed via `ghaiwpy <command>`.
- **Agents interpret and decide next steps** — reading conflict diffs, choosing resolution strategies, composing commit messages, deciding whether to proceed. These are guided by skills.

When adding new functionality, ask: "Can an AI agent get this wrong by reasoning about it?" If yes, put it in code. Examples:

| Deterministic (code) | Non-deterministic (agent) |
|------------------------|---------------------------|
| `git merge main --no-edit` | Resolving merge conflicts |
| Checking if worktree is clean | Deciding what to commit |
| Creating branch with naming convention | Writing commit messages |
| Emitting structured JSON events | Interpreting event output |

This is why `ghaiwpy work sync` exists as a CLI command rather than instructions for agents to run raw git commands — the sequence (preflight -> fetch -> merge -> conflict detection -> event emission) is deterministic and must not vary between agent sessions.

When ghaiw installs skills into a target project (`ghaiwpy init`), the skills reference `ghaiwpy <command>` — they do **not** bundle standalone copies of the logic. The ghaiw CLI is the single source of truth for deterministic operations.

## Testing

ghaiw-py uses **pytest** exclusively for all test suites.

### Test Organization

```
tests/
├── conftest.py              # Shared fixtures (tmp_git_repo, tmp_ghaiw_project, mock_gh)
├── test_cli_basics.py       # Basic CLI command tests (help, version)
├── unit/                    # Pure logic, no subprocess, no git
│   ├── test_models/         # Pydantic model tests
│   ├── test_services/       # Service logic tests (mocked providers/git)
│   ├── test_config/         # Config parsing tests
│   ├── test_transcript/     # Transcript parsing tests
│   └── test_utils/          # Utility function tests
├── integration/             # Needs git repos, mock gh
│   ├── test_check.py
│   ├── test_init.py
│   ├── test_work_lifecycle.py
│   └── test_skill_install.py
├── e2e/                     # End-to-end smoke tests
│   └── ...
├── live/                    # Needs gh auth + network (gated by RUN_LIVE_GH_TESTS=1)
│   └── test_gh_integration.py
└── fixtures/                # Static test data files
    └── ...
```

### Running Tests

```bash
# All tests (excluding live)
uv run pytest tests/ -v --ignore=tests/live

# Unit tests only (fast, no git/subprocess)
uv run pytest tests/unit/ -v

# Integration tests only (needs git, uses mock gh)
uv run pytest tests/integration/ -v

# Specific test file
uv run pytest tests/unit/test_services/test_work_done_sync.py -v

# Run tests matching a pattern
uv run pytest tests/ -v -k "test_check"

# With coverage
uv run pytest tests/ --cov=ghaiw --cov-report=term-missing

# Live GitHub tests (requires real gh auth)
RUN_LIVE_GH_TESTS=1 uv run pytest tests/live/ -v
```

### Test Fixtures

Key fixtures in `tests/conftest.py`:

| Fixture | Description |
|---------|-------------|
| `tmp_git_repo` | Fresh git repo with initial commit and `main` branch |
| `tmp_ghaiw_project` | Git repo with `.ghaiw.yml` config file (extends `tmp_git_repo`) |
| `monkeypatch_env` | Clears all `GHAIW_*` env vars to prevent test runner leakage |
| `mock_gh` | Creates a mock `gh` CLI binary that logs invocations; returns path to log file |

### Writing New Tests

New tests belong in the appropriate subdirectory. Use existing test files as reference.

**Basic test skeleton:**
```python
"""Tests for feature X."""

from pathlib import Path
import pytest

def test_feature_does_something(tmp_ghaiw_project: Path) -> None:
    """Feature X should produce expected output."""
    # Arrange
    config_path = tmp_ghaiw_project / ".ghaiw.yml"

    # Act
    result = some_function(config_path)

    # Assert
    assert result.status == "success"
    assert "expected text" in result.output
```

**Testing with mock gh CLI:**
```python
def test_issue_creation(tmp_ghaiw_project: Path, mock_gh: Path) -> None:
    """Creating an issue should invoke gh CLI correctly."""
    result = create_issue(tmp_ghaiw_project, title="Test issue")

    assert result.number == 1
    # Verify gh was called with expected args
    invocations = mock_gh.read_text()
    assert "issue create" in invocations
```

### Test Quality Rules

- Every bug fix must include a regression test that would fail before the fix.
- Use mocks for `gh` CLI and AI tool subprocess calls in unit/integration tests.
- For features that interact with GitHub, include at least one real `gh` integration test in `tests/live/`; mocks alone are not enough.
- Gate live GitHub tests with `RUN_LIVE_GH_TESTS=1` and skip explicitly when prerequisites are missing.
- Prefer exact assertions over loose substring checks: verify outputs, side effects, and absence of wrong output.
- For `--json` modes, parse stdout as JSON and fail if any non-JSON line appears.
- Pure functions (parsing, formatting, model validation) can and should be tested without mocks.

**When to skip re-running tests after `ghaiwpy work sync`:** If the sync merge only brings in changes to documentation or template files (`templates/`, `docs/`, `README.md`, `AGENTS.md`, `CHANGELOG.md`), there is no need to re-run tests. Re-run tests after sync when the merged changes touch `src/`, `scripts/`, or `tests/`.

## Version Bumping

Version lives in `src/ghaiw/__init__.py` (`__version__`) and `pyproject.toml` (`version`). Use `scripts/auto_version.py` to bump it:

```bash
python scripts/auto_version.py patch           # bug fixes, docs (0.1.0 -> 0.1.1)
python scripts/auto_version.py minor           # new features, flags (0.1.0 -> 0.2.0)
python scripts/auto_version.py major           # breaking changes (0.1.0 -> 1.0.0)
python scripts/auto_version.py minor --dry-run # preview only
```

The script updates both files, generates `CHANGELOG.md`, commits, and creates an annotated git tag.

### Changelog Generation

`scripts/changelog.py` generates `CHANGELOG.md` from the full git history. It groups commits by conventional-commit type (Features, Bug Fixes, etc.) under version-tagged sections. It runs automatically as part of `auto_version.py`, or standalone:

```bash
python scripts/changelog.py               # write CHANGELOG.md
python scripts/changelog.py --stdout      # print to stdout
python scripts/changelog.py --tag v1.0.0  # label unreleased as v1.0.0
```

### Semver Rules

- **patch** — bug fixes, documentation, refactors with no behavior change
- **minor** — new features, new commands, new flags (backward compatible)
- **major** — breaking changes: removed commands, renamed flags, changed output format

## Commit Conventions

Use [Conventional Commits](https://www.conventionalcommits.org/) format:
- `feat:` — new features -> minor bump
- `fix:` — bug fixes -> patch bump
- `docs:` — documentation -> patch bump
- `refactor:` — code restructuring -> patch bump
- `test:` — test changes -> patch bump
- `chore:` — maintenance -> patch bump
- Breaking changes (`feat!:` or `BREAKING CHANGE`) -> major bump

## Documentation Rules

Every change **must** include documentation updates as part of the implementation — not as a follow-up. Before finalizing any work:

1. **`AGENTS.md`** — Update if the change affects architecture, commands, conventions, design principles, or development workflow.
2. **`README.md`** — Update if the change affects user-facing behavior: new commands, flags, install steps, configuration options, or supported tools.
3. **`templates/skills/workflow/SKILL.md`** — *(inited-project artifact)* Update if the change affects always-on agent session rules that get installed into inited projects (worktree safety, commit workflow, planning lifecycle, sync mandate).
4. **`templates/skills/`** (task, deps, sync, pr-summary) — *(inited-project artifacts)* Update if the change affects how AI agents in inited projects should use ghaiw commands. This is where command references, flags, workflows, and examples belong.
5. **`templates/agents-pointer.md`** — *(inited-project artifact)* The pointer text that `ghaiwpy init` injects into target projects' `AGENTS.md`. Update this when the critical inline rules or pointer wording changes. **This is not the same as this repo's own `## Git Workflow` section** — that is the self-installed copy, written once by `ghaiwpy init` and never overwritten by `ghaiwpy update`.

Do not skip documentation even for "small" changes — a new flag, a renamed option, or a changed default all need docs updates. Documentation is part of "done", not a separate task.

### Change Checklist

Before considering any work complete, verify each item:

- [ ] **Code** — implementation is done and tests pass (`uv run pytest tests/ -v --ignore=tests/live`)
- [ ] **Types** — `uv run mypy src/ --strict` passes with no errors
- [ ] **Lint** — `uv run ruff check src/` and `uv run ruff format --check src/` pass
- [ ] **`AGENTS.md`** — updated if architecture, conventions, design principles, or workflow changed
- [ ] **`README.md`** — updated if user-facing behavior changed (commands, flags, config, install)
- [ ] **`templates/skills/workflow/SKILL.md`** — updated if always-on agent session rules changed
- [ ] **`templates/agents-pointer.md`** — updated if the critical inline rules or pointer wording changed
- [ ] **`templates/skills/`** (task, deps, sync) — updated if agent-facing command workflows, flags, or examples changed
- [ ] **Progressive disclosure** *(inited-project artifacts)* — session rules go in workflow skill, task workflows go in task skills, AGENTS.md pointer stays minimal
- [ ] **Commit** — uses conventional-commit prefix for correct auto-versioning

### Documentation Feedback Loop

After completing any task, reflect on whether you encountered any friction during the session — ambiguity, missing context, misleading guidance, unexpected behavior, or anything that required more investigation than it should have. **Friction is a signal.** Act on it before closing the session.

**At the end of every session, ask yourself:**

1. **Did I hit a bug or unexpected behavior?**
   - Describe it to the user and **offer** to create a GitHub Issue (`ghaiwpy task create`)
   - Do not silently work around it — surface it so it can be tracked and fixed

2. **Did I hit a doc gap or architecture misunderstanding?**
   - Was something in `AGENTS.md` missing, wrong, or misleading?
   - Did I have to dig into source files to answer a question that should have been documented?
   - **Offer** to update `AGENTS.md` (or the relevant skill) to prevent future agents from hitting the same gap

3. **Did I have to investigate more than expected?**
   - Did I look at files, run commands, or make assumptions to fill in missing context?
   - If so, that context belongs in the docs — **offer** to add it

**The goal: every friction point should happen at most once.** If a future agent would hit the same issue, the documentation has failed.

Always **offer** — don't create issues or edit docs without the user's confirmation. But never skip the reflection.

### Correction-Driven Documentation

When the user corrects you — changes your approach, points out a wrong assumption, or redirects your reasoning — that correction reveals a gap in the documentation. **Do not just fix the immediate task.** Also, **in the same response where you acknowledge the correction**:

1. **Identify the root cause** — What documentation was missing, ambiguous, or misleading that led to the wrong approach? If a rule already existed but you rationalized around it, the rule's wording is the gap. Ask: was the existing wording precise enough to prevent the rationalization? If not, that is a documentation failure, not just an adherence failure.
2. **Propose a documentation update** — Suggest adding or tightening the rule in the appropriate file (`AGENTS.md`, a skill, or `templates/agents-pointer.md`). Do this in the same response as the acknowledgement — not as a follow-up, not after the user asks.
3. **Apply the update** — If the user agrees, add the rule immediately as part of the current task. Documentation updates from corrections are not follow-ups.

The goal: every correction should happen **at most once**. If a future agent would make the same mistake, the documentation has failed.

<!-- ghaiw:pointer:start -->
## Git Workflow

**First action every session** — read @.claude/skills/workflow/SKILL.md for
full rules. The critical rules you must always follow:

1. Run `ghaiwpy check` first — **never edit source files in the main checkout, even before committing**; only planning operations (creating issues, writing plan files to `/tmp`) are allowed from main
2. Never create PRs manually (`gh pr create`) or push branches directly. `ghaiwpy work done` is the only way to finalize work — if it fails, debug and fix the error; do NOT bypass it
3. To finalize work: `ghaiwpy work sync` then `ghaiwpy work done`
4. Never create GitHub Issues via `gh issue create` — use `ghaiwpy task create` or read @.claude/skills/task/SKILL.md
<!-- ghaiw:pointer:end -->
