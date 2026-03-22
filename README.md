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

Finishing work and opening the PR:

*Without* WADE:

```bash
git checkout main && git pull
git checkout feat/issue-42-user-auth
git merge main          # resolve conflicts yourself, if any
git push
gh pr create --title "User Auth (#42)" --body "..."   # write description manually
# don't forget to link the issue, clean up the branch...
```

*With* WADE (the AI handles all of this):

```bash
wade implementation-session done
```

The AI merges the latest main into the branch, resolves any conflicts, writes the PR description from what it built, and marks it ready for review — you get a clean, already-integrated diff with no noise for your reviewer.

Working on multiple issues at once:

```bash
wade implement-batch 42 43 44   # three worktrees, three AI sessions, zero stashing
```

## Why WADE

| Without WADE | With WADE |
|---|---|
| `git fetch && checkout && pull && checkout -b ...` before every task | `wade 42` — one command, done |
| Copy-paste issue title + description into AI chat every time | Full issue context + project conventions loaded automatically |
| One task at a time, or stash-juggle between branches | Parallel issues in isolated worktrees, zero conflicts |
| Re-explain your branching rules, test commands, linters every session | Skills teach the AI your conventions once |
| PRs opened on stale branches — reviewer sees conflict noise, asks for a rebase, CI fails | AI merges the latest main and resolves conflicts before the PR — reviewer sees only your changes |
| Write the PR description, link the issue, clean up the branch — manually | The AI ships the PR. You just review |
| Manually pick the right model for each task | Automatic model routing based on issue complexity |
| Which terminal tab has which issue? No idea | Terminal title shows `wade implement #42 — Feature Name` |
| Which AI session worked on this issue? Which tool? Which model? | Every PR and issue logs the tool, model, and session resume command — for both Plan and Implement phases |

## Installation

One-line install script (installs [`uv`](https://docs.astral.sh/uv/) automatically if missing):

```bash
curl -LsSf https://raw.githubusercontent.com/ivanviragine/wade/main/install.sh | sh
```

Or, if you already have `uv` or `pipx`:

```bash
uv tool install wade-cli    # recommended
pipx install wade-cli
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
wade plan

# Start working — WADE detects plan state and picks the right session automatically
wade 42
```

## Commands

| Command | Description |
|---------|-------------|
| `wade <N>` | Smart shorthand — routes to implement or review pr-comments automatically |
| `wade plan` | AI planning session — creates issues + draft PRs |
| `wade implement <N>` | Create worktree and start AI session for an issue |
| `wade implement-batch <N> <M> ...` | Start parallel sessions for multiple issues *(beta)* |
| `wade review pr-comments <N>` | Address PR review comments |
| `wade review plan <file>` | AI-powered plan review |
| `wade review implementation` | AI-powered code review |
| `wade review batch <N>` | Coherence review across parallel implementation branches |
| `wade cd <N>` | Navigate to a worktree (requires shell integration) |
| `wade task create` | Create a GitHub issue interactively |
| `wade task list` | List open issues |
| `wade task read <N>` | Show issue details |
| `wade task deps <N> <M> ...` | Analyze dependencies between issues |
| `wade worktree list` | List active worktrees |
| `wade worktree remove <N>` | Remove a worktree |
| `wade init` | Initialize WADE in the current project |
| `wade update` | Upgrade WADE and refresh project files |
| `wade deinit` | Remove WADE from the current project |
| `wade check-config` | Validate `.wade.yml` configuration |
| `wade knowledge add` | Append a project learning from stdin |

Short aliases: `wade p` (plan), `wade i <N>` (implement), `wade r <N>` (review pr-comments).

Most workflow commands accept `--ai <tool>`, `--model <model>`, `--effort <level>`, and `--yolo` to override configured defaults. `implement` also supports `--detach` (new terminal tab) and `--cd` (print worktree path only).

## Supported AI Tools

| Tool | Binary |
|------|--------|
| [Claude Code](https://claude.com/product/claude-code) | `claude` |
| [Cursor](https://www.cursor.com/) | `cursor` |
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
| `implementation-session` | Implementation session rules and workflow |
| `review-pr-comments-session` | Review session rules and workflow |
| `deps` | Dependency analysis between issues |

## Worktree Hooks

Configure automated setup when worktrees are created via `wade implement` or `wade implement-batch`. Add a `hooks` section to `.wade.yml`:

```yaml
hooks:
  post_worktree_create: scripts/setup-worktree.sh
  copy_to_worktree:
    - .env
    - .env.example
```

**`post_worktree_create`** — A setup script to run after each worktree is created. Use it to install dependencies, run builds, or prepare the environment so worktrees are ready to use immediately.

**`copy_to_worktree`** — Files to copy from the project root into the worktree before running the hook. Useful for secrets and config files (e.g., `.env`) that are gitignored and wouldn't otherwise be present in a new worktree.

See [`templates/setup-worktree.sh.example`](templates/setup-worktree.sh.example) for a starter script.

## Shell Integration

To make `wade cd <N>` actually change your directory (instead of just printing the path), add this to your shell profile:

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
