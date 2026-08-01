# Sync Recovery

Read this only when the closing sync reports a conflict or error. On the happy
path (exit 0) you never need it.

`wade review-pr-comments-session sync --json` exit codes:

- **Exit 0 — Success**: branch up to date with main. Proceed to closing.
- **Exit 2 — Conflict**: a merge conflict was detected.
  - If your local changes were stashed before the sync ran (an `autostashed`
    event/message appeared), the merge is aborted and your stash is restored
    automatically — there is nothing left to resolve in the worktree. Read the
    reported conflicting files to understand why, then re-run
    `wade review-pr-comments-session sync --json` once you've addressed the
    cause (e.g. rebasing or adjusting your changes).
  - Otherwise (worktree was already clean, e.g. `--no-stash`), the merge is left
    paused for manual resolution:
    1. `git diff --name-only --diff-filter=U` to list conflicted files
    2. Read each conflicted file — understand both sides
    3. Resolve the conflict markers
    4. Stage only the resolved files: `git add <file1> <file2> ...`
    5. Complete the merge: `git commit --no-edit`
    6. Re-run `wade review-pr-comments-session sync --json` to verify clean
- **Exit 4 — Pre-flight failure**: report the issue (not in git repo, already on
  main, or a dirty worktree) and suggest how to fix it.

**Never re-implement git operations yourself.** Always use
`wade review-pr-comments-session sync`.
