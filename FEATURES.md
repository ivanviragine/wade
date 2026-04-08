# What's new in WADE (v0.0.2 → v0.15.15)

> 5 weeks. 130+ releases. Here's what shipped.

---

## One command to start working: `wade 42`

Type an issue number and WADE figures out the rest. It detects where you left off — no PR yet? Start implementing. PR in draft? Continue working. Reviews pending? Address them. Ready to merge? One click.

The smart-start menu adapts to context: it knows your PR state, your worktree, whether you have an active session to resume. You pick what to do; WADE handles git, branches, and isolation.

## AI plans before it codes

`wade plan` opens a planning session where AI analyzes the feature, breaks it into tasks, and writes structured plan files — before touching any code. Plans go through **automated self-review**: WADE delegates a second AI pass to critique the plan for gaps, edge cases, and scope creep.

When planning is done, WADE validates the output (structure, completeness, conventional commit titles), auto-creates GitHub Issues for each task, and offers to start implementation immediately.

## AI reviews its own work

Every implementation session includes a built-in review step. Before creating a PR, the AI reviews its own code for correctness, style, and completeness.

Three review modes:
- **Self-review prompt** — AI generates a structured review checklist the developer can inspect
- **Headless delegation** — a second AI session reviews the code automatically in the background, no terminal needed
- **Interactive delegation** — launches a new AI session in a separate terminal for a deeper, conversational review

This catches issues before human reviewers ever see the PR.

## Address PR reviews with one command

When reviewers leave comments on your PR, run `wade 42` and pick "Review PR comments." WADE fetches all unresolved threads — including outdated ones, PR-level reviews, and bot reviews (CodeRabbit, etc.) — and launches an AI session to address each one. It tracks which comments are new since the last commit so nothing gets missed.

## Batch mode: parallelize entire features

`wade implement-batch` takes a tracking issue and launches all sub-tasks in parallel — each in its own terminal, its own worktree, its own branch.

- Detects dependency chains automatically ("task B depends on A")
- Independent tasks run in parallel; chains run sequentially with auto-continuation
- Live polling dashboard shows progress across all tasks
- Post-batch coherence review checks that parallel branches work together

Turn a 10-task feature into 10 simultaneous AI sessions.

## Works with your AI tool

WADE is not locked to one AI. It supports **Claude Code, Cursor, Gemini CLI, OpenAI Codex**, and more — with adapter-level integration for each.

- Pick your tool per-project or per-command
- Configure model tiers (fast model for simple tasks, powerful model for complex ones)
- Control reasoning effort with `--effort` (or `WADE_EFFORT` env var)
- **YOLO mode** (`--yolo`): skip all confirmation prompts for fully autonomous runs
- Headless execution for CI/CD pipelines

Switch tools mid-project without changing your workflow.

## Cross-session knowledge

WADE maintains a project knowledge file that AI agents read at the start of every session and write to at the end. Learnings persist across sessions:

- "Our API always returns wrapped responses" — saved once, known forever
- Rate entries with thumbs-up/down to surface what's useful
- Outdated knowledge gets superseded, not deleted

The AI gets smarter about your codebase with every task it completes.

## Worktree isolation & safety guardrails

Every task runs in its own git worktree — fully isolated from your main branch and from other tasks. WADE adds additional safety layers:

- **File-write guards** prevent AI from modifying files outside the worktree during planning sessions
- **Auto-sync** merges the latest base branch into your worktree at session start, handling conflicts
- **Review enforcement** blocks `done` until reviews are addressed
- **Zero repo pollution** — all WADE artifacts live in the worktree only, never committed to your main branch
- **Session resume** — every PR records the session ID so you can pick up exactly where you left off

## Multi-provider support

GitHub Issues is the default, but WADE also supports **ClickUp** as a task provider. Same workflow, different backend. More providers can be added through the pluggable provider architecture.

## One-time setup: `wade init`

Interactive wizard that configures everything in one pass:

- Detects installed AI tools and sets defaults
- Configures worktree directory, branch naming, labels
- Sets up post-worktree hooks (install deps, copy `.env`, etc.)
- Enables cross-session knowledge
- Installs shell completions
- Writes a single `.wade.yml` config — re-run anytime to update

---

*130+ releases shipped between March 4 and April 8, 2026.*
