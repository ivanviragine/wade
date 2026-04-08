# What's new in WADE since v0.0.2

> 5 weeks. 130+ releases. Here's what shipped.

v0.0.2 already had the core: `wade 42` routing, `wade init`, AI planning, implementation with worktrees, batch mode, 7 AI tool adapters, skills, dependency analysis, terminal titles, model routing, and session logging.

Everything below is **new**.

---

## AI self-review of plans and code

Before v0.0.2, the AI planned and implemented — but never checked its own work. Now every session includes a built-in review step.

**Plan review**: after the AI writes a plan, WADE delegates a second pass to critique it for gaps, missing edge cases, and scope creep — before any issues get created.

**Code review**: before creating a PR, the AI reviews its own implementation for correctness, style, and completeness.

Three delegation modes:
- **Prompt** — generates a structured review for the developer to inspect
- **Headless** — a second AI session reviews automatically in the background, no terminal needed
- **Interactive** — launches a new AI session in a separate terminal for a deeper review

Issues get caught before human reviewers ever see the PR.

## Address PR review comments with AI

Entirely new workflow. When reviewers leave comments on your PR, run `wade 42` → "Review PR comments." WADE fetches all unresolved threads and launches an AI session to address each one.

- Commit-aware polling — tracks which comments are new since the last push
- Catches outdated threads and PR-level reviews
- Detects bot reviews (CodeRabbit, etc.) so the AI doesn't wait for a human that already approved
- Review enforcement — `done` blocks until reviews are addressed

## Post-batch coherence review

After batch mode finishes parallel tasks, `wade review batch` checks that the branches actually work together — catches integration conflicts, duplicated logic, and inconsistent patterns across parallel PRs.

## Cross-session knowledge

WADE maintains a project knowledge file that AI agents read at session start and write to at session end. Learnings persist:

- "Our API always returns wrapped responses" — saved once, known forever
- Rate entries with thumbs-up/down to surface what's useful
- Outdated entries get superseded, not deleted

The AI gets smarter about your codebase with every task it completes.

## Chain auto-continuation

Batch mode now understands dependency chains. If task B depends on task A, WADE runs A first, waits for its PR to merge, then automatically starts B on top of A's changes — no manual intervention.

## Context-aware smart-start

`wade 42` now shows a context-aware menu based on where you actually are:

- No PR yet → start implementing
- PR in draft → continue working or resume last session
- PR open with reviews → address review comments
- PR approved → merge

It also detects tracking issues and redirects to batch mode automatically.

## YOLO mode

`--yolo` skips all confirmation prompts — AI tool selection, model choice, effort level. Fully autonomous from `wade 42` to merged PR. Propagates through batch mode and chain continuation.

## Effort control

`--effort` flag (or `WADE_EFFORT` env var) controls how hard the AI thinks. Route simple tasks to fast models with low effort, complex tasks to powerful models with max reasoning.

## Plan validation

`wade plan-session done` validates plans deterministically before creating issues — checks structure, completeness, conventional commit titles. Errors block; warnings inform. No more malformed plans slipping through.

## File-write guards

During planning sessions, WADE hooks prevent the AI from accidentally writing code. Planning is for thinking, not implementing. Guards fail open on errors so they never block real work.

## Zero repo pollution

All WADE artifacts — the AGENTS.md pointer, AI tool settings, skill files, session data — now live exclusively in the worktree. Nothing gets committed to your main branch. Your repo stays clean.

## Auto-sync at session start

When starting or resuming implementation, WADE merges the latest base branch into the worktree and handles conflicts — before the AI writes a single line. No more building on stale code.

## ClickUp provider

GitHub Issues is no longer the only option. ClickUp works as a task backend with the same workflow. Same `wade 42`, different provider.

## Session resume

Every PR footer now records the AI tool, model, and session ID. When you come back to a task, "Resume last session" picks up exactly where the AI left off — full context preserved.

---

*Shipped between March 4 and April 8, 2026.*
