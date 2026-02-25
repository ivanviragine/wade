# ghaiw — AI-agent-driven git workflow management CLI

Python CLI toolkit for managing AI-assisted software development workflows. Wraps `gh` CLI and native git to manage GitHub Issues as tasks, git worktrees for isolated development, branch safety checks, and installable Agent Skill files.

## Installation

```bash
# Using the install script (recommended — sets up uv venv + symlink)
./install.sh
./install.sh ~/.local    # non-interactive, installs to ~/.local/bin

# Or with pip
pip install .

# Or with uv directly
uv pip install .
```

## Requirements

- Python 3.11+
- git 2.20+
- [gh CLI](https://cli.github.com/) (authenticated)
- One or more AI CLI tools: `claude`, `copilot`, `gemini`, `codex`

## Quick Start

```bash
# 1. Initialize ghaiwpy in your project
ghaiwpy init

# 2. Plan a feature (launches AI for planning)
ghaiwpy task plan

# 3. Start working on an issue (creates worktree + launches AI)
ghaiwpy work start 42

# 4. Sync with main before finishing
ghaiwpy work sync

# 5. Finalize and create PR
ghaiwpy work done
```

## How It Works

ghaiw structures AI-assisted development into a repeatable loop that ensures safety, isolation, and determinism.

### The Workflow Loop

The lifecycle of a feature follows a structured path:

1. **Plan** — `ghaiwpy task plan` launches an AI session that decomposes a feature into GitHub Issues. This creates a roadmap of atomic tasks.
2. **Start** — `ghaiwpy work start <N>` creates an isolated git worktree, writes context files (`.issue-context.md`, `PLAN.md`), and launches the AI tool. The agent starts with everything it needs to know about the task.
3. **Sync** — `ghaiwpy work sync` fetches and merges `origin/main` into the feature branch. This is deterministic and handles the preflight checks, so you don't need manual git commands.
4. **Done** — `ghaiwpy work done` pushes the branch and creates a PR or performs a direct merge, depending on your configuration.
5. **Batch** — `ghaiwpy work batch` runs multiple issues in parallel, respecting dependency order and launching sessions in separate terminal tabs.

### Worktree Isolation

Every issue gets its own git worktree, which is a separate working directory sharing the same `.git` store. This architecture provides several benefits:

- Multiple issues can be worked on simultaneously without stashing or branching conflicts.
- Each worktree has its own `.issue-context.md` and `PLAN.md` for AI reference, keeping context local to the task.
- Branch naming is automatic: `{prefix}/{issue_number}-{slugified_title}` (e.g., `feat/42-add-user-auth`).
- Stale worktrees (merged PR, deleted branch, or no commits) are automatically detected and cleaned up.
- Hooks can copy files like `.env` and run setup scripts automatically per worktree.

### AI Tool Integration

ghaiw uses a clipboard-prompt pattern. It doesn't embed AI directly. Instead, it launches AI CLI tools as subprocesses:

1. Copies a context-rich prompt to the clipboard.
2. Launches the AI tool (e.g., `claude`, `copilot`, `gemini`) in the worktree.
3. After the session, it parses transcripts for token usage tracking.

The system supports 7 AI tools via a self-registering adapter pattern. Adding a new tool requires only one file and one class. Model selection is automatic: the issue complexity (`easy`, `medium`, `complex`, `very_complex`) maps to your configured models.

### Merge Strategies

- **PR** (default) — Pushes the branch and creates a PR via `gh pr create`. The worktree stays until the PR is merged. A post-session prompt offers to squash-merge the PR.
- **direct** — Merges locally into main, pushes, and cleans up the worktree. This is ideal for solo projects or repositories without branch protection.

## Commands

### Project Setup

| Command | Description |
|---------|-------------|
| `ghaiwpy init` | Initialize ghaiw in the current project |
| `ghaiwpy update` | Update ghaiw skills, config migrations, and self-upgrade |
| `ghaiwpy update --skip-self-upgrade` | Update without checking for source code changes |
| `ghaiwpy deinit` | Remove ghaiw from the project |
| `ghaiwpy check` | Check worktree status (IN_WORKTREE / IN_MAIN_CHECKOUT / NOT_IN_GIT_REPO) |
| `ghaiwpy check-config` | Validate `.ghaiw.yml` configuration |
| `ghaiwpy shell-init` | Output shell function wrapper (see Shell Integration below) |

### Task Management

| Command | Description |
|---------|-------------|
| `ghaiwpy task plan` | Launch AI planning session |
| `ghaiwpy task create` | Create a GitHub issue interactively (prompts for title + body) |
| `ghaiwpy task create --plan-file <path>` | Create a GitHub issue from a plan markdown file |
| `ghaiwpy task list` | List open issues with the configured label |
| `ghaiwpy task read <N>` | Display issue details |
| `ghaiwpy task close <N>` | Close an issue |
| `ghaiwpy task deps <N> <M> ...` | Analyze dependencies between issues |

### Work Sessions

| Command | Description |
|---------|-------------|
| `ghaiwpy work` | Interactive menu (no args) |
| `ghaiwpy work start <N>` | Create worktree and start AI session for issue N |
| `ghaiwpy work start <N> --detach` | Start AI session in a new terminal tab |
| `ghaiwpy work start <N> --cd` | Create worktree and print path (no AI launch) |
| `ghaiwpy work sync` | Sync feature branch with main (fetch + merge) |
| `ghaiwpy work done` | Push branch and create PR (or direct merge) |
| `ghaiwpy work done [target]` | Finalize a specific issue, worktree, or plan file |
| `ghaiwpy work done --no-cleanup` | Create PR but keep the worktree |
| `ghaiwpy work list` | List active worktrees and their status |
| `ghaiwpy work batch <N> <M> ...` | Start parallel sessions for multiple issues |
| `ghaiwpy work batch --model <model>` | Parallel sessions with a specific AI model |
| `ghaiwpy work remove <N>` | Remove a worktree |
| `ghaiwpy work remove --stale` | Remove all stale worktrees |
| `ghaiwpy work cd <N>` | Print worktree path (for shell `cd`, see Shell Integration) |

## Shell Integration

Add this to your shell profile (`.bashrc`, `.zshrc`, etc.) to enable `ghaiwpy work cd`:

```bash
eval "$(ghaiwpy shell-init)"
```

This installs a shell function that intercepts `ghaiwpy work cd <N>` and performs a real `cd` into the worktree directory in your current shell.

## Self-Upgrade

When installed via `./install.sh` (frozen venv), `ghaiwpy update` automatically checks if the source repo has a newer version and reinstalls itself before proceeding with project updates. This ensures you always run the latest code without manual reinstalls.

```bash
# Skip self-upgrade (e.g., in CI or when testing)
ghaiwpy update --skip-self-upgrade
```

Editable installs (`uv pip install -e ".[dev]"`) skip self-upgrade automatically since code changes are reflected immediately.

## Configuration

ghaiwpy uses a `.ghaiw.yml` file in your project root:

```yaml
version: 2

project:
  main_branch: main
  issue_label: feature-plan
  worktrees_dir: ../.worktrees
  branch_prefix: feat
  merge_strategy: PR       # PR or direct

ai:
  default_tool: copilot
  plan:
    tool: claude
  work:
    tool: copilot

models:
  copilot:
    easy: claude-haiku-4.5
    medium: claude-haiku-4.5
    complex: claude-sonnet-4.6
    very_complex: claude-opus-4.6
```

### Merge Strategies

- **PR** (default) — pushes branch and creates a Pull Request via `gh pr create`
- **direct** — merges locally into main and pushes

### Complexity-to-Model Mapping

Issues include a `## Complexity` section (`easy`, `medium`, `complex`, `very_complex`). When `ghaiwpy work start` launches an AI tool, it automatically selects the configured model for that complexity level.

## Agent Skills

ghaiwpy installs Agent Skill files into your project that teach AI agents the workflow:

| Skill | Purpose |
|-------|---------|
| `workflow` | Always-on session rules (worktree safety, commit conventions) |
| `task` | GitHub issue creation workflow |
| `sync` | Branch sync and merge conflict resolution |
| `deps` | Dependency analysis between issues |
| `pr-summary` | PR description writing guidance |

Skills are installed to `.claude/skills/` with symlinks from `.github/skills/`, `.agents/skills/`, and `.gemini/skills/` for cross-tool discovery.

## Supported AI Tools

| Tool | Binary | Status |
|------|--------|--------|
| Claude Code | `claude` | Supported |
| GitHub Copilot | `copilot` | Supported |
| Google Gemini | `gemini` | Supported |
| OpenAI Codex | `codex` | Supported |
| OpenCode | `opencode` | Supported |
| Antigravity | `antigravity` | Supported |
| VS Code | `code` | Supported (GUI) |

## Architecture Overview

ghaiw is designed with a modular, layered architecture that separates concerns and ensures deterministic behavior.

### Package Structure

The source code is organized into specialized modules:

```
src/ghaiw/
├── __init__.py          # __version__
├── __main__.py          # python -m ghaiw
├── cli/                 # Typer commands (thin dispatch)
│   ├── main.py          # Root app + interactive menu
│   ├── admin.py         # init, update, deinit, check
│   ├── task.py          # task plan/create/list/read/update/close/deps
│   └── work.py          # work start/done/sync/list/batch/remove/cd
├── models/              # Pydantic domain models (pure data, no I/O)
├── services/            # Business logic (orchestration)
├── providers/           # Task backend providers (ABC)
├── ai_tools/            # AI tool adapters (self-registering)
├── git/                 # Git operations (all subprocess)
├── db/                  # SQLite via SQLModel
├── config/              # Configuration management
├── skills/              # Skill file management
├── ui/                  # Terminal UI (Rich)
├── logging/             # Structured logging (structlog)
└── utils/               # Shared utilities
```

### Layered Architecture

The project follows a strict layered import policy to ensure maintainability and prevent circular dependencies:

```
CLI → Services → Providers / AI Tools / Git / DB → Models
```

Each layer can only import from layers below it. Models are pure data and serve as a leaf dependency. Services orchestrate the business logic, while the CLI layer remains a thin dispatch.

### Key Design Patterns

- **AI Tool Self-Registration** — `__init_subclass__` auto-registers adapters. Adding a new tool only requires creating one file.
- **Provider Abstraction** — Uses an ABC with GitHubProvider as the current implementation. Future providers like Linear or Jira are drop-in replacements.
- **Determinism via Services** — All git and state operations are handled in code, never left to AI reasoning.
- **Prompts as Templates** — AI prompts live in `templates/prompts/` rather than being hardcoded as inline strings.
- **Pydantic Everywhere** — All data structures are implemented as typed Pydantic models.
- **Synchronous Only** — No asyncio is used. Process-level parallelism is achieved via multiple terminals.

### Technology Stack

| Component | Technology |
|-----------|-----------|
| CLI Framework | Typer |
| Data Models | Pydantic |
| Database | SQLModel (SQLite) |
| Terminal UI | Rich |
| Logging | structlog |
| Config | PyYAML + Pydantic Settings |

For the complete architecture reference, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). For development guidelines and conventions, see [AGENTS.md](AGENTS.md).

## Development

```bash
# Install dev dependencies
uv pip install -e ".[dev]"

# Run tests
uv run pytest tests/ -v --ignore=tests/live

# Type check
uv run mypy src/ --strict

# Lint
uv run ruff check src/
uv run ruff format --check src/

# Pre-commit hooks
pre-commit install
pre-commit run --all-files
```

### Docker

```bash
# Run tests in Docker
docker compose run test

# Test against multiple Python versions
docker compose run test-py311
docker compose run test-py312
docker compose run test-py313
```

### Version Bumping

```bash
python scripts/auto_version.py patch   # bug fixes (0.8.0 → 0.8.1)
python scripts/auto_version.py minor   # new features (0.8.0 → 0.9.0)
python scripts/auto_version.py major   # breaking changes (0.8.0 → 1.0.0)
```

### Shell Completion

Typer provides built-in shell completion:

```bash
# Bash
ghaiwpy --install-completion bash

# Zsh
ghaiwpy --install-completion zsh

# Fish
ghaiwpy --install-completion fish
```

> **Note:** Shell completion is separate from `ghaiwpy shell-init`. The former provides tab-completion for subcommands; the latter enables `ghaiwpy work cd` to change your shell's directory.

## License

MIT
