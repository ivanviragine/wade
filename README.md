# WADE — Workflow for AI-Driven Engineering

<img src="assets/wade.png" alt="WADE" width="600" />

Turn GitHub Issues into isolated, AI-powered development sessions — with one command.

WADE works with all major AI coding tools — Claude Code, Copilot, Gemini, Codex, and more — and automatically dispatches the right tool and model for each job. Run `wade init` once per project and it wires up everything: Skill files, `AGENTS.md` workflow pointer, and tool-specific configs for whichever AI tools you use. Then just point it at an issue number.

## Why WADE

| Without WADE | With WADE |
|---|---|
| Copy-paste issue details into AI chat | AI reads the issue automatically |
| Create branch, remember naming convention | `wade implement-task 42` handles it |
| One task at a time (or messy stash juggling) | Parallel tasks in isolated git worktrees |
| Manually write PR, link issue, clean up branch | Skills guide the AI to do it automatically |
| Re-explain project conventions to AI every time | Skills teach the AI how your project works |
| Configure skills and AGENTS.md per tool manually | `wade init` wires up all AI tools automatically |
| Pick tool and model manually every time | WADE selects both based on task type and complexity |

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

From there the AI session is self-contained: the installed Skills guide the AI to sync with main when needed, commit, push the branch, and open a linked PR — all without leaving the session.

### Right tool and model for each job

WADE dispatches based on command type and issue complexity — no manual selection needed:

| Situation | What WADE launches |
|---|---|
| Planning a new feature | Claude Opus — best reasoning for architecture decisions |
| Implementing a complex task | Claude Sonnet — solid execution at reasonable cost |
| Fixing a simple bug or config tweak | Claude Haiku — fast and cheap |
| Analyzing issue dependencies | Codex — dedicated code and graph analysis |

Tool and model are fully configurable per phase and complexity level in `.wade.yml`.

### One-time project setup

`wade init` runs **once per project**. It creates `.wade.yml`, installs Skill files into `.claude/skills/`, and wires up the git workflow. After that, the only commands you type day-to-day are `wade plan-task` and `wade implement-task`.

### Parallel work sessions

```bash
wade work batch 42 43 44   # three worktrees, three AI sessions, zero stashing
```

## Quick Start

```bash
# ── One-time project setup ──────────────────────────────────────────────────
wade init

# ── Daily workflow ───────────────────────────────────────────────────────────

# Plan a feature with AI — creates GitHub issues + draft PRs
wade plan-task

# Start working on an issue — WADE picks the right tool and model automatically
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

These are called by the AI skill during an implementation session — not by the user directly.

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
  default_tool: claude
  plan:
    tool: claude            # tool used for wade plan-task
  work:
    tool: claude            # tool used for wade implement-task

models:
  claude:
    easy: claude-haiku-4-5-20251001
    medium: claude-sonnet-4-6
    complex: claude-sonnet-4-6
    very_complex: claude-opus-4-6
```

**Merge strategies:** `PR` pushes the branch and opens a Pull Request; `direct` merges into main locally.

**Complexity-based model dispatch:** Issues carry a `complexity` label (`easy`, `medium`, `complex`, `very_complex`). `wade implement-task` reads it and launches the matching model automatically — no flags needed.

**Per-phase tool selection:** Planning sessions and implementation sessions can use different AI tools. Set `ai.plan.tool` and `ai.work.tool` independently.

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

`wade init` installs Skill files that teach your AI agent the workflow:

| Skill | Purpose |
|-------|---------|
| `task` | GitHub issue creation and plan format |
| `plan-session` | Planning session rules and workflow |
| `work-session` | Implementation session rules and workflow |
| `deps` | Dependency analysis between issues |

Skills, `AGENTS.md` workflow pointer, and any tool-specific configuration are set up automatically for all supported AI tools — you don't configure anything per tool manually.

## Shell Integration

By default, `wade work cd <N>` just prints the worktree directory path. To actually `cd` into it, add this to your shell profile (`.bashrc`, `.zshrc`, or equivalent):

```bash
eval "$(wade shell-init)"
```

**Why is this needed?** When a program runs in a subprocess, it can't change your shell's working directory — only the shell itself can do that with the `cd` builtin. This command installs a shell function that intercepts `wade work cd` calls and performs the actual `cd` in your shell, so you end up in the worktree instead of staying put.

**Without shell integration:** `wade work cd 5` prints `/path/to/worktree`, but you stay in your current directory.

**With shell integration:** `wade work cd 5` changes your directory to the worktree (just like running `cd` manually).

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
