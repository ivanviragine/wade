---
name: sync
description: >
  Prepare a feature branch for merging into main. Fetches latest main and
  merges it into the current branch, resolving any conflicts with AI
  assistance. Use when the user says "prepare for merge", "sync with main",
  "update branch", or is about to close a worktree.
---

# Sync

Sync the current feature branch with main so it can be merged cleanly.

## How this works

The deterministic git operations (fetch + merge) are handled by the `ghaiw`
CLI. Your job is to run it and **only** engage your reasoning if merge
conflicts arise.

## Step 1: Commit uncommitted work

Before merging, ensure the working tree is clean. Check for uncommitted
changes:

```bash
git status --porcelain
```

If there is any output (staged, unstaged, or untracked files):

1. Stage all changes: `git add -A`
2. Commit with a descriptive message using [Conventional Commits](https://www.conventionalcommits.org/) format:
   - **Agents:** `git commit -m "<type>: <summary>"` (single-line is fine)
   - **Humans:** prefer `git commit` (opens editor) for multi-line messages

If the tree is already clean, skip to Step 2.

## Step 2: Run the merge command

```bash
ghaiw work sync --json
```

Optional flags:
- `--main-branch NAME` — non-standard main branch
- `--dry-run` — preview what would happen

## Step 3: Handle the result

### Exit code 0 — Success
Report concisely: branch is up to date with main, ready to merge.
Next step: run `ghaiw work done` to create a PR or merge.

### Exit code 2 — Conflict merging main → feature branch
The command paused due to conflicts:

1. Read the conflict diff from command output
2. Read each conflicted file to see the conflict markers
3. Explain what changed on main vs the feature branch
4. Suggest a resolution and ask for confirmation
5. If confirmed: apply fixes, run `git add -A && git commit --no-edit`
6. Re-run the command to verify the branch is now clean

### Exit code 4 — Pre-flight failure
Report the issue (dirty worktree, not in repo, already on main, etc.)
and suggest how to fix it.

## Rules

- **Never re-implement the git operations yourself.** Always use `ghaiw work sync`.
- When analyzing conflicts, read actual file contents — don't guess from diff.
- Be concise on success, thorough on conflicts.

## Resources

- For complete API details, see [reference.md](reference.md)
- For usage examples, see [examples.md](examples.md)
