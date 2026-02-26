---
name: work-session
description: >
  Rules for AI implementation sessions in a ghaiw-managed worktree. Covers
  worktree safety, commit conventions, syncing with main, PR summaries, and
  session closing. Read this at the start of every work session.
---

# Work Session Rules

These rules govern AI implementation sessions in a ghaiw-managed project.
Read and follow them before performing any other action.

## Execution mode

Run `ghaiwpy` and `gh` commands with the required permissions/capabilities (not
in sandboxed mode). Do not "try sandbox first" — run them unsandboxed from the
start.

## Never use `gh issue create`

**NEVER** use `gh issue create` or the GitHub API to create issues directly.
Always use `ghaiwpy task create` (or `ghaiwpy task create --plan-file`).
Using `gh` directly bypasses label enforcement, snapshot/diff detection, and
dependency analysis hooks.

## First action: check your context

Run `ghaiwpy check` as your **first action**:

- `IN_WORKTREE` — you may proceed with work (code changes, commits, etc.)
- `IN_MAIN_CHECKOUT` — **editing any source file is forbidden, even before
  committing**. Tell the human to create a worktree first via
  `ghaiwpy work start`.
- `NOT_IN_GIT_REPO` — you are not inside a git repository.

## Worktree safety

All **code changes** (edits, new files, commits) **must** happen in a worktree.
The human creates worktrees via `ghaiwpy work start` (single issue) or
`ghaiwpy work batch` (multiple issues in parallel). **Never** create worktrees
yourself.

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/) format.
Prefer `git commit` (which opens an editor) over `git commit -m` for
multi-line messages.

## PR summary

**Write this file immediately — before you write any code.**

```bash
cat > /tmp/PR-SUMMARY-{issue-number}.md << 'EOF'
# PR Summary
(in progress)
EOF
```

`ghaiwpy work done` reads **`/tmp/PR-SUMMARY-{issue-number}.md`** to populate
the PR body. If the file is missing, the PR will have no description.
Creating it early as a placeholder guarantees it exists; update it as you work.

> **Never commit this file** — it is a session artifact.

### What to include

1. **What was accomplished** — high-level summary of changes
2. **Why these changes** — context from the issue/plan
3. **Key technical decisions** — important implementation choices
4. **What was tested** — how you verified the changes work
5. **Screenshots** — visual evidence for UI/UX changes (if applicable)

### Final form

```markdown
# PR Summary

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
ghaiwpy work sync --json
```

### Step 3: Handle the result

**Exit code 0 — Success**: Branch is up to date with main. Proceed to
`ghaiwpy work done`.

**Exit code 2 — Conflict**: The merge is paused due to conflicts:
1. Read each conflicted file to see the conflict markers
2. Explain what changed on main vs the feature branch
3. Suggest a resolution and ask for confirmation
4. Apply fixes: `git add -A && git commit --no-edit`
5. Re-run `ghaiwpy work sync --json` to verify clean

**Exit code 4 — Pre-flight failure**: Report the issue (dirty worktree, not
in repo, already on main) and suggest how to fix it.

**Never re-implement git operations yourself.** Always use `ghaiwpy work sync`.

## Closing the session

**NEVER** create Pull Requests manually (`gh pr create`) or push branches
directly.

To finalize your work, follow these steps in order:

**Step 1 — Verify PR summary exists and is complete:**

```bash
cat /tmp/PR-SUMMARY-{issue-number}.md
```

The file must exist and contain a real description (not just the placeholder).
If it is missing or still says "(in progress)", write it now before continuing.

**Step 2 — Sync with main:**

```bash
ghaiwpy work sync --json
```

**Step 3 — Done:**

```bash
ghaiwpy work done
```

`ghaiwpy work done` handles pushing the branch, creating the PR (or merging),
and triggering auto-versioning (if enabled). The worktree is **not** deleted —
it is cleaned up automatically by `work start` after the human merges the PR.

This is a **mandatory** final step. If `ghaiwpy work done` fails, debug and
fix the error — do NOT bypass it.

### Working on a child issue (sub-issue of a tracking/epic)

If your issue appears in a parent "Tracking:" issue checklist:

- `ghaiwpy work done` **automatically detects** the parent tracking issue and
  adds `Part of #<parent>` to the PR body alongside `Closes #<child>` — no
  manual action needed. Pass `--no-close` to leave the issue open.
- After your PR is merged, update the parent tracking issue's checklist:
  change `- [ ] #<your-issue>` to `- [x] #<your-issue>` using
  `gh issue edit <parent-number> --body "<updated-body>"`.
- If all children are complete, close the parent: `ghaiwpy task close <parent-number>`.

## After creating a new plan

If you finalize a plan or feature spec during a work session, you **must**
create a GitHub Issue from it:

1. Write the plan file (to `/tmp/`, never into the repo)
2. Create the issue via `ghaiwpy task create --plan-file <path>` or
   `ghaiwpy task create` (interactive)
3. List the created issues and show `ghaiwpy work start <number>` as a hint.
   Do **not** run the command yourself — the human starts work sessions.

## Skills reference

- **About to create GitHub Issues** → read @.claude/skills/task/SKILL.md first
- **About to analyze issue dependencies** → read @.claude/skills/deps/SKILL.md first
