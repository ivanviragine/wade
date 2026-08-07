# WADE — Workflow for AI-Driven Engineering

<p align="center">
  <img src="assets/wade.png" alt="WADE" width="250" />
</p>

**AI tools write the code. WADE handles everything else.**

*Every Wade does the dirty work so the heroes don't have to.*

Branches, worktrees, context loading, model selection, PR creation — all the workflow friction that surrounds AI coding sessions. WADE eliminates it. Works with `Claude Code`, `Copilot`, `Antigravity CLI`, `Codex`, and more. Run `wade init` once per project, then just point it at a GitHub *(more to come!)* issue number.

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
| `wade knowledge get` | Print the current project knowledge file |
| `wade knowledge rate` | Record a thumbs-up or thumbs-down for a knowledge entry |
| `wade knowledge enable [--path PATH]` | Enable knowledge capture and optionally set custom file path |
| `wade knowledge disable` | Disable knowledge capture (keeps existing knowledge file) |

Short aliases: `wade p` (plan), `wade i <N>` (implement), `wade r <N>` (review pr-comments).

Most workflow commands accept `--ai <tool>`, `--model <model>`, `--effort <level>`, `--permission-mode <tier>`, and `--yolo` to override configured defaults. `implement` also supports `--detach` (new terminal tab) and `--cd` (print worktree path only).

`--permission-mode` sets how much autonomy the AI tool is granted — an axis
independent of the delegation `--mode` (which controls *how* a tool is
dispatched: prompt/interactive/headless). The tiers, most→least permissive, are
`yolo` > `auto` > `accept-edits` > `default`:

| Tier | Behavior |
|------|----------|
| `default` | Prompt for every action (no autonomy grant). |
| `accept-edits` | Auto-apply file edits; still prompt for shell/commands. Claude and Antigravity CLI. |
| `auto` | Classifier-mediated auto mode (a model reviews each non-read action). Claude only. |
| `yolo` | Full autonomy — no prompts. |

`--yolo` is a back-compat alias for `--permission-mode yolo`; an explicit
`--permission-mode` wins when both are given. The same values are accepted in
`.wade.yml` as `ai.permission_mode` (global) or `ai.<command>.permission_mode`
(per-command), with `yolo: true` still honored as the alias. A tier a tool
doesn't support is downgraded automatically (e.g. `auto` → `accept-edits` on
non-Claude tools) with a warning — WADE forwards the requested tier and
[`crossby`](https://github.com/ivanviragine/crossby) owns the downgrade ladder.
Headless commands (`deps`, `review_*`) always run at `default`. `plan` is not a
permission mode — it's driven separately — and is rejected (warn + fall back to
`default`) if configured.

`wade plan --issue <N>` re-plans an existing issue. If the session produces a
single plan file, it's attached to `#N` and the issue stays open. If the
session decides the work should be split into several independent pieces
(2+ plan files), `#N` is **superseded**: a new issue + draft PR is created
per plan file, a comment and a `> **Superseded by ...**` banner are added to
`#N`, and it's closed as *not planned* (confirmed via prompt unless
`--yolo`/non-interactive). If any plan file fails to become an issue, `#N` is
left open with a warning instead of superseding on a partial split.

## Supported AI Tools

Adapters, model/effort resolution, and per-tool config (allowlists, hooks) are powered by [`crossby`](https://github.com/ivanviragine/crossby), WADE's AI-tool-integration dependency.

| Tool | Binary |
|------|--------|
| [Claude Code](https://claude.com/product/claude-code) | `claude` |
| [Cursor](https://www.cursor.com/) | `cursor` |
| [GitHub Copilot](https://github.com/features/copilot/cli) | `copilot` |
| [OpenAI Codex](https://developers.openai.com/codex/cli/) | `codex` |
| [OpenCode](https://opencode.ai/) | `opencode` |
| [VS Code](https://github.com/features/copilot/ai-code-editor) | `code` |
| [Antigravity IDE](https://antigravity.google/) | `antigravity` |
| [Antigravity CLI](https://antigravity.google/) | `agy` |

> In `.wade.yml`, refer to a tool by its **config ID**, which matches the binary
> above except for VS Code (`vscode`) and Antigravity CLI (`antigravity-cli`).
> Antigravity IDE (config ID `antigravity`) is launch-only — WADE opens your
> workspace in the desktop app; its workflow files are provisioned via the
> Antigravity CLI's shared `.agents/` layout.

### Session guards

When WADE creates a worktree it installs hooks into whichever tools your project
uses, so session rules are enforced in code rather than left to the agent to
honour. Guards are written per-worktree at session start — upgrading WADE is
enough to pick up improvements, with no re-init or migration.

| Guard | What it blocks |
|-------|----------------|
| Worktree containment | Writes that land outside your worktree |
| Plan-artifact | During `wade plan`, writes to anything but plan artifacts |
| Session completion | Finishing an implement/review session with unfinished work not yet run through `done` (nudges once) |
| Plan completion | Finishing a `wade plan` session that produced no valid `PLAN*.md` yet (nudges once) |

The session-completion guard keys on the same fact `done` records — a sha-keyed
`.wade/done@<HEAD>` marker written when `done`'s gates pass — so it nudges only
when the branch has commits ahead of its base **and** `done` has not finalized
the current commit. An early "stopping to ask a question" turn (no commits ahead)
never triggers it. The plan-completion guard is the planning counterpart: it
nudges once if a plan session is about to end with no valid plan file (a title
with a conventional-commit prefix plus a `## Complexity`). Both fail **open** — a
session is never trapped.

Independently of that nudge, `wade plan` now **strictly validates** plan files
before creating issues: a `PLAN*.md` missing a valid `## Complexity` or a
conventional-commit title is dropped with a loud error instead of silently
becoming an issue with no complexity label.

If some files pass and others fail, an interactive run asks whether to continue
with the valid ones; `--yolo` and non-interactive runs continue without asking.
If you decline, or if every plan file fails validation, no issues are created —
the generated `PLAN*.md` are preserved to a temp directory so you can fix the
reported errors and re-run instead of regenerating from scratch.

Both write guards cover **shell commands** as well as file edits, so a redirect
like `printf x > ../other-repo/app.py` is blocked, not just an `Edit` call. Shell
coverage is best-effort defense-in-depth: it catches ordinary commands, not
deliberate obfuscation (variable indirection, command substitution, subshells).

| Tool | Write guard | Session-completion guard |
|------|-------------|--------------------------|
| Claude Code | ✅ | ✅ |
| Cursor | ✅ | ✅ |
| GitHub Copilot | ✅ | ✅ |
| OpenAI Codex | ✅ shell (writes are sandboxed natively) | ✅ |
| Antigravity CLI | ✅ | ✅ |

The remaining supported tools (OpenCode, VS Code, Antigravity IDE) expose no hook
mechanism WADE can install into, so they receive neither guard — session rules
there rest on the skill files alone.

### Completion gates

`wade implementation-session done` (and `review-pr-comments-session done`) is the
authoritative completion gate: it refuses to finalize until the work is actually
ready, then writes the `.wade/done@<HEAD>` marker and pushes. Each gate has an
escape hatch under a `done:` block in `.wade.yml` (all default on):

| Gate | Refuses when… | Hatch |
|------|---------------|-------|
| PR-SUMMARY | `PR-SUMMARY.md` is missing, empty, or still a template placeholder (implementation only) | `done.require_pr_summary: false` |
| Sync | the branch is behind main — auto-syncs first, refuses only on conflict (implementation only) | `done.require_sync: false` |
| Review ran | `wade review implementation` did not run for the current commit | `--skip-review`, `done.require_review: false` (auto-off when `ai.review_implementation.enabled: false`) |
| Resolved threads | unresolved PR review threads remain (review-comments only) | `done.require_resolved_threads: false` |

A **pre-push git hook** (`done.pre_push_backstop`, default on) backs the gate up:
a push of the session branch without a current `.wade/done@<sha>` marker is
refused, so committing-and-pushing straight past `done` doesn't work. It is
worktree-scoped (never touches your main checkout or sibling worktrees) and
chains to any pre-existing `pre-push` hook. **Honesty:** `git push --no-verify`
bypasses the backstop in one flag — this is a quality/backstop layer that makes
the gate hard to skip, not an airtight boundary.

## Task Providers

WADE can pull tasks from three backends — pick one when you run `wade init`:

| Provider | Where issues live | Auth |
|----------|-------------------|------|
| `github` *(default)* | GitHub Issues | `gh` CLI |
| `clickup` | ClickUp list | API token in env var |
| `markdown` | A single committed `ISSUES.md` | None |

PRs always flow through GitHub regardless of choice — `wade fetch-reviews`,
the auto-poll loop, and review-thread resolution work identically across
providers.

### Markdown provider

Useful when you want issues versioned alongside the code, with no external
service. Each issue is one `##` heading in the file:

```markdown
# Wade Issues

## #47239185 Add login feature

<!-- wade
state: open
labels: feature, complexity:medium
-->

Description body here. Sub-headings, code blocks, anything markdown.
```

- The file is resolved against the **main worktree**, so every linked
  worktree reads/writes the same physical file. No textual merge conflicts
  on `ISSUES.md`.
- IDs are random 8-digit decimal so two parallel `wade implement` sessions
  in different worktrees can't collide. They stay numeric so existing
  `#NN` checklist refs in tracking-issue bodies still work.
- Configure via `.wade.yml`:

  ```yaml
  provider:
    name: markdown
    settings:
      path: ISSUES.md      # relative to repo root, or absolute
      auto_commit: false   # if true, close_task auto-commits the change
  ```

- After a PR merges, `provider.close_task` flips the section's `state` to
  `closed` in `ISSUES.md`. By default the file is left modified in your
  working tree — commit it yourself. Set `auto_commit: true` to have
  wade commit the change with a `chore: close #N` message; failures
  (not a git repo, hook rejection, signing required) are logged and
  swallowed so the close itself never blocks.
- Tracking issues with `- [ ] #N` checklist bodies work the same as with
  GitHub Issues — markdown's `find_parent_issue` scans every section's
  body for child refs.

## Agent Skills

wade installs Skills that teach your AI agent the workflow — issue format, planning rules, implementation session rules, and dependency analysis. Skills, the `AGENTS.md` workflow pointer, and any tool-specific configuration are set up automatically for every session (when you run `wade plan`/`wade implement`/`wade review`, or a standalone `wade task deps`), for every supported tool. Nothing to configure manually.

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

### Repo-quality gates

Three **opt-in, off-by-default** quality gates enforce code hygiene at the
commit boundary (and in-turn). Nothing is installed unless you configure it:

```yaml
hooks:
  pre_commit:
    lint: ./scripts/check.sh --lint   # runs on `git commit`; non-zero blocks it
    test: ./scripts/test.sh           # runs on `git commit`; non-zero blocks it
  commit_msg:
    conventional: true                # rejects a non-Conventional-Commit subject
  post_tool_use:
    enabled: true                     # feed lint findings back to the agent in-turn
    lint_cmd: ruff check              # FILE-SCOPED linter (the edited path is appended)
    timeout: 10                       # seconds; the linter is skipped on overrun
```

- **`pre_commit`** installs a per-worktree `pre-commit` git hook that runs the
  configured `lint`, then `test`, command(s). A non-zero exit blocks the commit
  and surfaces to the agent as a normal error. Set only the step(s) you want.
  Running the full `test` suite on every commit can be slow — prefer a fast
  subset there, or use `lint` alone.
- **`commit_msg`** installs a per-worktree `commit-msg` git hook that validates
  the subject line against [Conventional Commits](https://www.conventionalcommits.org/).
  A non-conforming subject blocks the commit. A `BREAKING CHANGE:` footer only
  marks an already-typed commit as breaking (an alternative to the subject `!`);
  it does not exempt the subject from the type requirement.
- **`post_tool_use`** feeds lint findings back to the agent *in-turn* — the
  cheapest moment to fix them, while the edit is still in working memory. It is
  **file-scoped**: `lint_cmd` runs on just the edited path (appended as a
  positional arg), on tools whose hooks can inject context back to the agent
  (Claude, Cursor, Codex, Copilot; Antigravity CLI is skipped). If `lint_cmd`
  is unset, wade falls back to `pre_commit.lint` run **whole-repo** on every
  edit — which is slower and may error on an unexpected positional arg, so
  prefer configuring a file-scoped `lint_cmd`. This layer never blocks.

Like the pre-push backstop, the git hooks are worktree-scoped (never touch your
main checkout or sibling worktrees) and chain to any pre-existing hook of the
same name. **Honesty:** `git commit --no-verify` bypasses the pre-commit and
commit-msg hooks in one flag — these are **quality** gates that make messy or
untested commits hard to land, not airtight enforcement boundaries.

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
