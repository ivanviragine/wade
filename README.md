# WADE — Workflow for AI-Driven Engineering

<p align="center">
  <img src="assets/wade.png" alt="WADE" width="250" />
</p>

**AI tools write the code. WADE handles everything else.**

Branches, worktrees, context loading, model selection, PR creation — all the workflow friction that surrounds AI coding sessions. WADE eliminates it. Works with Claude Code, Copilot, Gemini, Codex, and more. Run `wade init` once per project, then just point it at an issue number.

## See It in Action

Starting work on Issue #42 — without WADE:

```bash
git fetch origin && git checkout main && git pull
git checkout -b feat/issue-42-user-auth
# paste issue title + description into AI chat
# explain your branching rules, test locations, linters to run...
```

With WADE:

```bash
wade implement-task 42
```

WADE creates an isolated git worktree, launches your AI tool with the full issue loaded — title, description, labels, and all your project conventions — and Skills guide the AI from first commit to open PR without you touching git again.

Working on multiple issues at once:

```bash
wade work batch 42 43 44   # three worktrees, three AI sessions, zero stashing
```

## Why WADE

| The old way | With WADE |
|---|---|
| Paste issue context into every AI session | Full issue + project conventions loaded automatically |
| Five git commands before you even start | `wade implement-task 42` |
| One task at a time — or stash juggling | Parallel issues in isolated worktrees, zero conflicts |
| Write PR, link issue, clean up branch — manually | The AI ships the PR. You just review. |
| Re-explain project conventions every session | Skills teach the AI once. It knows forever. |
| Manually pick model — overpay or underpower | Haiku for fixes, Sonnet for features, Opus for architecture |
| Configure skills and tools per AI tool manually | Every tool wired up automatically by `wade init` |

## Installation

```bash
curl -LsSf https://raw.githubusercontent.com/ivanviragine/wade/main/install.sh | sh
```

Requires [gh CLI](https://cli.github.com/) (authenticated) and at least one supported AI coding tool. Python and all dependencies are managed automatically by `uv`.

```bash
gh auth login    # if not already authenticated
```

Or install manually:

```bash
uv tool install wade
pipx install wade
```

## Quick Start

```bash
# Once per project
wade init

# Plan a feature — AI creates GitHub issues and draft PRs
wade plan-task

# Start working — WADE picks the right tool and model automatically
wade implement-task 42
```

## Commands

### Setup

| Command | Description |
|---------|-------------|
| `wade init` | Initialize WADE in the current project (once per project) |
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

### Worktree Management

| Command | Description |
|---------|-------------|
| `wade work list` | List active worktrees |
| `wade work remove <N>` | Remove a worktree |
| `wade work remove --stale` | Remove all stale worktrees |
| `wade work cd <N>` | `cd` into a worktree (requires shell integration) |

### AI-Invoked Commands

Called by the AI skill during a session — not by the user directly.

| Command | Description |
|---------|-------------|
| `wade work sync` | Sync feature branch with main |
| `wade work done` | Push branch and create PR |
| `wade work done --no-cleanup` | Keep the worktree after direct merge |

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
  plan:
    tool: claude            # tool for wade plan-task
  work:
    tool: claude            # tool for wade implement-task

models:
  claude:
    easy: claude-haiku-4-5-20251001
    medium: claude-sonnet-4-6
    complex: claude-sonnet-4-6
    very_complex: claude-opus-4-6
```

`wade implement-task` reads the issue's `complexity` label and launches the matching model automatically. Planning and implementation sessions can use different tools. Merge strategy `PR` opens a pull request; `direct` merges locally.

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

`wade init` installs Skills that teach your AI agent the workflow — issue format, planning rules, implementation session rules, and dependency analysis. Skills, the `AGENTS.md` workflow pointer, and any tool-specific configuration are set up automatically for every supported tool. Nothing to configure manually.

| Skill | Purpose |
|-------|---------|
| `task` | GitHub issue creation and plan format |
| `plan-session` | Planning session rules and workflow |
| `work-session` | Implementation session rules and workflow |
| `deps` | Dependency analysis between issues |

## Shell Integration

To make `wade work cd <N>` actually change your directory (instead of just printing the path), add this to your shell profile:

```bash
eval "$(wade shell-init)"
```

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
