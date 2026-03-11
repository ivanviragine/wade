---
name: review-pr-comments-session
description: >
  Rules for AI sessions that address PR review comments. Covers fetching
  review comments, verifying findings, making fixes, and pushing changes.
  Read this at the start of every review-addressing session.
---

# Review PR Comments Session Rules

These rules govern AI sessions that address PR review comments.
Read and follow them before performing any other action.

## Execution mode

Run `wade` and `gh` commands with the required permissions/capabilities (not
in sandboxed mode). Do not "try sandbox first" — run them unsandboxed from the
start.

## Transparency

Always inform the user before running `wade` commands, reviews, or
session lifecycle operations. Clearly state what you are about to do
and why — never silently execute these commands.

## Never use `gh issue create`

**NEVER** use `gh issue create` or the GitHub API to create issues directly.
Always use `wade task create` for interactive issue creation.

## First action: check your context

Run `wade review-pr-comments-session check` as your **first action**:

- `IN_WORKTREE` — you may proceed with work (code changes, commits, etc.)
- `IN_MAIN_CHECKOUT` — **editing any source file is forbidden**. Tell the
  human to run `wade review pr-comments <issue>` from the main checkout.
- `NOT_IN_GIT_REPO` — you are not inside a git repository.

## Fetching review comments

Use `wade review-pr-comments-session fetch <issue-number>` to fetch all unresolved PR review
comments. This outputs formatted markdown with:
- Comments grouped by file
- CodeRabbit AI-agent prompts extracted and highlighted
- Thread IDs for resolution

**Run this command first** to understand what needs to be addressed.

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

## Resolving threads

After addressing a review comment, resolve the corresponding thread on GitHub:

```bash
wade review-pr-comments-session resolve <thread-node-id>
```

The thread ID is included in the output of `wade review-pr-comments-session fetch`.

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
- **Do NOT create new PRs** — push to the existing branch

## Syncing with main

Before finalizing, sync your branch with main:

```bash
wade review-pr-comments-session sync --json
```

### Handling sync results

**Exit code 0 — Success**: Branch is up to date with main. Proceed to closing.

**Exit code 2 — Conflict**: The merge is paused due to conflicts:
1. Run `git diff --name-only --diff-filter=U` to list conflicted files
2. Read each conflicted file — understand both sides of the conflict
3. Resolve the conflict markers in each file
4. Stage only the resolved files: `git add <file1> <file2> ...`
5. Complete the merge: `git commit --no-edit`
6. Re-run `wade review-pr-comments-session sync --json` to verify clean

**Exit code 4 — Pre-flight failure**: Report the issue and suggest how to fix it.

## PR summary

Before closing the session, write **`PR-SUMMARY.md`** in the worktree root.
`wade review-pr-comments-session done` reads this file to update the PR body.

> **Never commit this file** — it is a session artifact (already in `.gitignore`).

### Format

```markdown
## What was addressed
[Summary of review comments handled]

## Changes
- Fixed X per reviewer feedback
- Resolved CodeRabbit finding on Y

## Remaining
[Any threads intentionally left unresolved, with reasoning]
```

## Closing the session

**NEVER** create Pull Requests manually (`gh pr create`) or push branches
directly.

To finalize your work, follow these steps in order:

**Step 1 — Write PR summary:**

Write `PR-SUMMARY.md` in the worktree root (see format above). If the file
already exists, update it.

**Step 2 — Sync with main:**

```bash
wade review-pr-comments-session sync --json
```

**Step 3 — Done:**

```bash
wade review-pr-comments-session done
```

`wade review-pr-comments-session done` pushes changes to the existing PR branch.

This is a **mandatory** final step. If it fails, debug and fix the error —
do NOT bypass it.

**Step 4 — Review with the user:**

Present the PR link and a brief recap of what was addressed. Ask if they'd
like any further changes — apply them and repeat Steps 1–3 if so, or confirm
the session is complete if not.

## Skills reference

- **About to create GitHub Issues** → read @.claude/skills/task/SKILL.md first
