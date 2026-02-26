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
# 1. Initialize ghaiw in your project
ghaiw init

# 2. Plan a feature (launches AI for planning)
ghaiw task plan

# 3. Start working on an issue (creates worktree + launches AI)
ghaiw work start 42

# 4. Sync with main before finishing
ghaiw work sync

# 5. Finalize and create PR
ghaiw work done
```

## Commands

### Project Setup

| Command | Description |
|---------|-------------|
| `ghaiw init` | Initialize ghaiw in the current project |
| `ghaiw update` | Update ghaiw skills, config migrations, and self-upgrade |
| `ghaiw update --skip-self-upgrade` | Update without checking for source code changes |
| `ghaiw deinit` | Remove ghaiw from the project |
| `ghaiw check` | Check worktree status (IN_WORKTREE / IN_MAIN_CHECKOUT / NOT_IN_GIT_REPO) |
| `ghaiw check-config` | Validate `.ghaiw.yml` configuration |
| `ghaiw shell-init` | Output shell function wrapper (see Shell Integration below) |

### Task Management

| Command | Description |
|---------|-------------|
| `ghaiw task plan` | Launch AI planning session |
| `ghaiw task create` | Create a GitHub issue interactively (prompts for title + body) |
| `ghaiw task create --plan-file <path>` | Create a GitHub issue from a plan markdown file |
| `ghaiw task list` | List open issues with the configured label |
| `ghaiw task read <N>` | Display issue details |
| `ghaiw task close <N>` | Close an issue |
| `ghaiw task deps <N> <M> ...` | Analyze dependencies between issues |

### Work Sessions

| Command | Description |
|---------|-------------|
| `ghaiw work` | Interactive menu (no args) |
| `ghaiw work start <N>` | Create worktree and start AI session for issue N |
| `ghaiw work start <N> --detach` | Start AI session in a new terminal tab |
| `ghaiw work start <N> --cd` | Create worktree and print path (no AI launch) |
| `ghaiw work sync` | Sync feature branch with main (fetch + merge) |
| `ghaiw work done` | Push branch and create PR (or direct merge) |
| `ghaiw work done [target]` | Finalize a specific issue, worktree, or plan file |
| `ghaiw work done --no-cleanup` | Create PR but keep the worktree |
| `ghaiw work list` | List active worktrees and their status |
| `ghaiw work batch <N> <M> ...` | Start parallel sessions for multiple issues |
| `ghaiw work batch --model <model>` | Parallel sessions with a specific AI model |
| `ghaiw work remove <N>` | Remove a worktree |
| `ghaiw work remove --stale` | Remove all stale worktrees |
| `ghaiw work cd <N>` | Print worktree path (for shell `cd`, see Shell Integration) |

## Shell Integration

Add this to your shell profile (`.bashrc`, `.zshrc`, etc.) to enable `ghaiw work cd`:

```bash
eval "$(ghaiw shell-init)"
```

This installs a shell function that intercepts `ghaiw work cd <N>` and performs a real `cd` into the worktree directory in your current shell.

## Self-Upgrade

When installed via `./install.sh` (frozen venv), `ghaiw update` automatically checks if the source repo has a newer version and reinstalls itself before proceeding with project updates. This ensures you always run the latest code without manual reinstalls.

```bash
# Skip self-upgrade (e.g., in CI or when testing)
ghaiw update --skip-self-upgrade
```

Editable installs (`uv pip install -e ".[dev]"`) skip self-upgrade automatically since code changes are reflected immediately.

## Configuration

ghaiw uses a `.ghaiw.yml` file in your project root:

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

Issues include a `## Complexity` section (`easy`, `medium`, `complex`, `very_complex`). When `ghaiw work start` launches an AI tool, it automatically selects the configured model for that complexity level.

## Agent Skills

ghaiw installs Agent Skill files into your project that teach AI agents the workflow:

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
ghaiw --install-completion bash

# Zsh
ghaiw --install-completion zsh

# Fish
ghaiw --install-completion fish
```

> **Note:** Shell completion is separate from `ghaiw shell-init`. The former provides tab-completion for subcommands; the latter enables `ghaiw work cd` to change your shell's directory.

## License

MIT
