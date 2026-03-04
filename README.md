# WADE — Workflow for AI-Driven Engineering

<p align="center">
  <img src="assets/wade.png" alt="WADE" width="250" />
</p>

**AI tools write the code. WADE handles everything else.**

*Every Wade does the dirty work so the heroes don't have to.*

Branches, worktrees, context loading, model selection, PR creation — all the workflow friction that surrounds AI coding sessions. WADE eliminates it. Works with `Claude Code`, `Copilot`, `Gemini`, `Codex`, and more. Run `wade init` once per project, then just point it at a GitHub *(more to come!)* issue number.

## See It in Action

Starting work on Issue #42:

*Without* WADE:

```bash
git fetch origin && git checkout main && git pull
git checkout -b feat/issue-42-user-auth
# paste issue title + description into AI chat
# explain your branching rules, test locations, linters to run...
```

*With* WADE:

```bash
wade 42
```

WADE fetches the issue, detects whether it's been planned, and starts the right session automatically — planning if no plan exists yet, implementation if it does. Creates an isolated git worktree, launches your AI tool with the full issue loaded — title, description, labels, and all your project conventions — and Skills guide the AI from first commit to open PR without you touching git again.

Working on multiple issues at once:

```bash
wade work batch 42 43 44   # three worktrees, three AI sessions, zero stashing
```

## Why WADE

| Without WADE | With WADE |
|---|---|
| `git fetch && checkout && pull && checkout -b ...` before every task | `wade 42` — one command, done |
| Copy-paste issue title + description into AI chat every time | Full issue context + project conventions loaded automatically |
| One task at a time, or stash-juggle between branches | Parallel issues in isolated worktrees, zero conflicts |
| Re-explain your branching rules, test commands, linters every session | Skills teach the AI your conventions once |
| Forget to sync with main before opening a PR — merge conflicts for the reviewer | Branch synced with main automatically before every PR |
| Write the PR description, link the issue, clean up the branch — manually | The AI ships the PR. You just review |
| Manually pick the right model for each task | Automatic model routing based on issue complexity |
| Which terminal tab has which issue? No idea | Terminal title shows `wade work #42 — Feature Name` |

## Installation

One-line install script (installs [`uv`](https://docs.astral.sh/uv/) automatically if missing):

```bash
curl -LsSf https://raw.githubusercontent.com/ivanviragine/wade/main/install.sh | sh
```

Or, if you already have `uv` or `pipx`:

```bash
uv tool install wade    # recommended
pipx install wade
```

Requires [gh CLI](https://cli.github.com/) (authenticated) and at least one supported AI coding tool.

```bash
gh auth login    # if not already authenticated
```

## Quick Start

Initialize WADE in your project (once):

```bash
wade init
```

Then start working:

```bash
# Plan a feature — AI creates GitHub issues and draft PRs
wade plan-task

# Start working — WADE detects plan state and picks the right session automatically
wade 42
```

## Commands

| Command | Description |
|---------|-------------|
| `wade <N>` | Smart shorthand — routes to plan or implement automatically |
| `wade work batch <N> <M> ...` | Start parallel sessions for multiple issues *(beta)* |
| `wade plan-task` | AI planning session — creates issues + draft PRs |
| `wade implement-task <N>` | Create worktree and start AI session for an issue |
| `wade new-task` | Create a GitHub issue interactively |
| `wade task list` | List open issues |
| `wade task read <N>` | Show issue details |
| `wade work list` | List active worktrees |
| `wade work remove <N>` | Remove a worktree |
| `wade init` | Initialize WADE in the current project |
| `wade update` | Upgrade WADE and refresh project files |

`plan-task` and `implement-task` accept `--ai <tool>` and `--model <model>` to override the configured defaults. `implement-task` also supports `--detach` (new terminal tab) and `--cd` (print worktree path only).

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
