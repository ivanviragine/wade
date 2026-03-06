---
name: work-session
description: >
  Rules for AI implementation sessions in a wade-managed worktree. Covers
  worktree safety, commit conventions, syncing with main, PR summaries, and
  session closing. Read this at the start of every work session.
---

# Work Session Rules

These rules govern AI implementation sessions in a wade-managed project.
Read and follow them before performing any other action.

## Execution mode

Run `wade` and `gh` commands with the required permissions/capabilities (not
in sandboxed mode). Do not "try sandbox first" — run them unsandboxed from the
start.

## Never use `gh issue create`

**NEVER** use `gh issue create` or the GitHub API to create issues directly.
Always use `wade new-task` for interactive issue creation.
Using `gh` directly bypasses label enforcement, snapshot/diff detection, and
dependency analysis hooks.

## First action: check your context

Run `wade check` as your **first action**:

- `IN_WORKTREE` — you may proceed with work (code changes, commits, etc.)
- `IN_MAIN_CHECKOUT` — **editing any source file is forbidden, even before
  committing**. Tell the human to create a worktree first via
  `wade implement-task`.
- `NOT_IN_GIT_REPO` — you are not inside a git repository.

## Worktree safety

All **code changes** (edits, new files, commits) **must** happen in a worktree.
The human creates worktrees via `wade implement-task` (single issue) or
`wade work batch` (multiple issues in parallel). **Never** create worktrees
yourself.

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/) format.
Prefer `git commit` (which opens an editor) over `git commit -m` for
multi-line messages.

## PR summary

Before closing the session, write **`PR-SUMMARY.md`** in the worktree root
(your current working directory). `wade work done` reads this file to populate
the PR body. If the file is missing, the PR will have no description.

> **Never commit this file** — it is a session artifact (already in `.gitignore`).
> If you find it is already tracked by git (e.g. `git status` shows it as modified),
> untrack it first:
> ```bash
> git rm --cached PR-SUMMARY.md
> git commit -m "chore: untrack PR-SUMMARY.md (already gitignored)"
> ```
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

If there is output, stage and commit:
```bash
git add -A
git commit -m "<type>: <summary>"
```

### Step 2: Run the sync command

```bash
wade work sync --json
```

### Step 3: Handle the result

**Exit code 0 — Success**: Branch is up to date with main. Proceed to
`wade work done`.

**Exit code 2 — Conflict**: The merge is paused due to conflicts:
1. Read each conflicted file to see the conflict markers
2. Explain what changed on main vs the feature branch
3. Suggest a resolution and ask for confirmation
4. Apply fixes: `git add -A && git commit --no-edit`
5. Re-run `wade work sync --json` to verify clean

**Exit code 4 — Pre-flight failure**: Report the issue (dirty worktree, not
in repo, already on main) and suggest how to fix it.

**Never re-implement git operations yourself.** Always use `wade work sync`.

## Closing the session

**NEVER** create Pull Requests manually (`gh pr create`) or push branches
directly.

To finalize your work, follow these steps in order:

**Step 1 — Write PR summary:**

Write `PR-SUMMARY.md` in the worktree root with a real description of your
changes (see the format above). If the file already exists, update it.

**Step 2 — Sync with main:**

```bash
wade work sync --json
```

**Step 3 — Done:**

```bash
wade work done
```

`wade work done` handles pushing the branch, updating the existing draft PR
(appending a summary and marking it ready). The worktree is **not** deleted —
it is cleaned up automatically by `implement-task` after the human merges the PR.

This is a **mandatory** final step. If `wade work done` fails, debug and
fix the error — do NOT bypass it.

**Step 4 — Review with the user:**

Present the PR link and a brief recap of what was implemented. Ask if they'd
like any further changes — apply them and repeat Steps 1–3 if so, or confirm
the session is complete if not.

### Working on a child issue (sub-issue of a tracking/epic)

If your issue appears in a parent "Tracking:" issue checklist:

- `wade work done` **automatically detects** the parent tracking issue and
  adds `Part of #<parent>` to the PR body alongside `Closes #<child>` — no
  manual action needed. Pass `--no-close` to leave the issue open.
- After your PR is merged, update the parent tracking issue's checklist:
  change `- [ ] #<your-issue>` to `- [x] #<your-issue>` using
  `gh issue edit <parent-number> --body "<updated-body>"`.
- If all children are complete, close the parent: `wade task close <parent-number>`.

## After creating a new plan

If you finalize a plan or feature spec during a work session, you **must**
create a GitHub Issue from it:

1. Write the plan file to the worktree root (never into the repo's main checkout)
2. Create the issue via `wade new-task` (interactive)
3. List the created issues and show `wade implement-task <number>` as a hint.
   Do **not** run the command yourself — the human starts work sessions.

## Skills reference

- **About to create GitHub Issues** → read @.claude/skills/task/SKILL.md first
- **About to analyze issue dependencies** → read @.claude/skills/deps/SKILL.md first
