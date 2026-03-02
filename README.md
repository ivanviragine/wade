# WADE — Workflow for AI-Driven Engineering

Turn GitHub Issues into isolated, AI-powered development sessions.

`wade init` wires your project once. After that, a single command creates a git worktree, launches your AI tool with full issue context, and hands you a PR when you're done — no manual branch juggling, no lost context, no forgotten ticket numbers.

## Requirements

- Python 3.11+
- git 2.20+
- [gh CLI](https://cli.github.com/) — install it, then authenticate:
  ```bash
  gh auth login     # follow the prompts
  gh auth status    # verify it worked
  ```
- One or more AI CLI tools: [Claude Code](https://claude.com/product/claude-code), [GitHub Copilot](https://github.com/features/copilot/cli), [Google Gemini](https://geminicli.com/), [OpenAI Codex](https://developers.openai.com/codex/cli/), [OpenCode](https://opencode.ai/), [VS Code](https://github.com/features/copilot/ai-code-editor), or [Antigravity](https://antigravity.google/)

## Installation

```bash
curl -LsSf https://raw.githubusercontent.com/ivanviragine/wade/main/install.sh | sh
```

Or with uv / pipx:

```bash
uv tool install wade
pipx install wade
```

## Quick Start

```bash
# Wire up your project (one-time)
wade init

# Plan features with AI — creates issues + draft PRs
wade plan-task

# Start working on an issue
wade implement-task 42

# Sync with main mid-work
wade work sync

# Ship — push branch and open PR
wade work done
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
| `wade init` | Initialize WADE in the current project |
| `wade update` | Upgrade WADE and refresh managed project files |
| `wade deinit` | Remove WADE from the project |
| `wade check` | Check worktree status |
| `wade check-config` | Validate `.wade.yml` |

### Core Commands

| Command | Description |
|---------|-------------|
| `wade plan-task` | AI planning session — creates issues + draft PRs |
| `wade new-task` | Create a GitHub issue interactively |
| `wade implement-task <N>` | Create worktree and start AI session for issue N |
| `wade implement-task <N> --detach` | Launch AI in a new terminal tab |
| `wade implement-task <N> --cd` | Create worktree, print path (no AI launch) |

### Task Management

| Command | Description |
|---------|-------------|
| `wade task list` | List open issues |
| `wade task read <N>` | Show issue details |
| `wade task close <N>` | Close an issue |
| `wade task deps <N> <M> ...` | Analyze dependencies between issues |

### Work Sessions

| Command | Description |
|---------|-------------|
| `wade work sync` | Sync feature branch with main |
| `wade work done` | Push branch and create PR |
| `wade work done --no-cleanup` | Keep the worktree after direct merge |
| `wade work list` | List active worktrees |
| `wade work batch <N> <M> ...` | Start parallel sessions for multiple issues |
| `wade work remove <N>` | Remove a worktree |
| `wade work remove --stale` | Remove all stale worktrees |
| `wade work cd <N>` | `cd` into a worktree (requires shell integration) |

## Configuration

WADE uses a `.wade.yml` file in your project root, created by `wade init`:

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

**Complexity-based models:** Issues carry a `complexity` label (`easy`, `medium`, `complex`, `very_complex`). `wade implement-task` picks the right model automatically.

## Agent Skills

`wade init` installs Skill files into `.claude/skills/` (with symlinks for Copilot, Gemini, and Codex) that teach your AI agent the workflow:

| Skill | Purpose |
|-------|---------|
| `task` | GitHub issue creation and plan format |
| `plan-session` | Planning session rules and workflow |
| `work-session` | Implementation session rules and workflow |
| `deps` | Dependency analysis between issues |

## Supported AI Tools

| Tool | Binary |
|------|--------|
| [Claude Code](https://claude.com/product/claude-code) | `claude` |
| [GitHub Copilot](https://github.com/features/copilot/cli) | `copilot` |
| [Google Gemini](https://geminicli.com/) | `gemini` |
| [OpenAI Codex](https://developers.openai.com/codex/cli/) | `codex` |
| [OpenCode](https://opencode.ai/) | `opencode` |
| [VS Code](https://github.com/features/copilot/ai-code-editor) | `code` |
| [Antigravity](https://antigravity.google/) | `antigravity` |

## Shell Integration

Add to your shell profile to enable `wade work cd`:

```bash
eval "$(wade shell-init)"
```

This lets `wade work cd <N>` actually change your shell's directory into the worktree. Without it, the command just prints the path.

Tab completion:

```bash
wade --install-completion bash   # or zsh / fish
```

## Upgrading

```bash
wade update
```

Detects your install method (`uv tool`, `pipx`, Homebrew) and upgrades automatically, then refreshes all managed project files.

## License

MIT
