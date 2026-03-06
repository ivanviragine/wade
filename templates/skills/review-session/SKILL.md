---
name: review-session
description: >
  Rules for AI sessions that address PR review comments. Covers reading
  review comments, verifying findings, making fixes, and pushing changes.
  Read this at the start of every review-addressing session.
---

# Review Session Rules

These rules govern AI sessions that address PR review comments.
Read and follow them before performing any other action.

## Execution mode

Run `wade` and `gh` commands with the required permissions/capabilities (not
in sandboxed mode). Do not "try sandbox first" — run them unsandboxed from the
start.

## Never use `gh issue create`

**NEVER** use `gh issue create` or the GitHub API to create issues directly.
Always use `wade new-task` for interactive issue creation.

## First action: check your context

Run `wade check` as your **first action**:

- `IN_WORKTREE` — you may proceed with work (code changes, commits, etc.)
- `IN_MAIN_CHECKOUT` — **editing any source file is forbidden**. Tell the
  human to run `wade address-reviews <issue>` from the main checkout.
- `NOT_IN_GIT_REPO` — you are not inside a git repository.

## Read the review comments

Your primary input is **`REVIEW-COMMENTS.md`** in the worktree root. This
file contains all unresolved PR review comments, grouped by file.

Read it carefully before making any changes.

## Addressing comments

### Verify before fixing

**Always verify each finding against the current code before fixing it.**
Automated review tools (CodeRabbit, etc.) can be wrong — they may flag code
that is actually correct, or suggest changes that don't apply.

1. Read the referenced file and line
2. Understand the reviewer's concern
3. Decide if the concern is valid
4. If valid: fix it. If not: skip it (optionally note why in your commit message)

### CodeRabbit comments

CodeRabbit comments include a `🤖 Prompt for AI Agents` section — this is
the primary instruction to follow. The full comment body provides additional
context (rationale, proposed diffs, etc.) but the AI-agent prompt is the
actionable instruction.

Severity indicators:
- 🟠 **Major** — likely a bug or significant issue. Prioritize these.
- 🔵 **Trivial** — style or minor improvement. Fix if straightforward.

### Human comments

Human reviewer comments use the full comment body as the instruction.
Follow reviewer intent — ask clarifying questions in your commit message
if the request is ambiguous.

### Grouping changes

Address comments file-by-file or in logical groups. Each commit should be
cohesive — don't mix unrelated fixes.

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/) format:

- `fix: address review comment — <brief description>`
- `fix: address review comments` (for multiple related fixes)
- `refactor: address review feedback on <component>`

## Testing

Run the project's test suite after making changes to ensure nothing is broken.
Add tests if a review comment identified a missing test case.

## What NOT to do

- **Do NOT implement new features** — only address review comments
- **Do NOT make unrelated changes** — stay focused on the review feedback
- **Do NOT resolve review threads on GitHub** — the reviewer will do that
- **Do NOT create new PRs** — push to the existing branch

## Syncing with main

Before finalizing, sync your branch with main:

```bash
wade work sync --json
```

Handle conflicts as described in the work-session skill.

## Closing the session

**NEVER** create Pull Requests manually (`gh pr create`) or push branches
directly.

To finalize your work:

1. Write `PR-SUMMARY.md` in the worktree root describing what you addressed
2. Run `wade work sync --json`
3. Run `wade work done`

`wade work done` pushes changes to the existing PR branch.

## Skills reference

- **About to create GitHub Issues** → read @.claude/skills/task/SKILL.md first
