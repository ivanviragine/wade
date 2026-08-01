# Sync & Catchup Recovery

Read this only when a catchup or sync step reports a conflict or error. On the
happy path (exit 0) you never need it.

## Catchup: startup sync with base branch

`wade implement` auto-syncs your worktree branch with the base branch at startup
(before the session begins); the catchup output appears in the startup log.

**Auto-stash**: staged/unstaged user changes are stashed, merged, and restored
automatically. Session artifacts (PLAN.md, PR-SUMMARY.md, etc.) are never
stashed. Use `--no-stash` for the strict behavior (fail on any uncommitted
changes).

**Merge conflict reported at startup**: catchup always aborts the merge and
leaves the worktree clean — there are no markers to resolve.
`wade implementation-session catchup --json` is inspection-only: it re-runs the
same aborted catchup to report which files conflict, then aborts again.

To produce resolvable conflicts, run the merge manually:

```bash
git fetch origin
git merge origin/<main-branch>   # use the actual base branch name
```

Then resolve using the standard flow:

1. `git diff --name-only --diff-filter=U` to list conflicted files
2. Read each conflicted file — understand both sides
3. Resolve the conflict markers
4. Stage only the resolved files: `git add <file1> <file2> ...`
5. Complete the merge: `git commit --no-edit`

## Sync: exit codes

`wade implementation-session sync --json` auto-stashes tracked user changes,
merges, then restores them (disable with `--no-stash`).

- **Exit 0 — Success**: branch up to date with main. Proceed to closing.
- **Exit 2 — Conflict**: with auto-stash active the merge is aborted and your
  stash restored automatically — the worktree is clean. Resolve the conflict
  manually (see the manual-merge flow above), then re-run sync.
- **Exit 4 — Pre-flight failure**: report the issue (not in git repo, already on
  main, or `--no-stash` with a dirty worktree) and suggest how to fix it.

**Never re-implement git operations yourself.** Always use
`wade implementation-session sync`.

## Named errors (catchup or sync)

- **`untracked_conflict`**: untracked files in your worktree would be overwritten
  by the incoming merge. The paths are listed in the error. Commit, move, or
  delete them, then re-run.
- **`stash_left_behind`**: the stash pop conflicted after a successful merge.
  Your changes are preserved. Recover with:

  ```bash
  git stash apply <stash-ref>   # ref shown in the error output
  ```
