# ghaiw

Turn GitHub Issues into isolated, AI-powered development sessions.

`ghaiw init` wires your project once. After that, a single command creates a git worktree, launches your AI tool with full issue context, and hands you a PR when you're done — no manual branch juggling, no lost context, no forgotten ticket numbers.

## Requirements

- Python 3.11+
- git 2.20+
- [gh CLI](https://cli.github.com/) (authenticated)
- One or more AI CLI tools: `claude`, `copilot`, `gemini`, `codex`

## Installation

```bash
curl -LsSf https://raw.githubusercontent.com/ivanviragine/ghaiw/main/install.sh | sh
```

Or with uv / pipx:

```bash
uv tool install ghaiw
pipx install ghaiw
```

## Quick Start

```bash
# Wire up your project (one-time)
ghaiw init

# Plan features with AI — creates issues + draft PRs
ghaiw plan-task

# Start working on an issue
ghaiw implement-task 42

# Sync with main mid-work
ghaiw work sync

# Ship — push branch and open PR
ghaiw work done
```

## The Workflow

```
plan-task          →   GitHub Issue + draft PR (AI writes the plan)
implement-task 42  →   worktree + AI session (isolated per issue)
work sync          →   fetch + merge main into your branch
work done          →   push + PR (or direct merge)
```

Each issue gets its own git worktree so you can work on multiple tasks in parallel without stashing or switching branches.

## Commands

### Project Setup

| Command | Description |
|---------|-------------|
| `ghaiw init` | Initialize ghaiw in the current project |
| `ghaiw update` | Upgrade ghaiw and refresh managed project files |
| `ghaiw deinit` | Remove ghaiw from the project |
| `ghaiw check` | Check worktree status |
| `ghaiw check-config` | Validate `.ghaiw.yml` |

### Core Commands

| Command | Description |
|---------|-------------|
| `ghaiw plan-task` | AI planning session — creates issues + draft PRs |
| `ghaiw new-task` | Create a GitHub issue interactively |
| `ghaiw implement-task <N>` | Create worktree and start AI session for issue N |
| `ghaiw implement-task <N> --detach` | Launch AI in a new terminal tab |
| `ghaiw implement-task <N> --cd` | Create worktree, print path (no AI launch) |

### Task Management

| Command | Description |
|---------|-------------|
| `ghaiw task list` | List open issues |
| `ghaiw task read <N>` | Show issue details |
| `ghaiw task close <N>` | Close an issue |
| `ghaiw task deps <N> <M> ...` | Analyze dependencies between issues |

### Work Sessions

| Command | Description |
|---------|-------------|
| `ghaiw work sync` | Sync feature branch with main |
| `ghaiw work done` | Push branch and create PR |
| `ghaiw work done --no-cleanup` | Create PR but keep the worktree |
| `ghaiw work list` | List active worktrees |
| `ghaiw work batch <N> <M> ...` | Start parallel sessions for multiple issues |
| `ghaiw work remove <N>` | Remove a worktree |
| `ghaiw work remove --stale` | Remove all stale worktrees |
| `ghaiw work cd <N>` | `cd` into a worktree (requires shell integration) |

## Configuration

ghaiw uses a `.ghaiw.yml` file in your project root, created by `ghaiw init`:

```yaml
version: 2

project:
  main_branch: main
  issue_label: feature-plan
  worktrees_dir: ../.worktrees
  branch_prefix: feat
  merge_strategy: PR       # PR or direct

ai:
  default_tool: claude
  plan:
    tool: claude
  work:
    tool: claude

models:
  claude:
    easy: claude-haiku-4-5-20251001
    medium: claude-sonnet-4-6
    complex: claude-sonnet-4-6
    very_complex: claude-opus-4-6
```

**Merge strategies:** `PR` (default) pushes the branch and opens a Pull Request; `direct` merges into main locally.

**Complexity-based models:** Issues carry a `complexity` label (`easy`, `medium`, `complex`, `very_complex`). `ghaiw implement-task` picks the right model automatically.

## Agent Skills

`ghaiw init` installs Skill files into `.claude/skills/` (with symlinks for Copilot, Gemini, and Codex) that teach your AI agent the workflow:

| Skill | Purpose |
|-------|---------|
| `workflow` | Session rules — worktree safety, commit conventions |
| `task` | GitHub issue creation |
| `sync` | Branch sync and conflict resolution |
| `deps` | Dependency analysis between issues |
| `pr-summary` | PR description writing |

## Supported AI Tools

| Tool | Binary |
|------|--------|
| Claude Code | `claude` |
| GitHub Copilot | `copilot` |
| Google Gemini | `gemini` |
| OpenAI Codex | `codex` |

## Shell Integration

Add to your shell profile to enable `ghaiw work cd`:

```bash
eval "$(ghaiw shell-init)"
```

This lets `ghaiw work cd <N>` actually change your shell's directory into the worktree. Without it, the command just prints the path.

Tab completion:

```bash
ghaiw --install-completion bash   # or zsh / fish
```

## Upgrading

```bash
ghaiw update
```

Detects your install method (`uv tool`, `pipx`, Homebrew) and upgrades automatically, then refreshes all managed project files.

## License

MIT
