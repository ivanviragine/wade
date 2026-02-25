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

## Quick Reference

### Commands

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

### Architecture at a Glance

```
CLI Layer      ->  can import: services, models, config, logging, ui
Service Layer  ->  can import: providers, ai_tools, git, db, models, config, logging
Provider Layer ->  can import: models, config, logging  (NO service imports)
AI Tool Layer  ->  can import: models, config, logging  (NO service imports)
Git Layer      ->  can import: models, config, logging  (NO service imports)
DB Layer       ->  can import: models, logging           (NO config imports)
Models Layer   ->  can import: nothing (leaf dependency)
```

No circular dependencies. Models are pure data. Services orchestrate. Never import a higher layer from a lower layer.

For detailed architecture reference including the config system, migration pipeline, AI interaction patterns, and CLI flag reference, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Development Rules

### Naming Conventions

- **Modules**: `snake_case.py` — one module per concern (e.g., `task_service.py`, `work_service.py`)
- **Classes**: `PascalCase` — Pydantic models (`ProjectConfig`, `SyncResult`), ABCs (`AbstractAITool`, `AbstractTaskProvider`), adapters (`ClaudeAdapter`, `GitHubProvider`)
- **Functions**: `snake_case` — public API functions are unadorned, private helpers use `_` prefix
- **Constants**: `UPPER_SNAKE_CASE` — module-level constants (`MARKER_START`, `CONFIG_FILENAME`)
- **Enums**: `StrEnum` for string-valued enums (`AIToolID`, `MergeStrategy`, `ProviderID`, `ModelTier`)
- **CLI commands**: Match the Bash ghaiw commands — `ghaiwpy task plan`, `ghaiwpy work start`, etc.

### Commit Conventions

Use [Conventional Commits](https://www.conventionalcommits.org/) format:
- `feat:` — new features -> minor bump
- `fix:` — bug fixes -> patch bump
- `docs:` — documentation -> patch bump
- `refactor:` — code restructuring -> patch bump
- `test:` — test changes -> patch bump
- `chore:` — maintenance -> patch bump
- Breaking changes (`feat!:` or `BREAKING CHANGE`) -> major bump

### Documentation Rules

Every change **must** include documentation updates as part of the implementation — not as a follow-up. Before finalizing any work:

1. **`AGENTS.md`** — Update if the change affects architecture, commands, conventions, design principles, or development workflow.
2. **`README.md`** — Update if the change affects user-facing behavior: new commands, flags, install steps, configuration options, or supported tools.
3. **`templates/skills/workflow/SKILL.md`** — *(inited-project artifact)* Update if the change affects always-on agent session rules that get installed into inited projects (worktree safety, commit workflow, planning lifecycle, sync mandate).
4. **`templates/skills/`** (task, deps, sync, pr-summary) — *(inited-project artifacts)* Update if the change affects how AI agents in inited projects should use ghaiw commands. This is where command references, flags, workflows, and examples belong.
5. **`templates/agents-pointer.md`** — *(inited-project artifact)* The pointer text that `ghaiwpy init` injects into target projects' `AGENTS.md`. Update this when the critical inline rules or pointer wording changes. **This is not the same as this repo's own `## Git Workflow` section** — that is the self-installed copy, written once by `ghaiwpy init` and never overwritten by `ghaiwpy update`.

Do not skip documentation even for "small" changes — a new flag, a renamed option, or a changed default all need docs updates. Documentation is part of "done", not a separate task.

#### Documentation Feedback Loop

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

#### Correction-Driven Documentation

When the user corrects you — changes your approach, points out a wrong assumption, or redirects your reasoning — that correction reveals a gap in the documentation. **Do not just fix the immediate task.** Also, **in the same response where you acknowledge the correction**:

1. **Identify the root cause** — What documentation was missing, ambiguous, or misleading that led to the wrong approach? If a rule already existed but you rationalized around it, the rule's wording is the gap. Ask: was the existing wording precise enough to prevent the rationalization? If not, that is a documentation failure, not just an adherence failure.
2. **Propose a documentation update** — Suggest adding or tightening the rule in the appropriate file (`AGENTS.md`, a skill, or `templates/agents-pointer.md`). Do this in the same response as the acknowledgement — not as a follow-up, not after the user asks.
3. **Apply the update** — If the user agrees, add the rule immediately as part of the current task. Documentation updates from corrections are not follow-ups.

The goal: every correction should happen **at most once**. If a future agent would make the same mistake, the documentation has failed.

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

### Testing

ghaiw-py uses **pytest** exclusively for all test suites.

#### Test Organization

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

#### Running Tests

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

#### Test Fixtures

Key fixtures in `tests/conftest.py`:

| Fixture | Description |
|---------|-------------|
| `tmp_git_repo` | Fresh git repo with initial commit and `main` branch |
| `tmp_ghaiw_project` | Git repo with `.ghaiw.yml` config file (extends `tmp_git_repo`) |
| `monkeypatch_env` | Clears all `GHAIW_*` env vars to prevent test runner leakage |
| `mock_gh` | Creates a mock `gh` CLI binary that logs invocations; returns path to log file |

#### Writing New Tests

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

#### Test Quality Rules

- Every bug fix must include a regression test that would fail before the fix.
- Use mocks for `gh` CLI and AI tool subprocess calls in unit/integration tests.
- For features that interact with GitHub, include at least one real `gh` integration test in `tests/live/`; mocks alone are not enough.
- Gate live GitHub tests with `RUN_LIVE_GH_TESTS=1` and skip explicitly when prerequisites are missing.
- Prefer exact assertions over loose substring checks: verify outputs, side effects, and absence of wrong output.
- For `--json` modes, parse stdout as JSON and fail if any non-JSON line appears.
- Pure functions (parsing, formatting, model validation) can and should be tested without mocks.

**When to skip re-running tests after `ghaiwpy work sync`:** If the sync merge only brings in changes to documentation or template files (`templates/`, `docs/`, `README.md`, `AGENTS.md`, `CHANGELOG.md`), there is no need to re-run tests. Re-run tests after sync when the merged changes touch `src/`, `scripts/`, or `tests/`.

### Version Bumping

Version lives in `src/ghaiw/__init__.py` (`__version__`) and `pyproject.toml` (`version`). Use `scripts/auto_version.py` to bump it:

```bash
# Version bump
python scripts/auto_version.py patch           # bug fixes, docs (0.1.0 -> 0.1.1)
python scripts/auto_version.py minor           # new features, flags (0.1.0 -> 0.2.0)
python scripts/auto_version.py major           # breaking changes (0.1.0 -> 1.0.0)
python scripts/auto_version.py minor --dry-run # preview only
```

The script updates both files, generates `CHANGELOG.md`, commits, and creates an annotated git tag.

#### Changelog Generation

`scripts/changelog.py` generates `CHANGELOG.md` from the full git history. It groups commits by conventional-commit type (Features, Bug Fixes, etc.) under version-tagged sections. It runs automatically as part of `auto_version.py`, or standalone:

```bash
python scripts/changelog.py                   # write CHANGELOG.md
python scripts/changelog.py --stdout          # print to stdout
python scripts/changelog.py --tag v1.0.0      # label unreleased as v1.0.0
```

#### Semver Rules

- **patch** — bug fixes, documentation, refactors with no behavior change
- **minor** — new features, new commands, new flags (backward compatible)
- **major** — breaking changes: removed commands, renamed flags, changed output format

## Extension Points

### Adding a New AI Tool

Thanks to `__init_subclass__` auto-registration, adding a new AI tool requires only one file:

1. Create `src/ghaiw/ai_tools/<tool_name>.py`
2. Define a class that inherits from `AbstractAITool` and sets `TOOL_ID`
3. Implement the required abstract methods: `capabilities()`, `get_models()`, `launch()`, `parse_transcript()`
4. Optionally override `is_model_compatible()`, `plan_mode_args()`, `normalize_model_format()`
5. Add the tool ID to `AIToolID` enum in `models/ai.py`
6. Import the module in `ai_tools/__init__.py` to trigger registration

No modification to `base.py`, services, or CLI is needed.

### Adding a New Provider

`AbstractTaskProvider` ABC in `providers/base.py` defines the interface. To add a new provider:
1. Create `src/ghaiw/providers/<name>.py`
2. Implement all abstract methods: `list_tasks()`, `create_task()`, `read_task()`, `update_task()`, `close_task()`, `comment_on_task()`, `ensure_label()`, `add_label()`, `remove_label()`, `snapshot_task_numbers()`
3. Add the provider ID to `ProviderID` enum in `models/ai.py`
4. Register in `providers/registry.py`
Currently only `GitHubProvider` exists (wraps `gh` CLI via subprocess).

### Adding a New Subcommand to `task`

The task CLI is in `src/ghaiw/cli/task.py`, business logic in `src/ghaiw/services/task_service.py`. When adding a new subcommand:

1. **Implement the service method** — Add the business logic in `task_service.py` (or a new service if warranted)
2. **Wire the CLI** — Add a Typer command function in `cli/task.py` with appropriate options/arguments
3. **Add models** — If the subcommand introduces new data types, add them to `models/task.py`
4. **Update help** — Typer generates help automatically from docstrings and `help=` parameters
5. **Update docs** — README.md (user-facing), skill files (agent-facing)

### Adding a New Skill

`ghaiwpy init` installs skills file-by-file via the `skills/installer.py` module. When adding a new skill:

1. Create the skill template in `templates/skills/<name>/SKILL.md`
2. Register the skill in `skills/installer.py` — add it to the install, self-init (symlink), and update paths
3. Add the skill directory to the cleanup logic in `init_service.py` (deinit path)
4. Add the skill reference to `templates/skills/workflow/SKILL.md`

The self-init path creates symlinks from `.claude/skills/<name>` -> `../../templates/skills/<name>` to avoid file duplication when working on ghaiw-py itself.

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

## Complete Workflow Flows

These diagrams show the end-to-end flow for each major operation. They serve as the definitive reference for how commands orchestrate services, git operations, and external tools.

### Flow: ghaiwpy init
```
User runs ghaiwpy init
  │
  ├─ Validate: is git repo?
  ├─ Detect/select AI tool (--ai flag or interactive prompt)
  ├─ Probe available models (CLI probing → web scrape → defaults)
  ├─ Collect project settings (main branch, labels, worktrees dir, merge strategy)
  ├─ Review model-to-complexity mapping (interactive)
  ├─ Prompt per-command AI tool overrides
  ├─ Generate .ghaiw.yml (v2 format)
  ├─ Install skill files → .claude/skills/, .github/skills/, .agents/skills/, .gemini/skills/
  │   ├─ Self-init: symlinks to templates/skills/
  │   └─ Normal init: file copies
  ├─ Update .gitignore (add .ghaiw/ entries)
  ├─ Write AGENTS.md pointer (marker-delimited block)
  ├─ Configure Claude Code allowlist (.claude/settings.json)
  └─ Write .ghaiw-managed manifest
```

### Flow: ghaiwpy update
```
ghaiwpy update performs 12 steps:
  │
  1. Validate repo + config existence
  2. Self-upgrade if source version differs (frozen venv only)
  3. Read old version from manifest
  4. Show version transition message
  5. Run config migration pipeline (7 migrations)
  6. Reload config + backfill probed models
  7. Refresh skill files (force overwrite)
  8. Clean legacy artifacts (old skill dirs, old files)
  9. Configure Claude Code allowlist
  10. Configure Gemini experimental (if applicable)
  11. Refresh .gitignore + AGENTS.md pointer
  12. Rebuild manifest with new version
```

Self-upgrade mechanism: When installed via install.sh (frozen venv at ~/.local/share/ghaiw/venv/), the installer records the source repo path in ghaiw-source.txt. On update, if versions differ, utils/install.py:self_upgrade() reinstalls and re_exec() restarts. Editable installs skip this. Pass --skip-self-upgrade to bypass.

### Flow: ghaiwpy task plan
```
User runs ghaiwpy task plan [--ai claude] [--model claude-opus-4]
  │
  ├─ Select AI tool (flag → config → prompt)
  ├─ Create temp directory for plan files
  ├─ Snapshot current issue numbers (for Path A detection)
  ├─ Load plan prompt template → render with plan_dir
  ├─ Copy prompt to clipboard
  ├─ Launch AI CLI in plan mode with plan-dir args
  │   AI agent creates issues via ghaiwpy task create
  │   OR writes plan .md files to temp dir
  │
  ├─ AI CLI exits
  ├─ Extract token usage from transcript
  │
  ├─ Detection:
  │   ├─ Path A: Compare issue snapshots → detect newly created issues
  │   └─ Path B: Discover plan files in temp dir → create issues from files
  │
  ├─ Finalize issues:
  │   ├─ Apply plan token usage (proportional allocation)
  │   ├─ Add planned-by labels
  │   └─ Auto-dependency analysis (if 2+ issues)
  └─ Clean up temp directory
```

### Flow: ghaiwpy task create
```
A) Interactive mode (no --plan-file):
   ├─ Prompt for title (required)
   ├─ Prompt for body (multi-line, optional)
   ├─ gh issue create --title TITLE --body-file FILE --label LABEL
   └─ Show next-step hint: "ghaiwpy work start <N>"

B) From plan file (--plan-file path.md):
   ├─ Parse markdown: extract # Title, ## Complexity, ## sections
   ├─ Build PlanFile model
   ├─ gh issue create --title TITLE --body-file FILE --label LABEL
   ├─ Optionally add planned-by labels (--ai flag)
   └─ Show next-step hint
```

### Flow: ghaiwpy work start
```
User runs ghaiwpy work start 42 [--ai claude] [--model opus] [--detach] [--cd]
  │
  ├─ Resolve target: issue number or plan file path
  │   └─ If plan file: create issue first, then use its number
  │
  ├─ Read issue from GitHub (gh issue view)
  │
  ├─ Check for existing worktree:
  │   ├─ Exists + stale (PR merged/branch gone): cleanup old → create new
  │   └─ Exists + active: reuse
  │
  ├─ Create worktree (if needed):
  │   ├─ make_branch_name(prefix, issue_number, title)
  │   │   → e.g., feat/42-add-user-auth
  │   ├─ git worktree add -b BRANCH WORKTREE_PATH main
  │   └─ Returns worktree path
  │
  ├─ Bootstrap worktree:
  │   ├─ Copy files from config hooks.copy_to_worktree (e.g., .env)
  │   └─ Run post_worktree_create hook (e.g., scripts/setup-worktree.sh)
  │
  ├─ Write context files:
  │   ├─ .issue-context.md (full issue body for AI reference)
  │   └─ PLAN.md (structured plan for AI consumption)
  │
  ├─ Resolve model from complexity mapping:
  │   issue.complexity → config.models.<tool>.<tier> → model ID
  │
  ├─ Build work prompt → copy to clipboard
  │
  ├─ Launch mode:
  │   ├─ --cd: print worktree path to stdout, exit
  │   ├─ --detach: launch_in_new_terminal() (Ghostty/iTerm2/tmux)
  │   └─ Default: launch AI CLI inline (blocks until exit)
  │
  ├─ [After AI exits] Post-session processing:
  │   ├─ Parse transcript → extract token usage
  │   ├─ Update PR body with implementation usage stats
  │   ├─ Add worked-by labels to issue
  │   └─ Add in-progress label
  └─ Return success
```

### Flow: ghaiwpy work sync
```
User runs ghaiwpy work sync [--json] [--dry-run] [--main-branch main]
  │
  ├─ Preflight checks:
  │   ├─ In a git repo?
  │   ├─ Not on main branch?
  │   ├─ Not detached HEAD?
  │   ├─ Worktree is clean?
  │   └─ Main branch exists locally?
  │
  ├─ git fetch origin
  │
  ├─ Count commits behind origin/main
  │   └─ If 0: emit ALREADY_UP_TO_DATE, return
  │
  ├─ If --dry-run: report N commits behind, return
  │
  ├─ git merge --no-edit origin/main
  │   ├─ Success → emit MERGE_OK with commits_merged count
  │   └─ Conflict → emit MERGE_CONFLICT with file list
  │       ├─ git diff --name-only --diff-filter=U → conflict file list
  │       └─ Return SyncResult(success=False, conflicts=[...])
  │
  ├─ Emit structured SyncEvent objects
  └─ Return SyncResult

Exit codes: 0=success, 2=conflicts, 4=preflight failure
```

### Flow: ghaiwpy work done
```
User runs ghaiwpy work done [target] [--draft] [--no-cleanup] [--no-close]
  │
  ├─ Detect branch and issue number:
  │   ├─ From target (issue number, worktree name, or plan file)
  │   ├─ From current branch (extract issue number from branch name)
  │   └─ Or prompt interactively
  │
  ├─ Check worktree is clean
  │
  ├─ Merge Strategy:
  │
  │  ┌── PR Strategy ──────────────────────────────────────┐
  │  │ ├─ Read issue for title/body                        │
  │  │ ├─ git push origin BRANCH                           │
  │  │ ├─ Detect parent tracking issue (checklist search)  │
  │  │ ├─ Build PR body:                                   │
  │  │ │   ├─ "Closes #N"                                  │
  │  │ │   ├─ Plan summary from issue body                 │
  │  │ │   ├─ PR-SUMMARY.md (if exists in worktree)        │
  │  │ │   └─ Token usage stats                            │
  │  │ ├─ gh pr create --title --body --base main --head   │
  │  │ ├─ Remove in-progress label                         │
  │  │ └─ Worktree kept (cleaned up by next work start)    │
  │  └────────────────────────────────────────────────────┘
  │
  │  ┌── Direct Strategy ─────────────────────────────────┐
  │  │ ├─ Sync feature branch with main (fetch + merge)    │
  │  │ ├─ Checkout main                                    │
  │  │ ├─ git merge --no-ff FEATURE_BRANCH                 │
  │  │ ├─ git push origin main                             │
  │  │ ├─ Close issue (gh issue close)                     │
  │  │ └─ Cleanup worktree (unless --no-cleanup)           │
  │  └────────────────────────────────────────────────────┘
  │
  └─ Return success
```

### Flow: ghaiwpy work batch
```
User runs ghaiwpy work batch 10 11 12 [--ai claude] [--model opus]
  │
  ├─ Read all issues
  ├─ Build dependency graph from issue bodies:
  │   ├─ Parse "Depends on: #X" / "Blocks: #Y" from ## Dependencies sections
  │   └─ Construct DependencyGraph
  │
  ├─ Partition issues:
  │   ├─ Independent issues (no dependencies)
  │   └─ Dependency chains (ordered by topological sort)
  │
  ├─ Launch independent issues in parallel:
  │   └─ Each in a new terminal via launch_in_new_terminal()
  │       (Ghostty, iTerm2, tmux, GNOME Terminal)
  │
  ├─ For each dependency chain:
  │   ├─ Launch first issue in chain (new terminal)
  │   └─ Print remaining chain members for sequential execution
  │
  └─ Return success
```

### Flow: ghaiwpy task deps
```
User runs ghaiwpy task deps 10 11 12 [--ai claude]
  │
  ├─ Build context: read all issue titles + bodies
  │
  ├─ AI Analysis (two modes):
  │   ├─ Headless (preferred): tool --print PROMPT → parse stdout
  │   └─ Interactive (fallback): copy prompt → launch AI → read output file
  │
  ├─ Parse edges from AI output:
  │   Format: "10 -> 11 # auth must exist before dashboard"
  │   Validated against provided issue numbers
  │
  ├─ Apply cross-references to issues:
  │   ├─ Strip old ## Dependencies section from each issue body
  │   ├─ Inject new section with "Depends on: #X" and "Blocks: #Y"
  │   └─ gh issue edit N --body-file FILE
  │
  ├─ Create tracking issue (if 2+ issues):
  │   ├─ Title: "Execution Plan: [titles]"
  │   ├─ Body: checklist, Mermaid dependency graph, topological order
  │   └─ gh issue create
  │
  └─ Return DependencyGraph (with topo sort, Mermaid diagram)
```

### Flow: ghaiwpy check
```
User runs ghaiwpy check
  │
  ├─ git rev-parse --git-dir → Is this a git repo?
  │   └─ No → NOT_IN_GIT_REPO (exit 1)
  │
  ├─ Compare git-common-dir vs git-dir → Is this a worktree?
  │   ├─ Yes → IN_WORKTREE (exit 0) — safe to work
  │   └─ No → IN_MAIN_CHECKOUT (exit 2) — unsafe for agent work
  │
  └─ Output: status, toplevel path, branch name, git-dir
```

### Flow: Shell Integration (ghaiwpy work cd)
```
User adds: eval "$(ghaiwpy shell-init)"
  │
  ├─ Installs shell function wrapper that intercepts ghaiwpy work cd
  │
  └─ User runs: ghaiwpy work cd 42
      ├─ Find existing worktree for issue 42
      │   ├─ Found → print path to stdout
      │   └─ Not found → create worktree (work start --cd), print path
      └─ Shell wrapper captures path and runs: cd <path>
```

### Flow: Worktree Staleness Classification
```
classify_staleness(worktree) →
  │
  ├─ Commits ahead of main?
  │   └─ 0 commits → STALE_EMPTY (no work done)
  │
  ├─ PR exists and merged?
  │   └─ Yes → STALE_MERGED (work completed)
  │
  ├─ Remote tracking branch exists?
  │   └─ No → STALE_REMOTE_GONE (branch deleted after merge)
  │
  └─ Default → ACTIVE (work in progress)
```

<!-- ghaiw:pointer:start -->
## Git Workflow

**First action every session** — read @.claude/skills/workflow/SKILL.md for
full rules. The critical rules you must always follow:

1. Run `ghaiwpy check` first — **never edit source files in the main checkout, even before committing**; only planning operations (creating issues, writing plan files to `/tmp`) are allowed from main
2. Never create PRs manually (`gh pr create`) or push branches directly. `ghaiwpy work done` is the only way to finalize work — if it fails, debug and fix the error; do NOT bypass it
3. To finalize work: `ghaiwpy work sync` then `ghaiwpy work done`
4. Never create GitHub Issues via `gh issue create` — use `ghaiwpy task create` or read @.claude/skills/task/SKILL.md
<!-- ghaiw:pointer:end -->
