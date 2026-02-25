# Architecture Reference

> Deep technical reference for the ghaiw-py codebase. For user-facing documentation, see [README.md](../README.md). For development rules and conventions, see [AGENTS.md](../AGENTS.md).

## Layered Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  CLI Layer (cli/)            Thin Typer dispatch             │
│  main.py · admin.py · task.py · work.py                      │
├──────────────────────────┬──────────────────────────────────┤
│  Service Layer (services/)│  Business logic orchestration    │
│  task · work · plan · deps · init · check                    │
├──────────┬───────────────┼──────────┬───────────────────────┤
│ Providers│  AI Tools     │  Git     │  DB                    │
│ (providers/) │ (ai_tools/)│ (git/)  │ (db/)                  │
│ base.py  │  base.py      │ repo.py  │ engine.py              │
│ github.py│  claude.py    │ worktree │ tables.py              │
│ registry │  copilot.py   │ branch   │ repositories.py        │
│          │  gemini.py    │ sync.py  │                        │
│          │  codex.py     │ pr.py    │                        │
│          │  opencode.py  │          │                        │
│          │  antigravity  │          │                        │
│          │  vscode.py    │          │                        │
│          │  model_utils  │          │                        │
│          │  transcript   │          │                        │
├──────────┴───────────────┴──────────┴───────────────────────┤
│  Models Layer (models/)     Pure Pydantic data — leaf dep    │
│  config · task · work · ai · deps · events                   │
├─────────────────────────────────────────────────────────────┤
│  Support Modules                                             │
│  config/ · skills/ · ui/ · logging/ · utils/                 │
└─────────────────────────────────────────────────────────────┘
```

### Import Rules

```
CLI Layer      ->  can import: services, models, config, logging, ui
Service Layer  ->  can import: providers, ai_tools, git, db, models, config, logging
Provider Layer ->  can import: models, config, logging  (NO service imports)
AI Tool Layer  ->  can import: models, config, logging  (NO service imports)
Git Layer      ->  can import: models, config, logging  (NO service imports)
DB Layer       ->  can import: models, logging           (NO config imports)
Models Layer   ->  can import: nothing (leaf dependency)
```

No circular dependencies. Never import a higher layer from a lower layer.

## CLI Layer

### Command Dispatch

`src/ghaiw/cli/main.py` is the root Typer application. It registers subcommand groups (`task`, `work`) and admin commands (`init`, `update`, `deinit`, `check`, `check-config`, `shell-init`). The `tasks` alias is registered as a hidden Typer group pointing to the same `task_app`. The `ghaiwpy` entry point (defined in `pyproject.toml` as `ghaiw.cli.main:app`) invokes the root app.

CLI modules are thin dispatch layers — they parse flags via Typer, then call service methods. Business logic lives in `services/`, not in `cli/`.

Interactive menus: `ghaiwpy` with no args shows the main interactive menu. `ghaiwpy task` with no subcommand shows a task-specific menu (plan/create/list/read/update/close/deps). `ghaiwpy work` with no subcommand shows a work-specific menu (start/done/sync/list/batch/remove). `ghaiwpy task create` without `--plan-file` prompts interactively for title and body.

Shell integration: `ghaiwpy shell-init` outputs a shell function wrapper for `eval "$(ghaiwpy shell-init)"` that intercepts `ghaiwpy work cd <n>` to perform a real `cd` in the caller's shell.

### Complete Command Inventory (22 commands)

#### Root Entry Point

| Command | Description |
|---------|-------------|
| `ghaiwpy` | Interactive main menu (no args) or dispatch to subcommands |
| `ghaiwpy --version / -V` | Print version |
| `ghaiwpy --verbose / -v` | Enable verbose logging |

Interactive main menu items:
1. Start working on a task -> work_service.start()
2. List active worktrees -> work_service.list_sessions()
3. Create a new task -> task_service.create_interactive()
4. Plan a new task with AI -> plan_service.plan()
5. Show help

#### Admin Commands (7)

| Command | Flags | Service Call |
|---------|-------|-------------|
| `ghaiwpy init` | `--ai`, `--yes/-y` | init_service.init() |
| `ghaiwpy update` | `--skip-self-upgrade` | init_service.update() |
| `ghaiwpy deinit` | `--force/-f` | init_service.deinit() |
| `ghaiwpy check` | — | check_service.check_worktree() |
| `ghaiwpy check-config` | — | check_service.validate_config() |
| `ghaiwpy shell-init` | `--shell` | Outputs shell function wrapper |
| `ghaiwpy changelog` | `--stdout`, `--tag` | scripts/changelog.py |

Exit codes for check: 0=IN_WORKTREE, 1=NOT_IN_GIT_REPO, 2=IN_MAIN_CHECKOUT
Exit codes for check-config: 0=VALID, 1=NOT_FOUND, 3=INVALID

#### Task Subcommands (7)

| Command | Flags | Service Call |
|---------|-------|-------------|
| `ghaiwpy task plan` | `--ai`, `--model` | plan_service.plan() |
| `ghaiwpy task create` | `--plan-file`, `--no-start`, `--ai`, `--model` | task_service.create_interactive() or create_from_plan_file() |
| `ghaiwpy task list` | `--state/-s`, `--deps`, `--json` | task_service.list_tasks() |
| `ghaiwpy task read <N>` | `--json` | task_service.read_task() |
| `ghaiwpy task update <N>` | `--plan-file`, `--comment` | task_service.update_task() |
| `ghaiwpy task close <N>` | `--comment` | task_service.close_task() |
| `ghaiwpy task deps <N M ...>` | `--ai`, `--model`, `--check` | deps_service.analyze_deps() |

#### Work Subcommands (7)

| Command | Flags | Service Call |
|---------|-------|-------------|
| `ghaiwpy work start <N>` | `--ai` (multi), `--model`, `--detach`, `--cd` | work_service.start() |
| `ghaiwpy work done [target]` | `--plan`, `--no-close`, `--draft`, `--no-cleanup` | work_service.done() |
| `ghaiwpy work sync` | `--json`, `--dry-run`, `--main-branch` | work_service.sync() |
| `ghaiwpy work list` | `--json`, `--all` | work_service.list_sessions() |
| `ghaiwpy work batch [N M ...]` | `--ai`, `--model` | work_service.batch() |
| `ghaiwpy work remove [target]` | `--stale`, `--all`, `--force` | work_service.remove() |
| `ghaiwpy work cd <N>` | — | work_service.find_worktree_path() |

### CLI Flag Reference

Key flags:

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
- `--shell` — Specify shell type (bash/zsh/fish). Outputs a shell function for `eval "$(ghaiwpy shell-init)"`.

## Service Layer

Services contain all business logic. Each service orchestrates calls to providers, AI tools, git operations, and the database.

### task_service.py

Task CRUD operations, plan file parsing, label management. Core methods:
- `create_interactive()` — Prompts for title/body, creates issue via provider
- `create_from_plan_file(path)` — Parses markdown plan file into PlanFile model, creates issue
- `list_tasks(state, deps, json_mode)` — Lists issues with configured label
- `read_task(number, json_mode)` — Displays single issue details
- `update_task(number, plan_file, comment)` — Updates issue body or adds comment
- `close_task(number, comment)` — Closes issue with optional comment

Label management helpers:
- `ensure_issue_label()` — Create the configured issue label if it doesn't exist
- `add_in_progress_label()` / `remove_in_progress_label()` — Track work state
- `add_planned_by_labels()` — Tag issues with AI tool and model used for planning
- `add_worked_by_labels()` — Tag issues with AI tool and model used for implementation
- `apply_plan_token_usage()` — Distribute planning token costs across created issues
- `build_plan_summary_block()` — Generate plan summary for PR bodies

### work_service.py

Work session lifecycle management. Core methods:
- `start(target, ai_tools, model, detach, cd_only)` — Full worktree creation + AI launch pipeline
- `done(target, draft, no_cleanup, no_close)` — PR creation or direct merge
- `sync(json_mode, dry_run, main_branch)` — Fetch + merge with structured event output
- `list_sessions(json_mode, show_all)` — List active/stale worktrees with status
- `batch(issues, ai_tool, model)` — Parallel session launcher with dependency ordering
- `remove(target, stale, force)` — Worktree cleanup
- `find_worktree_path(target)` — Locate existing worktree for an issue
- `classify_staleness(repo_root, branch, main_branch, issue_number)` — Determine worktree state

Session helpers:
- `write_issue_context(worktree_path, task)` — Write `.issue-context.md` for AI reference
- `write_plan_md(worktree_path, task)` — Write `PLAN.md` for AI consumption
- `bootstrap_worktree(worktree_path, config, repo_root)` — Copy files + run hooks
- `build_work_prompt(task, ai_tool)` — Generate context-rich prompt for AI session
- `build_impl_usage_block(ai_tool, model, token_usage)` — Generate usage stats for PR body
- `extract_issue_from_branch(branch)` — Parse issue number from branch name

### plan_service.py

AI planning session orchestration. Core methods:
- `plan(ai_tool, model)` — Full planning pipeline: snapshot -> AI session -> detection -> issue creation
- `get_plan_prompt_template()` — Load plan prompt from templates
- `render_plan_prompt(plan_dir)` — Render prompt with plan directory path
- `run_ai_planning_session(ai_tool, plan_dir, model, transcript_path)` — Launch AI in plan mode
- `discover_plan_files(plan_dir)` — Find `.md` files in temp directory
- `validate_plan_files(plan_dir)` — Parse and validate plan files into PlanFile models

### deps_service.py

Dependency analysis between issues. Core methods:
- `analyze_deps(issues, ai_tool, model, check_only)` — AI-powered dependency analysis with cross-reference injection
- `build_context(provider, issue_numbers)` — Collect issue titles and bodies for AI prompt
- `build_deps_prompt(context)` — Generate dependency analysis prompt
- `parse_deps_output(text, valid_numbers)` — Parse "N -> M # reason" edges from AI output
- `apply_deps_to_issues(provider, issue_numbers, edges)` — Inject `## Dependencies` sections into issues
- `create_tracking_issue(provider, config, issue_numbers, graph, task_titles)` — Create execution plan issue with checklist and Mermaid diagram
- `run_headless_analysis(ai_tool, prompt, model)` — Attempt headless AI analysis via `--print`/`--prompt`

### init_service.py

Project initialization and lifecycle. Core methods:
- `init(ai_tool, yes)` — Full init pipeline
- `update(skip_self_upgrade)` — 12-step update pipeline
- `deinit(force)` — Remove all ghaiw artifacts

### check_service.py

Safety checks and config validation. Core methods:
- `check_worktree()` — Determine if in worktree, main checkout, or not in git repo
- `validate_config()` — Parse and validate `.ghaiw.yml` (checks project, ai, models, provider, hooks sections)

## AI Tools Layer

### Self-Registration Pattern

```python
class AbstractAITool(ABC):
    _registry: ClassVar[dict[AIToolID, type[AbstractAITool]]] = {}

    def __init_subclass__(cls, **kwargs):
        if hasattr(cls, "TOOL_ID") and not inspect.isabstract(cls):
            cls._registry[cls.TOOL_ID] = cls  # Auto-register!
```

Adding a new AI tool = 1 file, 1 class, set TOOL_ID. No other files need modification.

### Registered Adapters (7)

| Adapter | TOOL_ID | Binary | Model Discovery | Headless | Plan Mode |
|---------|---------|--------|----------------|----------|-----------|
| ClaudeAdapter | CLAUDE | `claude` | `claude models` CLI -> web scrape fallback | `--print` | `--permission-mode plan` |
| CopilotAdapter | COPILOT | `copilot` | Validation error parsing | `--prompt` | No |
| GeminiAdapter | GEMINI | `gemini` | `gemini --list-models` -> web scrape | No | `--approval-mode plan` |
| CodexAdapter | CODEX | `codex` | Web scraping | No | No |
| OpenCodeAdapter | OPENCODE | `opencode` | `opencode models` CLI | `--prompt` | No |
| AntigravityAdapter | ANTIGRAVITY | `antigravity` | None | No | No |
| VSCodeAdapter | VSCODE | `code` | None (GUI) | No | No |

### Abstract Methods

Every adapter must implement:
```python
def capabilities(self) -> AIToolCapabilities      # Tool feature matrix
def get_models(self) -> list[AIModel]              # Available models
def launch(worktree_path, model, prompt, ...) -> int  # Launch AI CLI
def parse_transcript(transcript_path) -> TokenUsage   # Extract token usage
```

Optional overrides (with defaults):
- `is_model_compatible(model)` — Validate model ID for this tool (default: True)
- `plan_mode_args()` — Extra CLI args for plan/approval mode (default: [])
- `plan_dir_args(plan_dir)` — CLI args to grant plan directory access (default: [])
- `normalize_model_format(model_id)` — Normalize model ID format (default: as-is)
- `get_default_model(tier)` — Get best model for a tier (default: probes get_models())
- `get_recommended_mapping()` — Get model mapping for all complexity levels
- `build_launch_command(model, prompt, plan_mode)` — Build full command line

Class methods:
- `get(tool_id)` — Get adapter instance by ID from registry
- `available_tools()` — List all registered tool IDs
- `detect_installed()` — Check which tools are installed on the system (via `shutil.which`)

### Helper Modules

**`model_utils.py`** — Tier classification and model discovery utilities:
- `classify_tier_claude(model_id)` — haiku->FAST, sonnet->BALANCED, opus->POWERFUL
- `classify_tier_gemini(model_id)` — flash->FAST, pro->BALANCED, ultra->POWERFUL
- `classify_tier_universal(model_id)` — Generic classifier (used by Copilot, OpenCode)
- `has_date_suffix(model_id)` — Detect YYYYMMDD suffix (alias vs dated model)

**`transcript.py`** — Per-tool transcript parsers:
- `parse_claude_transcript(path)` — Parse "in:X out:Y cached:Z" footer
- `parse_copilot_transcript(path)` — Parse "X in, Y out, Z cached" summary
- `parse_gemini_transcript(path)` — Parse structured table rows
- `parse_codex_transcript(path)` — Parse "Token usage: total=X input=Y output=Z" footer

### AI Interaction Pattern

All AI-interactive commands follow the same pattern:
1. **Tool selection** — `--ai` flag -> config -> interactive prompt
2. **Clipboard prompt** — Copy context-rich prompt to clipboard, print paste message
3. **Launch AI CLI** — Execute binary via `AbstractAITool.launch()`, runs interactively
4. **Post-AI processing** — Detect new issues, parse transcripts, capture token usage

The `launch()` method accepts `transcript_path: Path | None` — when provided, captures session output for token extraction.

**Deps interactive fallback**: When headless analysis fails (tool doesn't support `--print`/`--prompt`), `deps_service.py` falls back to interactive mode.

### Model Format Normalization

- Claude CLI: Dashes — `claude-haiku-4-5`
- Copilot CLI: Dots — `claude-haiku-4.5`
- OpenCode: Provider-prefixed — `anthropic/claude-sonnet-4`
- Each adapter's `normalize_model_format()` handles conversion.

### Transcript Token Extraction (5-level cascade)

1. Gemini table format -> structured rows with model/requests/input/cache/output
2. Copilot summary -> "X in, Y out, Z cached" + per-model breakdown
3. Claude footer -> "in:614 out:94 cached:2,345"
4. Codex footer -> "Token usage: total=X input=Y output=Z cached=W"
5. Generic fallback -> keyword search: "total tokens:", "input tokens:", etc.

## Provider Layer

### Abstract Interface

```python
class AbstractTaskProvider(ABC):
    # Task CRUD (abstract — must implement)
    list_tasks(), create_task(), read_task(), update_task(), close_task(), comment_on_task()
    # Labels (abstract)
    ensure_label(), add_label(), remove_label()
    # Snapshot/diff (abstract)
    snapshot_task_numbers()
    # Optional (with default implementations)
    create_pr(), merge_pr(), get_pr_for_branch(), update_pr_body()
    get_repo_nwo(), find_parent_issue(), move_to_in_progress()
```

### GitHubProvider

Wraps `gh` CLI via subprocess for all operations:
- Issues: `gh issue list/create/view/edit/close/comment`
- Labels: `gh label list/create`, `gh issue edit --add-label/--remove-label`
- PRs: `gh pr create/merge/view/edit`
- Project boards: GraphQL mutations via `gh api graphql`

Future providers: Linear, Asana, Jira, Trello, ClickUp (ABC ready, registry extensible).

### Issue Detection (Snapshot/Diff Pattern)

`task plan` uses a snapshot/diff pattern to detect issues created during an AI session:
1. **Before AI** — Snapshot all open issue numbers with the configured label
2. **AI runs** — Agent creates issues via `ghaiwpy task create`
3. **After AI** — Compare current issue numbers against pre-snapshot

When no issues are detected, reads plan files from session temp dir instead (Path B).

## Git Layer

All operations are subprocess-based — no gitpython or similar libraries.

| Module | Functions | Subprocess Calls |
|--------|----------|-----------------|
| `repo.py` | `is_git_repo`, `is_worktree`, `get_repo_root`, `get_current_branch`, `detect_main_branch`, `is_clean`, `get_dirty_status`, `get_remote_url` | `git rev-parse`, `git status --porcelain`, `git remote get-url` |
| `worktree.py` | `create_worktree`, `remove_worktree`, `list_worktrees`, `prune_worktrees` | `git worktree add/remove/list/prune` |
| `branch.py` | `make_branch_name`, `branch_exists`, `create_branch`, `delete_branch`, `commits_ahead` | `git rev-parse --verify`, `git branch`, `git rev-list --count` |
| `sync.py` | `fetch_origin`, `merge_branch`, `get_conflicted_files`, `abort_merge` | `git fetch origin`, `git merge --no-edit`, `git diff --name-only --diff-filter=U` |
| `pr.py` | `create_pr`, `merge_pr`, `update_pr_body`, `get_pr_for_branch` | `gh pr create/merge/edit/view` |

Branch naming: `{prefix}/{issue_number}-{slugified_title}` (e.g., `feat/42-add-user-auth`)

### Merge Strategy

`MergeStrategy` (config key `project.merge_strategy`):
- **`PR`** (default) — Agent runs `ghaiwpy work done` to push and create PR. Worktree is not cleaned up — cleaned up by next `work start` after PR merge. Post-work prompt offers squash-merge via `gh pr merge --squash --delete-branch`.
- **`direct`** — Merge locally into main, push, clean up worktree.

## Data Models

All models are Pydantic `BaseModel` subclasses. Pure data — no I/O, no side effects.

### Core Enums

| Enum | Values | Purpose |
|------|--------|---------|
| `AIToolID` | CLAUDE, COPILOT, GEMINI, CODEX, OPENCODE, ANTIGRAVITY, VSCODE | AI tool identifiers |
| `AIToolType` | TERMINAL, GUI | Tool interaction mode |
| `ModelTier` | FAST, BALANCED, POWERFUL | Model capability tiers |
| `Complexity` | EASY, MEDIUM, COMPLEX, VERY_COMPLEX | Issue complexity levels |
| `TaskState` | OPEN, IN_PROGRESS, CLOSED | Issue lifecycle states |
| `MergeStrategy` | PR, DIRECT | How branches merge to main |
| `WorktreeState` | ACTIVE, STALE_EMPTY, STALE_MERGED, STALE_REMOTE_GONE | Worktree staleness |
| `ProviderID` | GITHUB, LINEAR, ASANA, TRELLO, CLICKUP, JIRA | Task backend providers |
| `LabelType` | ISSUE_LABEL, IN_PROGRESS, PLANNED_BY, PLANNED_MODEL, WORKED_BY, WORKED_MODEL, AI_LABEL | Label categories |
| `EventType` | 18 variants (PREFLIGHT_OK through DEPS_ANALYZED) | Structured event types |

### Domain Models

| Model | Key Fields | Purpose |
|-------|-----------|---------|
| `Task` | id, title, body, state, complexity, labels, url, parent_id, subtask_ids, created_at, updated_at | GitHub issue representation |
| `PlanFile` | path, title, complexity, body, sections | Parsed markdown plan file (has `from_markdown()` classmethod) |
| `WorkSession` | id, task_id, worktree_path, branch_name, ai_tool, ai_model, started_at, ended_at, token_usage, pr_number, pr_url | Active work session |
| `SyncResult` | success, current_branch, main_branch, conflicts, commits_merged, events | Merge operation result |
| `SyncEvent` | event, data, timestamp | Individual sync operation event |
| `WorkflowEvent` | event, data, timestamp | General workflow event |
| `TokenUsage` | total_tokens, input_tokens, output_tokens, cached_tokens, premium_requests, model_breakdown, raw_transcript_path | AI session token usage |
| `ModelBreakdown` | model, input_tokens, output_tokens, cached_tokens, premium_requests | Per-model usage within a session |
| `AIModel` | id, display_name, tier, is_alias | Available AI model (frozen) |
| `AIToolCapabilities` | tool_id, display_name, binary, tool_type, supports_model_flag, model_flag, headless_flag, supports_headless | Tool feature matrix (frozen) |
| `DependencyEdge` | from_task, to_task, reason | Single dependency between issues |
| `DependencyGraph` | edges, topological_order, independent_groups, mermaid_diagram, tracking_task_id | Full dependency analysis (has `topo_sort()`, `generate_mermaid()`, `partition()` methods) |
| `ProjectConfig` | version, project, ai, models, provider, hooks, config_path, project_root | Parsed .ghaiw.yml (has `get_ai_tool()`, `get_model()`, `get_complexity_model()` methods) |
| `ProjectSettings` | main_branch, issue_label, worktrees_dir, branch_prefix, merge_strategy | Project section of config |
| `AIConfig` | default_tool, plan, deps, work | AI section of config |
| `AICommandConfig` | tool, model | Per-command AI config (plan/deps/work) |
| `ComplexityModelMapping` | easy, medium, complex, very_complex | Complexity -> model ID map |
| `HooksConfig` | post_worktree_create, copy_to_worktree | Hooks section of config |
| `ProviderConfig` | name, project, api_token_env | Provider section of config |
| `Label` | name, color, description, label_type | GitHub label definition |

## Database Layer

### Engine

SQLite at `.ghaiw/ghaiw.db` with WAL mode, 30s busy timeout, foreign keys ON.

### Tables

| Table | PK | Key Columns | Purpose |
|-------|----|------------|---------|
| `TaskRecord` | id | provider, title, body, state, complexity, parent_id, url, created_at, updated_at, synced_at | Local task cache |
| `SessionRecord` | UUID | task_id (FK), session_type, ai_tool, ai_model, worktree_path, branch_name, started_at, ended_at, transcript_path | Work session tracking |
| `TokenUsageRecord` | UUID | session_id (FK), total/input/output/cached tokens, premium_requests, cost_estimate_usd | Token consumption |
| `ModelBreakdownRecord` | UUID | usage_id (FK), model, input/output/cached tokens, premium_requests | Per-model usage detail |
| `PRRecord` | UUID | session_id (FK), pr_number, pr_url, branch, state, created_at, merged_at | Pull request tracking |
| `WorktreeRecord` | UUID | task_id, path, branch, state, created_at, removed_at | Worktree lifecycle |
| `DependencyRecord` | UUID | from_task_id, to_task_id, reason, created_at | Task dependencies |
| `AuditLogRecord` | UUID | action, entity_type, entity_id, details, timestamp | Audit trail |

### Repository Pattern

Each table has a dedicated repository class providing typed CRUD methods:
- `TaskRepository` — create, get, get_by_state, update, upsert
- `SessionRepository` — create, get, get_by_task, get_active, update
- `TokenUsageRepository` — create, get_by_session, total_tokens_for_task
- `PRRepository` — create, get_by_session
- `WorktreeRepository` — create, get_active, get_by_task, update
- `DependencyRepository` — create, get_by_tasks
- `AuditLogRepository` — create, get_by_entity

## Config System

### Config Loading

`config/loader.py` walks up from CWD to find `.ghaiw.yml` and parses it via PyYAML into a `ProjectConfig` Pydantic model.

Full v2 config format:
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

### Config Fallback Chain

```
CLI --ai/--model flag  ->  command-specific config (ai.plan.tool)  ->  global default_tool
```

Implemented via `ProjectConfig.get_ai_tool(command)`, `ProjectConfig.get_model(command)`, and `ProjectConfig.get_complexity_model(tool, complexity)`.

### Config Migration Pipeline

`config/migrations.py` provides 7 idempotent migrations run during `ghaiwpy update`:

| # | Function | What it does |
|---|----------|-------------|
| 1 | `ensure_version(raw)` | Set `version: 2` if missing |
| 2 | `migrate_deprecated_model_values(raw, ai_tool)` | Replace deprecated model names |
| 3 | `migrate_flat_to_nested_models(raw, ai_tool)` | Move v1 flat keys to v2 nested `models.<tool>.<tier>` |
| 4 | `normalize_model_format(raw, ai_tool)` | Dashes (Claude) vs dots (Copilot) |
| 5 | `backfill_missing_model_keys(raw, ai_tool)` | Fill missing tiers with probed/default models |
| 6 | `backfill_per_command_keys(raw)` | Ensure `ai.plan`, `ai.deps`, `ai.work` sections exist |
| 7 | `normalize_merge_strategy(raw)` | Lowercase `pr` -> uppercase `PR` |

`run_all_migrations(config_path)` loads YAML, runs all 7 in order, writes back only if changed. Returns `True` if file was modified.

### Additional Config Modules

- **`config/defaults.py`** — Hardcoded `TOOL_DEFAULTS` dict with fallback model mappings per AI tool (Claude, Copilot, Gemini, Codex, Antigravity). Used when model probing fails or returns no recognized models.
- **`config/schema.py`** — Re-exports Pydantic config models from `models/config.py` (AICommandConfig, AIConfig, ComplexityModelMapping, HooksConfig, ProjectConfig, ProjectSettings, ProviderConfig, ProviderID). Provides a clean import path for the config layer.
- **`config/claude_allowlist.py`** — Manages `.claude/settings.json` to allowlist `ghaiwpy` commands so Claude Code can invoke them without manual approval. Pattern: `Bash(ghaiwpy *)`.
- **`config/legacy.py`** — Removes artifacts from older ghaiw versions during `ghaiwpy update` (old skill directories, renamed files, pre-v1/v2/v3 format remnants).

## Skills and Pointer System

### Skill Installation

`skills/installer.py` handles skill file installation:
- **Self-init** (this repo): Creates symlinks from `.claude/skills/<name>` -> `../../templates/skills/<name>`
- **Normal init** (target projects): Copies files from `templates/skills/` to `.claude/skills/`
- Cross-tool symlinks: `.github/skills/`, `.agents/skills/`, `.gemini/skills/` all point to `.claude/skills/`
- `ALWAYS_OVERWRITE` set: `{"workflow"}` — always refreshed on update regardless of changes

Skill templates and their files:

| Skill | Files | Purpose |
|-------|-------|---------|
| `workflow` | SKILL.md | Always-on session rules (worktree safety, commit conventions) |
| `task` | SKILL.md, plan-format.md, examples.md | Issue creation workflow |
| `sync` | SKILL.md, examples.md, reference.md | Branch sync and conflict resolution |
| `deps` | SKILL.md | Dependency analysis |
| `pr-summary` | SKILL.md | PR description writing |

### Pointer System

`skills/pointer.py` manages the AGENTS.md workflow pointer:

Marker format:
```
<!-- ghaiw:pointer:start -->
## Git Workflow
...
<!-- ghaiw:pointer:end -->
```

Functions:
- `get_pointer_content()` — Load pointer template from `templates/agents-pointer.md`
- `has_pointer(file_path)` — Check for marker-delimited block
- `extract_pointer_content(file_path)` — Extract text between markers
- `remove_pointer(file_path)` — Remove marker block (with old-style fallback)
- `write_pointer(file_path)` — Append marker-wrapped pointer
- `ensure_pointer(project_root)` — High-level detect/refresh/install

Staleness detection: Extract inner content -> compare to current template -> refresh if different.

## UI and Utils

### UI Layer

| Module | Purpose |
|--------|---------|
| `console.py` | Rich-based terminal output with ~35-entry semantic color palette. Methods: `success()`, `error()`, `warning()`, `info()`, `detail()`, `hint()`, `step()`, `header()`, `banner()`, `empty()`, `table()`, `panel()`, `rule()` |
| `prompts.py` | TTY-aware interactive prompts: `confirm()`, `input_prompt()`, `select()`, `menu()` (returns defaults if not TTY — CI-safe) |
| `formatters.py` | Dual output via `OutputFormatter`: `--json` mode (structured JSON) vs human-readable (Rich tables). Style maps for staleness, state, and complexity |

### Utils Layer

| Module | Purpose |
|--------|---------|
| `clipboard.py` | Cross-platform clipboard: pbcopy (macOS) -> xclip -> xsel |
| `terminal.py` | Tab title (OSC escape), background title keeper thread, terminal detection (Ghostty/iTerm2/tmux/GNOME Terminal), `launch_in_new_terminal()` |
| `slug.py` | Title -> URL-safe slug (lowercase, hyphens, collapse, truncate at word boundary, max 40 chars) |
| `markdown.py` | Extract title/sections from markdown, marker block operations (`has_marker_block()`, `extract_marker_block()`) |
| `process.py` | `run()` with logging, `run_with_transcript()` (via `script` utility), `CommandError` exception |
| `install.py` | Self-upgrade: read source version -> compare -> reinstall -> `os.execv()` re-exec |

### Logging

`logging/setup.py` configures structlog:
- TTY-aware rendering: `ConsoleRenderer` if TTY, `JSONRenderer` otherwise
- Log level: DEBUG if `--verbose`, ERROR otherwise

`logging/context.py` provides session-scoped context variables:
- `bind_session_context(session_id, task_id)` — Bind context for all subsequent log entries via `structlog.contextvars`
- `clear_session_context()` — Clear all session context variables

## Dependencies

### Runtime

| Package | Version | Purpose |
|---------|---------|---------|
| `typer` | >=0.12 | CLI framework |
| `pydantic` | >=2.0 | Data validation and settings |
| `pydantic-settings` | >=2.0 | Env var overrides |
| `sqlmodel` | >=0.0.16 | SQLite ORM (SQLAlchemy + Pydantic) |
| `pyyaml` | >=6.0 | YAML config parsing |
| `rich` | >=13.0 | Terminal UI |
| `structlog` | >=24.0 | Structured logging |

### Development

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | >=8.0 | Test framework |
| `pytest-cov` | >=5.0 | Coverage reporting |
| `mypy` | >=1.10 | Static type checking (strict) |
| `ruff` | >=0.4 | Linting and formatting |
| `pre-commit` | >=3.7 | Git hook management |
| `types-PyYAML` | >=6.0 | Type stubs |

### External

- **Python** 3.11+ (uses `StrEnum`, `|` union syntax)
- **git** 2.20+ (worktree commands)
- **gh CLI** — must be authenticated; needs `project` scope for board moves
- **uv** — recommended for development

## Key Design Patterns Summary

| Pattern | Where | How |
|---------|-------|-----|
| Self-Registration | AI Tools | `__init_subclass__` auto-registers adapters — zero-config extension |
| Provider Abstraction | Providers | ABC + GitHubProvider wrapping `gh` CLI — future providers are drop-in |
| Subprocess Encapsulation | Git, Providers | All external calls through `_run_git()` / `_run_gh()` wrappers with domain exceptions |
| Snapshot/Diff | Plan Service | Pre/post issue snapshot to detect AI-created issues without agent feedback |
| Cascading Fallback | Model Discovery, Transcripts | CLI probing -> web scraping -> defaults; 5-level token parsing cascade |
| Marker-Based Blocks | Pointer, Token Usage | HTML comment markers for idempotent update/detection of injected content |
| Config Migration Pipeline | Config | 7 idempotent migrations run on update — safe to re-run |
| Complexity-to-Model Mapping | Work Service | Issue complexity tier -> config lookup -> appropriate model for AI session |
| Determinism via Services | Architecture | All deterministic ops in code (services/git/providers); agents handle only interpretation |
| Progressive Disclosure | Skills | AGENTS.md pointer -> workflow skill -> task-specific skills (loaded on-demand) |
| TTY Awareness | UI/Prompts | Interactive prompts only when stdin is a TTY; defaults otherwise (CI-safe) |

## File Count Summary

```
src/ghaiw/                    # Main package
├── cli/          (4 files)   # Typer commands
├── models/       (6 files)   # Pydantic domain models
├── services/     (6 files)   # Business logic
├── providers/    (3 files)   # Task backend adapters
├── ai_tools/    (11 files)   # AI tool adapters + helpers
├── git/          (5 files)   # Git operations
├── db/           (3 files)   # SQLite persistence
├── config/       (7 files)   # Configuration management
├── skills/       (2 files)   # Skill file management
├── ui/           (3 files)   # Terminal UI
├── logging/      (3 files)   # Structured logging
└── utils/        (6 files)   # Shared utilities

templates/                    # Installed artifacts
├── skills/       (5 dirs)    # Skill templates (9 files total)
├── prompts/                  # AI prompt templates
└── agents-pointer.md         # AGENTS.md injection template

tests/                        # Test suites
├── unit/                     # Pure logic tests
├── integration/              # Git + mock gh tests
├── e2e/                      # End-to-end smoke tests
├── live/                     # Real GitHub tests
└── fixtures/                 # Static test data

scripts/                      # Dev automation
├── auto_version.py           # Version bumping
└── changelog.py              # CHANGELOG generation
```

Total: ~59 Python source files, 8 DB tables, 30+ Pydantic models, 22 CLI commands, 7 AI tool adapters, 7 config migrations.
