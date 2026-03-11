---
name: implementation-session
description: >
  Rules for AI implementation sessions in a wade-managed worktree. Covers
  worktree safety, commit conventions, syncing with main, PR summaries, and
  session closing. Read this at the start of every work session.
---

# Implementation Session Rules

These rules govern AI implementation sessions in a wade-managed project.
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
Using `gh` directly bypasses label enforcement, snapshot/diff detection, and
dependency analysis hooks.

## First action: check your context

Run `wade implementation-session check` as your **first action**:

- `IN_WORKTREE` — you may proceed with work (code changes, commits, etc.)
- `IN_MAIN_CHECKOUT` — **editing any source file is forbidden, even before
  committing**. Tell the human to create a worktree first via
  `wade implement`.
- `NOT_IN_GIT_REPO` — you are not inside a git repository.

## Worktree safety

All **code changes** (edits, new files, commits) **must** happen in a worktree.
The human creates worktrees via `wade implement` (single issue) or
`wade implement-batch` (multiple issues in parallel). **Never** create worktrees
yourself.

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/) format.
Prefer `git commit` (which opens an editor) over `git commit -m` for
multi-line messages.

## PR summary

Before closing the session, write **`PR-SUMMARY.md`** in the worktree root
(your current working directory). `wade implementation-session done` reads this file to populate
the PR body. If the file is missing, the PR will have no description.

> **Never commit this file** — it is a session artifact (already in `.gitignore`).
> If you find it is already tracked by git (e.g. `git status` shows it as modified),
> untrack it first:
>
> ```bash
> git rm --cached PR-SUMMARY.md
> git commit -m "chore: untrack PR-SUMMARY.md (already gitignored)"
> ```
>
> Then re-write the file — it will be ignored going forward.

### What to include

1. **What was accomplished** — high-level summary of changes
2. **Why these changes** — context from the issue/plan
3. **Key technical decisions** — important implementation choices
4. **What was tested** — how you verified the changes work

### Format

```markdown
## What was done
[High-level summary in 2-3 sentences]

## Changes
- Added X to improve Y
- Modified Z to handle edge case W

## Testing
- Tested scenario A: result
- Ran test suite: all passing

## Notes for reviewers
[Optional: anything the reviewer should know]
```

## Syncing with main

Before finalizing, sync your branch with main.

### Step 1: Commit uncommitted work

```bash
git status --porcelain
```

If there is output, stage and commit your changes before syncing.

### Step 2: Run the sync command

```bash
wade implementation-session sync --json
```

### Step 3: Handle the result

**Exit code 0 — Success**: Branch is up to date with main. Proceed to closing.

**Exit code 2 — Conflict**: The merge is paused due to conflicts:
1. Run `git diff --name-only --diff-filter=U` to list conflicted files
2. Read each conflicted file — understand both sides of the conflict
3. Resolve the conflict markers in each file
4. Stage only the resolved files: `git add <file1> <file2> ...`
5. Complete the merge: `git commit --no-edit`
6. Re-run `wade implementation-session sync --json` to verify clean

**Exit code 4 — Pre-flight failure**: Report the issue (dirty worktree, not
in repo, already on main) and suggest how to fix it.

**Never re-implement git operations yourself.** Always use `wade implementation-session sync`.

## Closing the session

**NEVER** create Pull Requests manually (`gh pr create`) or push branches
directly.

To finalize your work, follow these steps in order:

**Step 1 — Self-review:**

Run `wade review implementation` to catch issues early. The command checks your
project config and skips if reviews are not enabled. If the review surfaces
actionable feedback, address it and commit before proceeding.
For staged-only review: `wade review implementation --staged`.

**Step 2 — Write PR summary:**

Write `PR-SUMMARY.md` in the worktree root with a real description of your
changes (see the format above). If the file already exists, update it.

**Step 3 — Sync with main:**

```bash
wade implementation-session sync --json
```

**Step 4 — Done:**

```bash
wade implementation-session done
```

`wade implementation-session done` handles pushing the branch, updating the existing draft PR
(appending a summary and marking it ready). The worktree is **not** deleted —
it is cleaned up automatically by `implement` after the human merges the PR.

This is a **mandatory** final step. If `wade implementation-session done` fails, debug and
fix the error — do NOT bypass it.

**Step 5 — Review with the user:**

Present the PR link and a brief recap of what was implemented. Ask if they'd
like any further changes — apply them and repeat Steps 1–4 if so, or confirm
the session is complete if not.

### Working on a child issue (sub-issue of a tracking/epic)

If your issue appears in a parent "Tracking:" issue checklist:

- `wade implementation-session done` **automatically detects** the parent tracking issue and
  adds `Part of #<parent>` to the PR body alongside `Closes #<child>` — no
  manual action needed. Pass `--no-close` to leave the issue open.
- After your PR is merged, update the parent tracking issue's checklist:
  change `- [ ] #<your-issue>` to `- [x] #<your-issue>` using
  `gh issue edit <parent-number> --body "<updated-body>"`.
- If all children are complete, close the parent: `wade task close <parent-number>`.

### Working on the parent/epic tracking issue directly

If you are working on the tracking issue itself (not a specific child):
- The PR body should reference all child issues and their status.
- Use `Closes #<tracking-issue>` in the PR body.
- List child issue statuses using GitHub's tasklist syntax so GitHub renders
  progress automatically.

## After creating a new plan

If you finalize a plan or feature spec during a work session, you **must**
create a GitHub Issue from it:

1. Write the plan file to the worktree root (never into the repo's main checkout)
2. Create the issue via `wade task create` (interactive)
3. List the created issues and show `wade implement <number>` as a hint.
   Do **not** run the command yourself — the human starts work sessions.

## Skills reference

- **About to create GitHub Issues** → read @.claude/skills/task/SKILL.md first
