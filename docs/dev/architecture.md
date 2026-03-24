# Architecture Reference

Detailed architecture documentation for WADE development. For the compact overview, see `AGENTS.md`.

## Commands Reference

```bash
# Install (development)
uv pip install -e ".[dev]"

# Run tests
./scripts/test.sh                                                         # all tests (excludes live)
./scripts/test.sh tests/unit/                                             # unit tests only
./scripts/test.sh tests/integration/                                      # integration tests only
./scripts/test-e2e.sh                                                     # deterministic e2e contract lane
./scripts/test-e2e-docker.sh                                              # deterministic e2e in Docker (CI-equivalent)
RUN_LIVE_GH_TESTS=1 WADE_LIVE_REPO=/path/to/repo ./scripts/test-live-gh.sh # manual live GitHub lane
RUN_LIVE_AI_TESTS=1 ANTHROPIC_API_KEY=... WADE_LIVE_AI_TIMEOUT=45 ./scripts/test-live-ai.sh # manual live AI lane (API-key-backed, not /login session auth)

# Run a single test file
./scripts/test.sh tests/unit/test_config/test_loader.py

# Run tests matching a pattern
./scripts/test.sh -k "test_pattern"

# Type check + lint (both)
./scripts/check.sh
./scripts/check.sh --types    # mypy only
./scripts/check.sh --lint     # ruff only

# Auto-format source
./scripts/fmt.sh

# Version bump
uv run python scripts/auto_version.py patch           # bug fixes, docs (0.1.0 -> 0.1.1)
uv run python scripts/auto_version.py minor           # new features, flags (0.1.0 -> 0.2.0)
uv run python scripts/auto_version.py major           # breaking changes (0.1.0 -> 1.0.0)
uv run python scripts/auto_version.py minor --dry-run # preview only

# Generate changelog
uv run python scripts/changelog.py                   # write CHANGELOG.md
uv run python scripts/changelog.py --stdout          # print to stdout
uv run python scripts/changelog.py --tag v1.0.0      # label unreleased as v1.0.0

# Probe AI CLIs for new/removed models and diff against models.json
./scripts/probe_models.sh
```

## Package Structure

```
src/wade/
├── __init__.py          # __version__
├── __main__.py          # python -m wade
├── cli/                 # Typer commands (thin dispatch)
│   ├── main.py          # Root app + interactive menu, subcommand registration
│   ├── admin.py         # init, update, deinit, check-config, shell-init
│   ├── task.py          # task create/list/read/update/close/deps
│   ├── worktree.py      # worktree list/remove/cd (interactive menu)
│   ├── implementation_session.py  # implementation-session check/sync/done
│   ├── review_pr_comments_session.py # review-pr-comments-session check/sync/done/fetch/resolve
│   ├── review.py        # review plan/implementation/pr-comments/batch
│   ├── plan_session.py  # plan-session done
│   └── autocomplete.py  # Shell autocompletion helpers
├── models/              # Pydantic domain models (pure data, no I/O)
│   ├── config.py        # ProjectConfig, ProjectSettings, AIConfig, ComplexityModelMapping
│   ├── task.py          # Task, PlanFile, Complexity, Label, TaskState
│   ├── session.py       # ImplementationSession, WorktreeState, SyncResult, SyncEvent, MergeStrategy
│   ├── ai.py            # AIToolID, AIModel, ModelTier, TokenUsage, AIToolCapabilities
│   ├── delegation.py    # DelegationRequest, DelegationResult, DelegationMode
│   ├── review.py        # PRReviewStatus, ReviewThread, ReviewComment
│   ├── batch.py         # BatchIssueContext, BatchReviewContext
│   ├── deps.py          # DependencyEdge, DependencyGraph
│   └── events.py        # Typed event models
├── db/                  # SQLite via SQLModel
│   ├── engine.py        # Engine creation, WAL mode
│   ├── tables.py        # SQLModel table definitions
│   └── repositories.py  # Repository classes
├── services/            # Business logic (orchestration)
│   ├── task_service.py  # Task CRUD, plan parsing, labels
│   ├── implementation_service.py  # Implementation session lifecycle
│   ├── plan_service.py  # AI planning sessions
│   ├── review_service.py           # PR review session lifecycle
│   ├── review_delegation_service.py # AI-powered review delegation
│   ├── batch_review_service.py      # Batch issue review
│   ├── deps_service.py  # Dependency analysis
│   ├── smart_start.py   # Smart routing (wade <N> → implement or review)
│   ├── init_service.py  # Project initialization
│   ├── check_service.py # Safety checks, config validation
│   ├── ai_resolution.py # AI tool/model/effort resolution logic
│   ├── delegation_service.py # Delegation mode resolution
│   └── prompt_delivery.py   # Initial message delivery for AI tools
├── providers/           # Task backend providers (ABC + registry)
│   ├── base.py          # AbstractTaskProvider
│   ├── github.py        # GitHubProvider (gh CLI subprocess)
│   ├── clickup.py       # ClickUpProvider (REST API)
│   └── registry.py      # Provider registry (register_provider / get_provider)
├── ai_tools/            # AI tool adapters (ABC, self-registering)
│   ├── base.py          # AbstractAITool with __init_subclass__ registry
│   ├── claude.py        # ClaudeAdapter
│   ├── cursor.py        # CursorAdapter
│   ├── copilot.py       # CopilotAdapter
│   ├── gemini.py        # GeminiAdapter
│   ├── codex.py         # CodexAdapter (OpenAI Codex)
│   ├── opencode.py      # OpenCodeAdapter (multi-provider)
│   ├── antigravity.py   # AntigravityAdapter
│   ├── vscode.py        # VSCodeAdapter (GUI launcher)
│   ├── transcript.py    # Shared transcript parsing
│   └── model_utils.py   # pick_best_for_tier, has_date_suffix
├── git/                 # Git operations (all subprocess)
│   ├── repo.py          # Repo introspection
│   ├── worktree.py      # Worktree create/remove/list
│   ├── branch.py        # Branch naming, creation, deletion
│   ├── sync.py          # Fetch + merge, conflict detection
│   └── pr.py            # PR creation, merge
├── hooks/               # Git/pre-commit hooks
│   └── plan_write_guard.py  # Guard script for plan file write protection
├── skills/              # Skill file management
│   ├── installer.py     # Install/update/remove skill files
│   └── pointer.py       # AGENTS.md pointer insertion/detection
├── config/              # Configuration management
│   ├── loader.py        # Find + parse .wade.yml (walk up from CWD)
│   ├── schema.py        # Re-exports from models (Pydantic Settings)
│   ├── defaults.py      # Hardcoded defaults per AI tool
│   ├── migrations.py    # Config migration pipeline (ensure version key)
│   ├── claude_allowlist.py  # .claude/settings.json allowlist management
│   ├── cursor_allowlist.py  # Cursor settings allowlist management
│   ├── hooks_util.py       # Shared JSON hook config read/merge/write utility
│   ├── cursor_hooks.py     # Cursor settings.json hooks
│   ├── copilot_hooks.py    # Copilot VS Code settings hooks
│   └── gemini_hooks.py     # Gemini configuration hooks
├── ui/                  # Terminal UI (Rich)
│   ├── console.py       # Console class
│   └── prompts.py       # confirm, input, select, menu
├── data/                # Bundled data files
│   └── models.json      # AI model registry (probed from CLIs)
├── logging/             # Structured logging
│   ├── setup.py         # structlog configuration
│   └── context.py       # Session context binding
└── utils/               # Shared utilities
    ├── clipboard.py     # Cross-platform clipboard
    ├── terminal.py      # Tab title, TTY detection, launch_in_new_terminal
    ├── slug.py          # Title -> URL-safe slug
    ├── markdown.py      # Plan file parsing
    ├── process.py       # Subprocess helpers
    ├── http.py          # HTTPClient for REST API providers
    ├── update_check.py  # Version checking, self-upgrade hints
    └── install.py       # Self-upgrade helpers (venv/source detection, re-exec)
```

## Command Dispatch

`src/wade/cli/main.py` is the root Typer application. It registers subcommand groups (`task`, `worktree`, `plan-session`, `implementation-session`, `review-pr-comments-session`, `review`) and admin commands (`init`, `update`, `deinit`, `check-config`, `shell-init`). The `tasks` alias is registered as a hidden Typer group pointing to the same `task_app`. The `wade` entry point (defined in `pyproject.toml` as `wade.cli.main:cli_main`) invokes the root app.

CLI modules are **thin dispatch layers** — they parse flags via Typer, then call service methods. Business logic lives in `services/`, not in `cli/`.

**Interactive menus**: `wade task` and `wade worktree` with no subcommand show interactive menus. `wade task create` prompts interactively for title and body. Top-level commands `plan`, `implement`, `implement-batch`, and `cd` are registered directly on the root app. The `review` subcommand group provides `plan`, `implementation`, `pr-comments`, and `batch` commands. Hidden short aliases `p`, `i`, and `r` map to `plan`, `implement`, and `review pr-comments` respectively. The numeric shorthand `wade <N>` is rewritten to the hidden `smart-start` command in `cli_main()`, which detects PR state and routes to implement or review pr-comments.

**Shell integration**: `wade shell-init` outputs a shell function wrapper for `eval "$(wade shell-init)"` that intercepts `wade cd <n>` and `wade worktree cd <n>` to perform a real `cd` in the caller's shell.

## Config System

`config/loader.py` walks up from CWD to find `.wade.yml` and parses it via PyYAML into a `ProjectConfig` Pydantic model. The v2 config format has nested sections:

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
  implement:
    tool: claude
models:
  claude:
    easy: claude-haiku-4.5
    medium: claude-haiku-4.5
    complex: claude-sonnet-4.6
    very_complex: claude-opus-4.6
provider:
  name: github
hooks:
  post_worktree_create: scripts/setup-worktree.sh
  copy_to_worktree:
    - .env
knowledge:
  enabled: true
  path: KNOWLEDGE.md
```

**Model complexity mapping**: The `models` section maps AI tool names to complexity-tiered model IDs (`easy`, `medium`, `complex`, `very_complex`). When `wade implement` is invoked, the service reads the `complexity:X` label from the issue (falling back to `## Complexity` in the body), maps it to the appropriate configured model, and passes it as `--model` to the AI tool — unless the user explicitly passed `--model` themselves.

**Per-command AI tool and model overrides**: The `ai` section supports `plan`, `deps`, `implement`, `review_plan`, `review_implementation`, and `review_batch` sub-sections, with optional `tool`, `model`, `mode`, `effort`, `enabled`, `yolo`, and `timeout` keys as applicable. The fallback chain is: CLI `--ai`/`--model` flag -> command-specific config -> global `default_tool`. This is implemented in `ProjectConfig.get_ai_tool(command)` and `ProjectConfig.get_model(command)`. When `mode` is omitted, `review_plan` and `review_implementation` default to `prompt`, while `review_batch` defaults to `interactive`.

**Worktree hooks**: The `hooks` section lets projects run setup automatically when a worktree is created. `post_worktree_create` points to a script that runs in the new worktree (e.g., installing dependencies). `copy_to_worktree` lists files to copy from the project root into the worktree before the hook runs (e.g., `.env`). Hook failures are non-fatal — a warning is logged and the session continues.

**Project knowledge**: The optional `knowledge` section enables a project knowledge file for cross-session AI learning. `wade init` can create the file and add it, plus its `.ratings.yml` sidecar, to `hooks.copy_to_worktree` so sessions can read and update the same relative path from worktrees. `wade knowledge add` appends learnings to that file, `wade knowledge get` prints its current contents, and `wade knowledge rate` records thumbs-up/thumbs-down feedback per entry in the sidecar file. The path must stay inside the project root.

## Config Migration Pipeline

`config/migrations.py` provides a single migration run during `wade update`:

| # | Function | What it does |
|---|----------|-------------|
| 1 | `ensure_version(raw)` | Set `version: 2` if missing |

`run_all_migrations(config_path)` loads YAML, runs the migration, writes back only if changed. Returns `True` if the file was modified.

## Update Flow

`wade update` performs 11 steps:

1. Self-upgrade if source version differs (see below)
2. Validate repo + config existence
3. Read old version from manifest
4. Show version transition message
5. Run config migration pipeline
6. Reload config + backfill probed models
7. Refresh skill files
8. Configure AI tool allowlists (Claude via `claude_allowlist.py`, Cursor via `cursor_allowlist.py`)
9. Configure Gemini experimental settings (if applicable)
10. Refresh .gitignore + AGENTS.md pointer
11. Rebuild manifest with version

**Self-upgrade mechanism**: `utils/install.py:detect_install_method()` inspects `sys.executable` to determine how wade was installed (`uv-tool`, `pipx`, `brew`, or `editable`). On `wade update`, `self_upgrade()` runs the appropriate package manager command (e.g. `uv tool upgrade wade`), then `re_exec()` replaces the current process via `os.execv()` so the new code is loaded. Editable installs skip this naturally. Pass `--skip-self-upgrade` to bypass.

## AI Interaction Pattern

All AI-interactive commands follow the same pattern:

1. **Tool selection** — If no `--ai` flag is given, use the tool from config (via `ProjectConfig.get_ai_tool()`). If that's empty, prompt the user interactively via `ui/prompts.py`.
2. **Initial prompt** — Build the starter prompt and display it in a console panel. It is passed directly to the AI tool as an initial message on launch (no clipboard involved).
3. **Launch AI CLI** — Execute the AI tool binary via `AbstractAITool.launch()`. The tool runs interactively in the terminal with the prompt pre-filled.
4. **Post-AI processing** — After the AI CLI exits, the service picks up where it left off (e.g., detecting new issues, parsing output files, capturing token usage from transcripts).

Each AI tool adapter must implement the abstract method `capabilities()` (binary name, model flag syntax, headless flag). All other methods — `initial_message_args()`, `launch()`, `parse_transcript()`, `is_model_compatible()`, and `build_launch_command()` — have default implementations and can be overridden as needed. The `launch()` method accepts an optional `transcript_path: Path | None` parameter — when provided, the adapter captures session output to that file for post-session token usage extraction. When adding a new AI-interactive command, follow this existing pattern.

**Deps delegation modes**: `deps_service.py` runs analysis via the generic delegation infrastructure (`delegation_service.py`). The default mode is `headless`; it can be overridden to `interactive` or `prompt` via the `ai.deps.mode` config key or the `--mode` CLI flag. There is no automatic fallback between modes — the resolved mode is used directly. Prompt mode prints the raw dependency-analysis prompt with no AI-tool requirement or worktree bootstrap. Headless and interactive modes perform the real AI launch path and are the only modes that create the temporary analysis worktree.

## Issue Detection (Snapshot/Diff Pattern)

`wade plan` uses a snapshot/diff pattern to detect issues created during an AI session (Path A — fallback):

1. **Before AI** — Snapshot all open issue numbers with the configured label
2. **AI runs** — The agent creates issues via `wade task create` from within the AI CLI
3. **After AI** — Compare current issue numbers against the pre-snapshot, returning only newly created ones

This avoids requiring the AI to report back which issues it created — the service detects them deterministically. When no issues are detected (Path B), the service reads plan files from the session temp dir and creates lightweight issues + draft PRs.

## Merge Strategy

`MergeStrategy` (config key `project.merge_strategy`) controls how feature branches are merged into main:
- **`PR`** (default) — The agent runs `wade implementation-session done` during its session to push the branch and update the existing draft PR (or create one if missing). The worktree is **not** cleaned up by `done` — it is cleaned up automatically by `implement` after the human merges the PR. When the tool exits, `implement`'s post-work prompt detects the PR and asks "Do you want to merge this PR?" — if yes, squash-merges via `gh pr merge --squash --delete-branch`.
- **`direct`** — Merge locally into main, push, and clean up the worktree. Useful for solo projects or repos without branch protection.

`wade implementation-session done` handles PR creation / direct merge. The post-work lifecycle prompt handles the merge decision (PR strategy) or local merge options (direct strategy).

## Determinism via Services

All deterministic operations — git commands, state transitions, file manipulation, API calls — **must live in service/utility code**, never in AI agent reasoning. Agents are non-deterministic; code is deterministic. The boundary is:

- **Code decides and executes** — fetch, merge, branch creation, worktree lifecycle, issue state changes. These are codified in `services/`, `git/`, `providers/` and exposed via `wade <command>`.
- **Agents interpret and decide next steps** — reading conflict diffs, choosing resolution strategies, composing commit messages, deciding whether to proceed. These are guided by skills.

When adding new functionality, ask: "Can an AI agent get this wrong by reasoning about it?" If yes, put it in code. Examples:

| Deterministic (code) | Non-deterministic (agent) |
|------------------------|---------------------------|
| `git merge main --no-edit` | Resolving merge conflicts |
| Checking if worktree is clean | Deciding what to commit |
| Creating branch with naming convention | Writing commit messages |
| Emitting structured JSON events | Interpreting event output |

This is why `wade implementation-session sync` exists as a CLI command rather than instructions for agents to run raw git commands — the sequence (preflight -> fetch -> merge -> conflict detection -> event emission) is deterministic and must not vary between agent sessions.

When wade installs skills into a target project (`wade init`), the skills reference `wade <command>` — they do **not** bundle standalone copies of the logic. The wade CLI is the single source of truth for deterministic operations.

## CLI Flag Reference

**`wade implement`:**
- `--detach` — Launch AI in a new terminal tab/window (non-blocking). Uses `build_launch_command()` + `launch_in_new_terminal()`.
- `--cd` — Create worktree, print its path to stdout, and exit without launching AI. Deterministic setup still runs first (for example worktree bootstrap and draft-PR bootstrap when needed). Used internally by `wade cd`.

**`wade implementation-session done`:**
- `target` (positional) — Optional issue number, worktree name, or plan file path. When a file path is given, creates the issue first; when a number/name, finds the worktree; when omitted, detects from current branch.
- `--no-close` — Don't close the issue on merge.
- `--draft` — Create PR as draft.
- `--no-cleanup` — Keep the worktree after direct merge (no effect in PR strategy, which already preserves worktrees).

**`wade implement-batch`:**
- `--model` — Pass a specific AI model to all parallel sessions.

**`wade worktree remove`:**
- `--all` — Hidden alias for `--stale` (removes all stale worktrees).

**`wade update`:**
- `--skip-self-upgrade` — Skip the source-version self-upgrade check.

**`wade task create`:**
- No flags required — prompts interactively for title and body.

**`wade shell-init`:**
- No flags. Outputs a shell function for `eval "$(wade shell-init)"`.

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
- `questionary>=2.0` — Interactive prompts (select, confirm, input)
- `structlog>=24.0` — Structured logging
- `httpx>=0.27,<1.0` — HTTP client (ClickUp provider)

Dev:
- `pytest>=8.0` — Test framework
- `pytest-cov>=5.0` — Coverage reporting
- `mypy>=1.10` — Static type checking (strict mode)
- `ruff>=0.4` — Linting and formatting
- `pre-commit>=3.7` — Git hook management
- `types-PyYAML>=6.0` — Type stubs for PyYAML
