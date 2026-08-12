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

**Migration-owned collisions are auto-reconciled.** When the base branch has
started tracking wade-managed/regenerable files your worktree only had as
untracked copies — `.gitattributes`, `KNOWLEDGE.md`, `KNOWLEDGE.ratings.jsonl` —
**catchup** (startup only) discards those untracked copies, merges in the base's
now-tracked versions, and restores the `merge=union` block. It also clears the
`--skip-worktree` `.gitignore` `wade:worktree` block (and the `AGENTS.md` /
`CLAUDE.md` `wade:pointer` block, but **only** when that block is the *entire*
local difference) so the merge is no longer blocked, then re-injects them
afterward.
This is startup-only: the mid/end-session `sync` never blind-deletes an untracked
`KNOWLEDGE.md` (it may hold real, uncommitted knowledge you added this session).

**If catchup can't advance, you'll know in-session — loudly.** Whatever the
reason (untracked collision on a non-wade file, a real merge conflict, or an
`AGENTS.md` that carries a real edit alongside the pointer), catchup does **not**
proceed silently on a stale base. It prints a boxed error naming how many commits
behind you are, writes a `.wade/stale_base` marker, and re-injects a
`⚠️ BRANCH IS N COMMITS BEHIND` banner into your session context on every
startup/resume/compaction. **Do not start work until you sync.** The marker is
cleared automatically once a later `sync`/`catchup` reaches "up to date".

**Merge conflict reported at startup**: on a genuine *content* conflict catchup
aborts the merge and leaves the worktree clean — there are no markers to resolve.
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
  delete them, then re-run. Note: at **startup catchup**, a collision made up
  *only* of wade-managed/regenerable files (`.gitattributes`, `KNOWLEDGE.md`,
  `KNOWLEDGE.ratings.jsonl`) is reconciled automatically and does **not** surface
  here — this error fires only when a *non-wade* untracked path is involved (and
  in `sync`, where nothing is ever auto-deleted). When it fires and catchup could
  not advance, heed the `⚠️ BRANCH IS N COMMITS BEHIND` in-session banner and
  `sync` before starting work.
- **`stash_left_behind`**: the stash pop conflicted after a successful merge.
  Your changes are preserved. Recover with:

  ```bash
  git stash apply <stash-ref>   # ref shown in the error output
  ```

  If the pop left conflict markers in a file it applied, resolve them in place —
  the merge itself already succeeded, so there is nothing to `git commit --no-edit`.

## `GitError` on merge with a clean `git status`

The merge aborts with `Your local changes to the following files would be
overwritten by merge: AGENTS.md`, yet `git status` and `git diff` show nothing.
Bootstrap sets git's `--skip-worktree` bit on `AGENTS.md` so the injected
`## Git Workflow` pointer never reads as dirty — that also hides the file from
`status`/`diff` while git still refuses to merge over it. Confirm with
`git ls-files -v AGENTS.md` (a leading `S`), then:

```bash
git update-index --no-skip-worktree AGENTS.md
git diff AGENTS.md
```

**Stop there if that diff shows anything beyond the generated `wade:pointer`
block.** The next command discards it permanently, and the skip-worktree bit
means nobody will see what was lost. Real `AGENTS.md` edits need the
pointer-preserving workflow instead — see the `AGENTS.md` / `--skip-worktree`
entry in `KNOWLEDGE.md`.

Only once the pointer block is the *entire* diff:

```bash
git checkout -- AGENTS.md
```

Re-run sync, then put the pointer and the bit back:

```bash
uv run python -c "from pathlib import Path; \
  from wade.skills.pointer import ensure_pointer; ensure_pointer(Path('.'))"
git update-index --skip-worktree AGENTS.md
```

**Never restore `AGENTS.md` from a copy taken before the merge** — main's own
`AGENTS.md` changes arrive with that merge, and `--skip-worktree` would silently
mask reverting them.
