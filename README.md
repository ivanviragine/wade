# WADE — Workflow for AI-Driven Engineering

Turn GitHub Issues into isolated, AI-powered development sessions — with one command.

## The Problem

Working with AI coding tools is powerful, but the workflow around them is still manual:

| Without WADE | With WADE |
|---|---|
| Copy-paste issue details into AI chat | AI reads the issue automatically |
| Create branch, remember naming convention | `wade implement-task 42` handles it |
| One task at a time (or messy stash juggling) | Parallel tasks in isolated git worktrees |
| Manually write PR, link issue, clean up branch | `wade work done` — one command |
| Re-explain project conventions to AI every time | Skills teach your AI how your project works |

## Installation

```bash
curl -LsSf https://raw.githubusercontent.com/ivanviragine/wade/main/install.sh | sh
```

Requires [gh CLI](https://cli.github.com/) (authenticated) and at least one AI coding tool. Everything else — including Python — is managed automatically by `uv`.

```bash
# Verify gh is authenticated
gh auth login     # follow the prompts
gh auth status    # confirm it worked
```

Or install manually:

```bash
uv tool install wade
pipx install wade
```

## How It Works

**Before WADE** — starting work on GitHub Issue #42:

```bash
git fetch origin && git checkout main && git pull
git checkout -b feat/issue-42-user-auth
# copy issue title + description into AI chat
# explain branching conventions, test locations, linters to run...
```

**With WADE** — same thing:

```bash
wade implement-task 42
```

That one command creates an isolated git worktree, opens your AI tool with the issue title, description, labels, and all project context pre-loaded. The AI already knows your project conventions — `wade init` installs Skill files that teach it.

Working on multiple issues simultaneously:

```bash
wade work batch 42 43 44   # three worktrees, three AI sessions, zero stashing
```

Done with an issue:

```bash
wade work done   # pushes branch, opens PR linked to the issue, cleans up worktree
```

## Quick Start

```bash
# Wire up your project once
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

## Commands

### Setup

| Command | Description |
|---------|-------------|
| `wade init` | Initialize WADE in the current project |
| `wade update` | Upgrade WADE and refresh managed project files |
| `wade deinit` | Remove WADE from the project |
| `wade check` | Check worktree status |
| `wade check-config` | Validate `.wade.yml` |

### Planning & Issues

| Command | Description |
|---------|-------------|
| `wade plan-task` | AI planning session — creates issues + draft PRs |
| `wade new-task` | Create a GitHub issue interactively |
| `wade task list` | List open issues |
| `wade task read <N>` | Show issue details |
| `wade task close <N>` | Close an issue |
| `wade task deps <N> <M> ...` | Analyze dependencies between issues |

### Implementation

| Command | Description |
|---------|-------------|
| `wade implement-task <N>` | Create worktree and start AI session for issue N |
| `wade implement-task <N> --detach` | Launch AI in a new terminal tab |
| `wade implement-task <N> --cd` | Create worktree, print path (no AI launch) |
| `wade work batch <N> <M> ...` | Start parallel sessions for multiple issues |

### Work Sessions

| Command | Description |
|---------|-------------|
| `wade work sync` | Sync feature branch with main |
| `wade work done` | Push branch and create PR |
| `wade work done --no-cleanup` | Keep the worktree after direct merge |
| `wade work list` | List active worktrees |
| `wade work remove <N>` | Remove a worktree |
| `wade work remove --stale` | Remove all stale worktrees |
| `wade work cd <N>` | `cd` into a worktree (requires shell integration) |

## Configuration

`wade init` creates a `.wade.yml` in your project root:

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

**Merge strategies:** `PR` pushes the branch and opens a Pull Request; `direct` merges into main locally.

**Complexity-based models:** Issues carry a `complexity` label (`easy`, `medium`, `complex`, `very_complex`). `wade implement-task` picks the right model automatically.

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

## Agent Skills

`wade init` installs Skill files into `.claude/skills/` that teach your AI agent the workflow:

| Skill | Purpose |
|-------|---------|
| `task` | GitHub issue creation and plan format |
| `plan-session` | Planning session rules and workflow |
| `work-session` | Implementation session rules and workflow |
| `deps` | Dependency analysis between issues |

Skills work with Claude Code natively. Symlinks are created for Copilot, Gemini, and Codex.

## Shell Integration

Add to your shell profile to enable `wade work cd`:

```bash
eval "$(wade shell-init)"
```

This lets `wade work cd <N>` change your shell's directory into the worktree. Without it, the command just prints the path.

Tab completion:

```bash
wade --install-completion bash   # or zsh / fish
```

## Upgrading

```bash
wade update
```

Detects your install method (`uv tool`, `pipx`, Homebrew) and upgrades automatically, then refreshes all managed project files.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
