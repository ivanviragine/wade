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

## Commands

### Project Setup

| Command | Description |
|---------|-------------|
| `ghaiwpy init` | Initialize ghaiw in the current project |
| `ghaiwpy update` | Update ghaiw skills and config |
| `ghaiwpy deinit` | Remove ghaiw from the project |
| `ghaiwpy check` | Check worktree status (IN_WORKTREE / IN_MAIN_CHECKOUT / NOT_IN_GIT_REPO) |
| `ghaiwpy check-config` | Validate `.ghaiw.yml` configuration |

### Task Management

| Command | Description |
|---------|-------------|
| `ghaiwpy task plan` | Launch AI planning session |
| `ghaiwpy task create` | Create a GitHub issue (interactive or from plan file) |
| `ghaiwpy task list` | List open issues with the configured label |
| `ghaiwpy task read <N>` | Display issue details |
| `ghaiwpy task close <N>` | Close an issue |
| `ghaiwpy task deps` | Analyze dependencies between issues |

### Work Sessions

| Command | Description |
|---------|-------------|
| `ghaiwpy work start <N>` | Create worktree and start AI session for issue N |
| `ghaiwpy work sync` | Sync feature branch with main (fetch + merge) |
| `ghaiwpy work done` | Push branch and create PR (or direct merge) |
| `ghaiwpy work list` | List active worktrees and their status |
| `ghaiwpy work batch` | Start parallel sessions for multiple issues |
| `ghaiwpy work remove <N>` | Remove a worktree |

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
| Antigravity | `antigravity` | Supported |

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
python scripts/auto_version.py patch   # bug fixes (0.1.0 → 0.1.1)
python scripts/auto_version.py minor   # new features (0.1.0 → 0.2.0)
python scripts/auto_version.py major   # breaking changes (0.1.0 → 1.0.0)
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

## License

MIT
